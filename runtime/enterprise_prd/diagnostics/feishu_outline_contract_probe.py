from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROBE_VERSION = "0.1.0"
SOURCE_ID = "slm-followup-records"
LOCATOR_REF = "local-secret:slm-followup-records"
LIVE_EVIDENCE_NAME = "FEISHU_OUTLINE_RESPONSE_CONTRACT_DISCOVERY_0_1_0_LIVE_RESULT.json"


class ProbeContractError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProbeExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProbePaths:
    repo_root: Path
    private_root: Path
    locator: Path
    discovery_root: Path
    attempt: Path
    report: Path
    evidence: Path

    @classmethod
    def for_repo(cls, repo_root: Path) -> "ProbePaths":
        repo = Path(repo_root)
        private = repo / "pilot/private/local-context"
        discovery = private / "discovery"
        return cls(repo, private, private / "locator-secrets.local.json", discovery,
                   discovery / "feishu-outline-probe-attempt.local.json",
                   discovery / "feishu-outline-shape-report.local.json",
                   repo / "docs/evidence" / LIVE_EVIDENCE_NAME)


LIVE_PATHS = ProbePaths.for_repo(Path(__file__).resolve().parents[2])
_LOCATOR_ROOT_FIELDS = {"schema_version", "entries"}
_LOCATOR_ENTRY_FIELDS = {"locator_ref", "document_id"}
_ATTEMPT_PAYLOAD = {"schema_version": 1, "probe_version": PROBE_VERSION,
                    "source_id": SOURCE_ID, "external_call_may_have_occurred": True}


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _json_loads(value: str):
    def reject_constant(_: str):
        raise ValueError("non-finite JSON number")
    return json.loads(value, parse_constant=reject_constant)


def _assert_under(path: Path, root: Path, *, must_exist: bool) -> None:
    try:
        root_resolved = root.resolve(strict=True)
        if root.is_symlink():
            raise ProbeContractError("private_root_symlink")
        relative = path.relative_to(root)
        if ".." in relative.parts:
            raise ProbeContractError("private_path_escape")
        cursor = root
        for part in relative.parts[:-1]:
            cursor /= part
            if cursor.exists() and cursor.is_symlink():
                raise ProbeContractError("private_path_symlink")
        resolved = path.resolve(strict=must_exist)
        if not resolved.is_relative_to(root_resolved):
            raise ProbeContractError("private_path_escape")
        if path.exists() and path.is_symlink():
            raise ProbeContractError("private_path_symlink")
    except (OSError, ValueError):
        raise ProbeContractError("private_path_invalid") from None


def prepare_paths(paths: ProbePaths) -> None:
    if paths.private_root.is_symlink() or not paths.private_root.is_dir():
        raise ProbeContractError("private_root_invalid")
    _assert_under(paths.locator, paths.private_root, must_exist=True)
    if not paths.locator.is_file():
        raise ProbeContractError("locator_unavailable")
    _assert_under(paths.discovery_root, paths.private_root, must_exist=False)
    try:
        paths.discovery_root.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError:
        raise ProbeContractError("discovery_root_unavailable") from None
    _assert_under(paths.discovery_root, paths.private_root, must_exist=True)
    for target in (paths.attempt, paths.report):
        _assert_under(target, paths.private_root, must_exist=False)
    expected = paths.repo_root / "docs/evidence" / LIVE_EVIDENCE_NAME
    if paths.evidence != expected or paths.evidence.parent.is_symlink() or not paths.evidence.parent.is_dir():
        raise ProbeContractError("evidence_path_invalid")
    if any(target.exists() or target.is_symlink() for target in (paths.attempt, paths.report, paths.evidence)):
        raise ProbeContractError("probe_already_attempted")


def load_token(paths: ProbePaths) -> str:
    try:
        raw = _json_loads(paths.locator.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ProbeContractError("locator_unavailable") from None
    if type(raw) is not dict or set(raw) != _LOCATOR_ROOT_FIELDS or raw["schema_version"] != 1 or type(raw["entries"]) is not list:
        raise ProbeContractError("locator_contract_invalid")
    matches = []
    for item in raw["entries"]:
        if type(item) is not dict or set(item) != _LOCATOR_ENTRY_FIELDS:
            raise ProbeContractError("locator_contract_invalid")
        if item["locator_ref"] == LOCATOR_REF:
            matches.append(item["document_id"])
    if len(matches) != 1:
        raise ProbeContractError("locator_identity_invalid")
    token = matches[0]
    if type(token) is not str or not token or any(char.isspace() for char in token):
        raise ProbeContractError("locator_value_invalid")
    return token


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("file write made no progress")
        remaining = remaining[written:]


def create_attempt_marker(paths: ProbePaths) -> None:
    descriptor = -1
    try:
        descriptor = os.open(paths.attempt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ProbeContractError("probe_already_attempted") from None
    except OSError:
        raise ProbeContractError("attempt_marker_failed") from None
    try:
        _write_all(descriptor, canonical_json_bytes(_ATTEMPT_PAYLOAD))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(paths.discovery_root)
    except OSError:
        raise ProbeContractError("attempt_marker_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


_JSON_TYPES = {"missing", "null", "boolean", "integer", "number", "string", "array", "object"}
_PARSE_STATUSES = {"not_string", "empty", "special_markup_rejected", "malformed", "parsed"}
_MISMATCH_ORDER = ("response_root_not_object", "data_missing_or_wrong_type", "document_missing_or_wrong_type", "document_id_missing", "document_id_wrong_type", "document_id_empty", "document_id_mismatch", "title_missing", "title_wrong_type", "title_empty", "revision_id_missing", "revision_id_wrong_type", "revision_id_empty", "revision_id_invalid_value", "content_missing", "content_wrong_type", "content_empty", "xml_special_markup", "xml_malformed", "xml_root_shape_mismatch", "xml_wrapper_shape_mismatch", "xml_heading_shape_mismatch", "none")
_TITLE_ONLY_MISMATCHES = {"title_missing", "title_wrong_type", "title_empty"}
_DOCUMENT_FIELDS = {"document_id", "title", "revision_id", "content"}
_XML_FIELDS = ("root_is_fragment", "root_known_attribute_count", "root_unknown_attribute_count", "root_mode_is_outline", "root_has_nonwhitespace_text", "root_has_nonwhitespace_tail", "root_direct_child_count", "root_outline_child_count", "root_unknown_child_count", "outline_known_attribute_count", "outline_unknown_attribute_count", "outline_has_nonwhitespace_text", "outline_has_nonwhitespace_tail", "outline_direct_child_count", "h1_count", "h2_count", "h3_count", "h4_count", "h5_count", "h6_count", "outline_unknown_child_count", "heading_missing_id_count", "heading_extra_attribute_count", "heading_with_child_count", "heading_empty_text_count", "heading_nonwhitespace_tail_count", "duplicate_heading_id_count", "duplicate_normalized_path_count")


def json_type(value: object, *, present: bool = True) -> str:
    if not present: return "missing"
    if value is None: return "null"
    if type(value) is bool: return "boolean"
    if type(value) is int: return "integer"
    if type(value) is float: return "number"
    if type(value) is str: return "string"
    if type(value) is list: return "array"
    if type(value) is dict: return "object"
    raise ProbeContractError("non_json_value")


def _field_shape(document: dict | None, name: str, token: str) -> dict:
    present = type(document) is dict and name in document
    value = document[name] if present else None
    result = {"present": present, "type": json_type(value, present=present), "nonempty": bool(value.strip()) if type(value) is str else None}
    if name == "document_id": result["matches_requested_document"] = value == token if type(value) is str else None
    return result


def _empty_xml_shape(status: str) -> dict:
    return {"parse_status": status, **{field: None for field in _XML_FIELDS}}


def _has_text(value: str | None) -> bool: return value is not None and bool(value.strip())
def _normalize_title(value: str) -> str: return " ".join(unicodedata.normalize("NFKC", value).strip().split())
def _duplicates(values: list[str]) -> int: return len(values) - len(set(values))


def _xml_shape(content: object) -> dict:
    if type(content) is not str: return _empty_xml_shape("not_string")
    if not content.strip(): return _empty_xml_shape("empty")
    if "<!" in content or "<?" in content: return _empty_xml_shape("special_markup_rejected")
    try: root = ET.fromstring(content)
    except ET.ParseError: return _empty_xml_shape("malformed")
    children = list(root)
    shape = {"parse_status": "parsed", "root_is_fragment": root.tag == "fragment", "root_known_attribute_count": int("mode" in root.attrib), "root_unknown_attribute_count": len(set(root.attrib) - {"mode"}), "root_mode_is_outline": root.attrib.get("mode") == "outline", "root_has_nonwhitespace_text": _has_text(root.text), "root_has_nonwhitespace_tail": _has_text(root.tail), "root_direct_child_count": len(children), "root_outline_child_count": sum(child.tag == "outline" for child in children), "root_unknown_child_count": sum(child.tag != "outline" for child in children), **{field: None for field in _XML_FIELDS[9:]}}
    if len(children) != 1 or children[0].tag != "outline": return shape
    wrapper, headings = children[0], list(children[0])
    levels = {f"h{level}" for level in range(1, 7)}
    shape.update({"outline_known_attribute_count": 0, "outline_unknown_attribute_count": len(wrapper.attrib), "outline_has_nonwhitespace_text": _has_text(wrapper.text), "outline_has_nonwhitespace_tail": _has_text(wrapper.tail), "outline_direct_child_count": len(headings), **{f"h{level}_count": sum(item.tag == f"h{level}" for item in headings) for level in range(1, 7)}, "outline_unknown_child_count": sum(item.tag not in levels for item in headings), "heading_missing_id_count": 0, "heading_extra_attribute_count": 0, "heading_with_child_count": 0, "heading_empty_text_count": 0, "heading_nonwhitespace_tail_count": 0, "duplicate_heading_id_count": 0, "duplicate_normalized_path_count": 0})
    ids, paths, stack = [], [], []
    for item in headings:
        if item.tag not in levels: continue
        block_id, title = item.attrib.get("id"), _normalize_title(item.text) if type(item.text) is str else ""
        missing_id, has_child = type(block_id) is not str or not block_id.strip(), bool(len(item))
        shape["heading_missing_id_count"] += int(missing_id); shape["heading_extra_attribute_count"] += len(set(item.attrib) - {"id"}); shape["heading_with_child_count"] += int(has_child); shape["heading_empty_text_count"] += int(not title); shape["heading_nonwhitespace_tail_count"] += int(_has_text(item.tail))
        if missing_id or has_child or not title: continue
        ids.append(block_id.strip()); level = int(item.tag[1])
        while stack and stack[-1][0] >= level: stack.pop()
        paths.append("/".join([part for _, part in stack] + [title])); stack.append((level, title))
    shape["duplicate_heading_id_count"] = _duplicates(ids); shape["duplicate_normalized_path_count"] = _duplicates(paths)
    return shape


def _xml_mismatch(shape: dict) -> str | None:
    if shape["parse_status"] == "special_markup_rejected": return "xml_special_markup"
    if shape["parse_status"] == "malformed": return "xml_malformed"
    if shape["parse_status"] != "parsed": return None
    root = all((shape["root_is_fragment"], shape["root_known_attribute_count"] == 1, shape["root_unknown_attribute_count"] == 0, shape["root_mode_is_outline"], not shape["root_has_nonwhitespace_text"], not shape["root_has_nonwhitespace_tail"], shape["root_direct_child_count"] == 1, shape["root_outline_child_count"] == 1, shape["root_unknown_child_count"] == 0))
    if not root: return "xml_root_shape_mismatch"
    wrapper = all((shape["outline_unknown_attribute_count"] == 0, not shape["outline_has_nonwhitespace_text"], not shape["outline_has_nonwhitespace_tail"], shape["outline_direct_child_count"] > 0))
    if not wrapper: return "xml_wrapper_shape_mismatch"
    if any(shape[name] for name in ("outline_unknown_child_count", "heading_missing_id_count", "heading_extra_attribute_count", "heading_with_child_count", "heading_empty_text_count", "heading_nonwhitespace_tail_count", "duplicate_heading_id_count", "duplicate_normalized_path_count")): return "xml_heading_shape_mismatch"
    return None


def _document_mismatches(document: dict, token: str, xml: dict) -> list[str]:
    mismatches = []
    for field in ("document_id", "title", "revision_id", "content"):
        if field not in document: mismatches.append(f"{field}_missing"); continue
        value = document[field]
        if field == "revision_id":
            if type(value) is str and not value.strip(): mismatches.append("revision_id_empty")
            elif type(value) is int and value < 0: mismatches.append("revision_id_invalid_value")
            elif not ((type(value) is int and value >= 0) or (type(value) is str and value.strip())): mismatches.append("revision_id_wrong_type")
        elif type(value) is not str: mismatches.append(f"{field}_wrong_type")
        elif not value.strip(): mismatches.append(f"{field}_empty")
        elif field == "document_id" and value != token: mismatches.append("document_id_mismatch")
    if type(document.get("content")) is str and document["content"].strip():
        mismatch = _xml_mismatch(xml)
        if mismatch: mismatches.append(mismatch)
    return [item for item in _MISMATCH_ORDER if item in set(mismatches)]


def build_shape_report(payload: object, requested_token: str) -> dict:
    root = type(payload) is dict; data_present = root and "data" in payload; data = payload.get("data") if root else None; data_object = type(data) is dict; document_present = data_object and "document" in data; document = data.get("document") if data_object else None; document_object = type(document) is dict
    response = {"root_type": json_type(payload), "data_present": data_present, "data_type": json_type(data, present=data_present), "document_present": document_present, "document_type": json_type(document, present=document_present), "unknown_root_field_count": len(set(payload) - {"data"}) if root else 0, "unknown_data_field_count": len(set(data) - {"document"}) if data_object else 0}
    doc_shape = {field: _field_shape(document if document_object else None, field, requested_token) for field in ("document_id", "title", "revision_id", "content")}; doc_shape["unknown_document_field_count"] = len(set(document) - _DOCUMENT_FIELDS) if document_object else 0
    xml = _xml_shape(document.get("content") if document_object else None)
    mismatches = ["response_root_not_object"] if not root else ["data_missing_or_wrong_type"] if not data_object else ["document_missing_or_wrong_type"] if not document_object else _document_mismatches(document, requested_token, xml)
    if not mismatches: mismatches = ["none"]
    return {"schema_version": 1, "probe_version": PROBE_VERSION, "source_id": SOURCE_ID, "command_kind": "outline", "response_shape": response, "document_shape": doc_shape, "xml_shape": xml, "mismatches": mismatches, "primary_mismatch": mismatches[0]}


def decision_for(report: dict) -> str:
    mismatches = set(report["mismatches"])
    return "new_adapter_spec_eligible" if mismatches == {"none"} or mismatches <= _TITLE_ONLY_MISMATCHES else "pivot_to_local_import"


_REPORT_ROOT_FIELDS = {"schema_version", "probe_version", "source_id", "command_kind", "response_shape", "document_shape", "xml_shape", "mismatches", "primary_mismatch"}
_RESPONSE_FIELDS = {"root_type", "data_present", "data_type", "document_present", "document_type", "unknown_root_field_count", "unknown_data_field_count"}
_FIELD_SHAPE_FIELDS = {"present", "type", "nonempty"}


def validate_report(report: dict) -> None:
    if type(report) is not dict or set(report) != _REPORT_ROOT_FIELDS: raise ProbeContractError("report_schema_invalid")
    if report["schema_version"] != 1 or report["probe_version"] != PROBE_VERSION or report["source_id"] != SOURCE_ID or report["command_kind"] != "outline": raise ProbeContractError("report_schema_invalid")
    response = report["response_shape"]
    if type(response) is not dict or set(response) != _RESPONSE_FIELDS: raise ProbeContractError("report_schema_invalid")
    if response["root_type"] not in _JSON_TYPES or response["data_type"] not in _JSON_TYPES or response["document_type"] not in _JSON_TYPES: raise ProbeContractError("report_type_invalid")
    if any(type(response[field]) is not bool for field in ("data_present", "document_present")) or any(type(response[field]) is not int or response[field] < 0 for field in ("unknown_root_field_count", "unknown_data_field_count")): raise ProbeContractError("report_type_invalid")
    document = report["document_shape"]
    if type(document) is not dict or set(document) != _DOCUMENT_FIELDS | {"unknown_document_field_count"}: raise ProbeContractError("report_schema_invalid")
    for field in _DOCUMENT_FIELDS:
        shape = document[field]; expected = _FIELD_SHAPE_FIELDS | ({"matches_requested_document"} if field == "document_id" else set())
        if type(shape) is not dict or set(shape) != expected or shape["type"] not in _JSON_TYPES or type(shape["present"]) is not bool or shape["nonempty"] not in (True, False, None): raise ProbeContractError("report_type_invalid")
        if field == "document_id" and shape["matches_requested_document"] not in (True, False, None): raise ProbeContractError("report_type_invalid")
    xml = report["xml_shape"]
    if type(xml) is not dict or set(xml) != {"parse_status", *_XML_FIELDS} or xml["parse_status"] not in _PARSE_STATUSES: raise ProbeContractError("report_schema_invalid")
    for field in _XML_FIELDS:
        value = xml[field]
        if field.startswith(("root_is_", "root_has_", "outline_has_")) or field == "root_mode_is_outline":
            if value not in (True, False, None): raise ProbeContractError("report_type_invalid")
        elif value is not None and (type(value) is not int or value < 0): raise ProbeContractError("report_type_invalid")
    mismatches = report["mismatches"]
    if type(mismatches) is not list or not mismatches or len(mismatches) != len(set(mismatches)) or any(value not in _MISMATCH_ORDER for value in mismatches) or mismatches != [value for value in _MISMATCH_ORDER if value in set(mismatches)] or report["primary_mismatch"] != mismatches[0] or ("none" in mismatches and mismatches != ["none"]): raise ProbeContractError("report_mismatch_invalid")


_KNOWN_RESPONSE_KEYS = {"data", "document", "document_id", "title", "revision_id", "content"}
def _walk_sensitive_strings(value: object):
    if type(value) is dict:
        for key, child in value.items():
            if str(key) not in _KNOWN_RESPONSE_KEYS: yield str(key)
            yield from _walk_sensitive_strings(child)
    elif type(value) is list:
        for child in value: yield from _walk_sensitive_strings(child)
    elif type(value) is str: yield value


def assert_redacted(report: dict, raw_payload: object, token: str) -> None:
    validate_report(report); rendered = canonical_json_bytes(report).decode("utf-8")
    forbidden = {token, hashlib.sha256(canonical_json_bytes(raw_payload)).hexdigest()}
    for value in _walk_sensitive_strings(raw_payload):
        if len(value) >= 8: forbidden |= {value, value[:8], hashlib.sha256(value.encode("utf-8")).hexdigest()}
    if any(value and value in rendered for value in forbidden) or any(marker in rendered for marker in ("http://", "https://", "/Users/", "local-secret:")): raise ProbeContractError("report_contains_response_value")


def atomic_write_json(path: Path, payload: dict, *, mode: int, no_replace: bool = True) -> None:
    if no_replace and (path.exists() or path.is_symlink()): raise ProbeContractError("output_already_exists")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent); temporary = Path(name)
    try:
        os.fchmod(descriptor, mode); _write_all(descriptor, canonical_json_bytes(payload)); os.fsync(descriptor); os.close(descriptor); descriptor = -1
        if no_replace and (path.exists() or path.is_symlink()): raise ProbeContractError("output_already_exists")
        os.replace(temporary, path); _fsync_directory(path.parent)
    finally:
        if descriptor >= 0: os.close(descriptor)
        if temporary.exists(): temporary.unlink()


_FIXED_ARGUMENTS_BEFORE_TOKEN = ("lark-cli", "docs", "+fetch", "--doc")
_FIXED_ARGUMENTS_AFTER_TOKEN = ("--doc-format", "xml", "--detail", "with-ids", "--scope", "outline", "--max-depth", "6", "--as", "user", "--format", "json")
class FixedOutlineRunner:
    def __init__(self, *, run: Callable = subprocess.run) -> None: self._run = run
    def fetch(self, token: str):
        argv = [*_FIXED_ARGUMENTS_BEFORE_TOKEN, token, *_FIXED_ARGUMENTS_AFTER_TOKEN]
        try: result = self._run(argv, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired): raise ProbeExecutionError("outline_fetch_failed") from None
        if result.returncode != 0: raise ProbeExecutionError("outline_fetch_failed")
        try: return _json_loads(result.stdout)
        except (json.JSONDecodeError, ValueError, UnicodeError): raise ProbeExecutionError("response_not_json") from None


_LIVE_EVIDENCE_FIELDS = {"schema_version", "probe_version", "source_id", "private_report_sha256", "primary_mismatch", "mismatch_count", "single_fixed_fetch", "retry_count", "raw_response_persisted", "body_read", "inspect_or_metadata_called", "search_or_folder_traversal", "other_source_accessed", "feishu_write", "decision"}
def _live_evidence(report: dict | None, report_hash: str | None, decision: str) -> dict:
    primary = "probe_execution_failed" if report is None else report["primary_mismatch"]
    count = 0 if report is None or report["mismatches"] == ["none"] else len(report["mismatches"])
    payload = {"schema_version": 1, "probe_version": PROBE_VERSION, "source_id": SOURCE_ID, "private_report_sha256": report_hash, "primary_mismatch": primary, "mismatch_count": count, "single_fixed_fetch": True, "retry_count": 0, "raw_response_persisted": False, "body_read": False, "inspect_or_metadata_called": False, "search_or_folder_traversal": False, "other_source_accessed": False, "feishu_write": False, "decision": decision}
    if set(payload) != _LIVE_EVIDENCE_FIELDS: raise ProbeContractError("live_evidence_schema_invalid")
    return payload


def _write_failure_evidence(paths: ProbePaths) -> dict:
    evidence = _live_evidence(None, None, "probe_failed_without_report"); atomic_write_json(paths.evidence, evidence, mode=0o644); return evidence


def run_live_probe(paths: ProbePaths = LIVE_PATHS, runner: FixedOutlineRunner | None = None) -> dict:
    prepare_paths(paths); token = load_token(paths); create_attempt_marker(paths)
    try:
        payload = (runner or FixedOutlineRunner()).fetch(token); report = build_shape_report(payload, token); validate_report(report); assert_redacted(report, payload, token)
        atomic_write_json(paths.report, report, mode=0o600); report_hash = hashlib.sha256(paths.report.read_bytes()).hexdigest(); evidence = _live_evidence(report, report_hash, decision_for(report)); atomic_write_json(paths.evidence, evidence, mode=0o644); return evidence
    except ProbeExecutionError: return _write_failure_evidence(paths)
    except Exception:
        if not paths.evidence.exists():
            try: return _write_failure_evidence(paths)
            except Exception: pass
        raise ProbeExecutionError("probe_failed_after_attempt") from None


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments: print(json.dumps({"status": "blocked", "code": "arguments_not_allowed"}, sort_keys=True)); return 2
    try: evidence = run_live_probe()
    except (ProbeContractError, ProbeExecutionError) as exc: print(json.dumps({"status": "blocked", "code": exc.code}, sort_keys=True)); return 1
    print(json.dumps({"status": "complete", "decision": evidence["decision"]}, sort_keys=True)); return 1 if evidence["decision"] == "probe_failed_without_report" else 0


if __name__ == "__main__": raise SystemExit(main())
