from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

from .errors import ContractError
from .version import SCHEMA_VERSION


ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MODES = frozenset({"existing-system-increment"})
DELIVERY_LEVELS = frozenset({"light", "standard"})
COVERAGE_LEVELS = frozenset({"light", "pilot-slice", "full"})
SOURCE_ROLES = (
    "current-product-requirement",
    "confirmed-product-decision",
    "observed-runtime",
    "current-code",
    "same-version-screenshot",
    "current-prototype-baseline",
)
GATE_STATES = frozenset({"pass", "block", "not_required"})
RUN_STATES = frozenset({"ready", "degraded", "blocked", "restart_required"})
REVIEW_VERDICTS = frozenset({"pass", "block"})
FINDING_SEVERITIES = frozenset({"blocker", "major", "minor", "note"})

ENTRY_KEYS = frozenset({
    "schema_version", "prototype_run_id", "prd_run_id", "review_context_id",
    "review_artifact_path", "review_artifact_sha256", "prd_artifact_path",
    "prd_artifact_sha256", "review_evidence", "spec_readiness",
    "prototype_readiness", "readiness_assessment_path", "readiness_assessment_sha256",
    "stage_authorized_by", "stage_authorized_at", "requested_mode",
    "requested_delivery", "requested_coverage",
})
SOURCE_SET_KEYS = frozenset({
    "schema_version", "prototype_run_id", "source_set_id", "sources", "selected_at",
})
SNAPSHOT_KEYS = frozenset({
    "schema_version", "prototype_run_id", "skill_name", "skill_version",
    "skill_archive", "skill_sha256", "skill_root", "skill_content_sha256",
    "pack_name", "pack_version", "pack_archive", "pack_sha256", "pack_root",
    "pack_content_sha256", "release_manifest_path",
    "release_manifest_sha256", "entry_path", "entry_sha256",
    "source_set_path", "source_set_sha256", "source_registry_path", "source_registry_version",
    "source_registry_sha256", "allowed_adapters", "workspace_root_id",
    "status", "created_at",
})
RECEIPT_KEYS = frozenset({
    "schema_version", "prototype_run_id", "validator_mode", "prototype_path",
    "prototype_sha256", "manifest_path", "manifest_sha256", "evidence_path",
    "evidence_sha256", "independent_review_path", "independent_review_sha256",
    "skill_root", "skill_content_sha256", "pack_root", "pack_content_sha256",
    "validator_path", "validator_sha256", "status", "checked_at",
})
V4_BINDING_FIELDS = frozenset({
    "manifest_contract_version",
    "independent_review_path", "independent_review_sha256",
    "reader_snapshot_path", "reader_snapshot_sha256",
    "reader_metadata_path", "reader_metadata_sha256",
    "previous_manifest_path", "previous_manifest_sha256",
    "previous_snapshot_path", "previous_snapshot_sha256",
    "lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256",
    "product_freeze_manifest_path", "product_freeze_manifest_sha256",
})
V4_RECEIPT_KEYS = RECEIPT_KEYS | V4_BINDING_FIELDS
BUILDER_HANDOFF_KEYS = frozenset({
    "schema_version", "prototype_run_id", "builder_handoff_id",
    "builder_context_id", "entry_path", "entry_sha256", "source_set_path",
    "source_set_sha256", "snapshot_path", "snapshot_sha256",
    "prd_artifact_path", "prd_artifact_sha256", "review_artifact_path",
    "review_artifact_sha256", "prototype_path", "prototype_sha256",
    "manifest_path", "manifest_sha256", "evidence_path", "evidence_sha256",
    "independent_review_path", "independent_review_sha256",
    "validation_receipt_path", "validation_receipt_sha256", "delivery",
    "coverage", "baseline_evidence", "high_fidelity_readiness",
    "automated_validation", "prototype_sync", "pending_item_ids",
    "blocker_ids", "created_at",
})
V4_BUILDER_HANDOFF_KEYS = BUILDER_HANDOFF_KEYS | V4_BINDING_FIELDS
REVIEW_KEYS = frozenset({
    "schema_version", "prototype_run_id", "review_context_id",
    "reviewed_builder_handoff_sha256", "reviewed_prototype_sha256",
    "verdict", "findings", "reviewed_at",
})
V4_REVIEW_KEYS = REVIEW_KEYS | V4_BINDING_FIELDS | frozenset({
    "reviewed_manifest_sha256", "reviewed_reader_snapshot_sha256",
    "reviewed_previous_manifest_sha256", "reviewed_previous_snapshot_sha256",
    "negative_check_ids",
})
PROTOTYPE_HANDOFF_KEYS = frozenset({
    "schema_version", "prototype_run_id", "review_context_id", "skill_name",
    "skill_version", "skill_sha256", "skill_content_sha256", "pack_name",
    "pack_version", "pack_sha256", "pack_content_sha256",
    "source_registry_version", "source_registry_sha256",
    "entry_path", "entry_sha256", "prd_artifact_path", "prd_artifact_sha256",
    "review_artifact_path", "review_artifact_sha256", "prototype_path",
    "prototype_sha256", "manifest_path", "manifest_sha256", "evidence_path",
    "evidence_sha256", "independent_review_path", "independent_review_sha256",
    "builder_handoff_path", "builder_handoff_sha256", "prototype_review_path",
    "prototype_review_sha256", "baseline_evidence",
    "high_fidelity_readiness", "automated_validation", "independent_review",
    "prototype_sync", "coverage", "blocker_ids", "gate_status", "created_at",
})
V4_PROTOTYPE_HANDOFF_KEYS = PROTOTYPE_HANDOFF_KEYS | V4_BINDING_FIELDS


def _context_keys(document, keys):
    if type(document) is dict and "validation_context" in document:
        context = document["validation_context"]
        if keys not in (V4_RECEIPT_KEYS, V4_BUILDER_HANDOFF_KEYS) or type(context) is not dict or set(context) != {"workspace_root", "evidence_roots"}:
            raise ContractError("Standard validation_context fields are invalid")
        roots = context["evidence_roots"]
        if type(roots) is not list or not roots or any(type(root) is not str for root in roots) or roots != sorted(set(roots)):
            raise ContractError("Standard evidence_roots must be sorted and unique")
        for path in [context["workspace_root"], *roots]:
            if type(path) is not str or not PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or path != PurePosixPath(path).as_posix():
                raise ContractError("Standard context requires canonical absolute paths")
        return keys | {"validation_context"}
    return keys


def _execution_keys(document, keys):
    if type(document) is dict and "execution" in document:
        if keys not in (V4_REVIEW_KEYS, V4_PROTOTYPE_HANDOFF_KEYS) or document["execution"] != {
            "mode": "t01-0.1.18-compatibility", "runtime_version": "0.1.19",
            "source_skill_version": "0.1.18", "source_pack_version": "0.1.18",
        }:
            raise ContractError("T01 execution provenance is invalid")
        return keys | {"execution"}
    return keys


def _closed(document: object, keys: frozenset[str], label: str, *, schema_version: int = SCHEMA_VERSION) -> dict:
    if type(document) is not dict or set(document) != keys:
        raise ContractError(f"{label} fields are invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != schema_version:
        raise ContractError(f"{label} schema_version must be {schema_version}")
    return document


def _text(value: object, field: str, *, maximum: int = 500) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ContractError(f"{field} is invalid")
    return value


def _identifier(value: object, field: str) -> str:
    text = _text(value, field, maximum=128)
    if ID_RE.fullmatch(text) is None:
        raise ContractError(f"{field} is invalid")
    return text


def _sha256(value: object, field: str) -> str:
    if type(value) is not str or HASH_RE.fullmatch(value) is None:
        raise ContractError(f"{field} is invalid")
    return value


def _timestamp(value: object, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} needs UTC offset")
    return text


def _enum(value: object, values: frozenset[str], field: str) -> str:
    if value not in values:
        raise ContractError(f"{field} is invalid")
    return str(value)


def _path(value: object, field: str) -> str:
    text = _text(value, field, maximum=4096)
    if "\x00" in text:
        raise ContractError(f"{field} is invalid")
    return text


def _relative_path(value: object, field: str) -> str:
    text = _path(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ContractError(f"{field} must be a safe relative path")
    return text


def _semver(value: object, field: str) -> str:
    text = _text(value, field)
    if SEMVER_RE.fullmatch(text) is None:
        raise ContractError(f"{field} must be semver")
    return text


def _string_list(value: object, field: str) -> list[str]:
    if type(value) is not list:
        raise ContractError(f"{field} must be a list")
    result = [_identifier(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ContractError(f"{field} has duplicates")
    return result


def _path_hash_pair(
    document: dict,
    path_field: str,
    hash_field: str,
    *,
    required: bool | None,
) -> None:
    path_value = document[path_field]
    hash_value = document[hash_field]
    if required is False:
        if path_value is not None or hash_value is not None:
            raise ContractError(f"{path_field} and {hash_field} must be null")
        return
    if required is None and path_value is None and hash_value is None:
        return
    _path(path_value, path_field)
    _sha256(hash_value, hash_field)


def _validate_v4_bindings(value: dict, *, final: bool = False) -> None:
    if value["manifest_contract_version"] != "4.0.0":
        raise ContractError("manifest_contract_version must be 4.0.0")
    for path_field, hash_field in (
        ("reader_snapshot_path", "reader_snapshot_sha256"),
        ("reader_metadata_path", "reader_metadata_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=True)
    previous = (
        ("previous_manifest_path", "previous_manifest_sha256"),
        ("previous_snapshot_path", "previous_snapshot_sha256"),
        ("lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256"),
    )
    any_previous = any(value[path] is not None or value[digest] is not None for path, digest in previous)
    for path, digest in previous:
        _path_hash_pair(value, path, digest, required=any_previous)
    _path_hash_pair(value, "product_freeze_manifest_path", "product_freeze_manifest_sha256", required=None)
    if not final and value.get("independent_review_path") is not None:
        raise ContractError("Independent Review must follow Builder Handoff")
    if final:
        _path_hash_pair(value, "independent_review_path", "independent_review_sha256", required=True)


def validate_entry(document: object) -> dict:
    if type(document) is dict and type(document.get("schema_version")) is int and document["schema_version"] == 1:
        raise ContractError("Prototype Entry v1 cannot resume; create an assessment and a new Run")
    value = _closed(document, ENTRY_KEYS, "prototype entry", schema_version=2)
    for field in ("prototype_run_id", "prd_run_id", "review_context_id"):
        _identifier(value[field], field)
    _path_hash_pair(value, "review_artifact_path", "review_artifact_sha256", required=True)
    _path_hash_pair(value, "prd_artifact_path", "prd_artifact_sha256", required=True)
    if value["review_evidence"] != "pass" or value["prototype_readiness"] != "pass":
        raise ContractError("review_evidence and prototype_readiness must be pass")
    if type(value["spec_readiness"]) is not str or value["spec_readiness"] not in {"pass", "block", "not_assessed"}:
        raise ContractError("spec_readiness is invalid")
    _path_hash_pair(value, "readiness_assessment_path", "readiness_assessment_sha256", required=True)
    _text(value["stage_authorized_by"], "stage_authorized_by")
    _timestamp(value["stage_authorized_at"], "stage_authorized_at")
    _enum(value["requested_mode"], MODES, "requested_mode")
    _enum(value["requested_delivery"], DELIVERY_LEVELS, "requested_delivery")
    _enum(value["requested_coverage"], COVERAGE_LEVELS, "requested_coverage")
    return value


def validate_source_set(document: object) -> dict:
    value = _closed(document, SOURCE_SET_KEYS, "prototype source set")
    _identifier(value["prototype_run_id"], "prototype_run_id")
    _identifier(value["source_set_id"], "source_set_id")
    _timestamp(value["selected_at"], "selected_at")
    if type(value["sources"]) is not list or len(value["sources"]) != len(SOURCE_ROLES):
        raise ContractError("sources must contain the six required roles")
    roles: list[str] = []
    for item in value["sources"]:
        if type(item) is not dict or set(item) != {"role", "path", "sha256"}:
            raise ContractError("source fields are invalid")
        roles.append(_text(item["role"], "source role"))
        _path(item["path"], "source path")
        _sha256(item["sha256"], "source sha256")
    if tuple(roles) != SOURCE_ROLES:
        raise ContractError("source roles must be complete and ordered")
    return value


def validate_snapshot(document: object) -> dict:
    value = _closed(document, SNAPSHOT_KEYS, "prototype snapshot")
    _identifier(value["prototype_run_id"], "prototype_run_id")
    for field in ("skill_name", "pack_name", "workspace_root_id"):
        _identifier(value[field], field)
    for field in ("skill_version", "pack_version", "source_registry_version"):
        _semver(value[field], field)
    for field in ("skill_archive", "pack_archive"):
        archive = PurePosixPath(_relative_path(value[field], field))
        if archive.suffix != ".zip":
            raise ContractError(f"{field} must be a ZIP path")
    for path_field, hash_field in (
        ("release_manifest_path", "release_manifest_sha256"),
        ("skill_root", "skill_content_sha256"),
        ("pack_root", "pack_content_sha256"),
        ("entry_path", "entry_sha256"),
        ("source_set_path", "source_set_sha256"),
        ("source_registry_path", "source_registry_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=True)
    for field in ("skill_sha256", "pack_sha256"):
        _sha256(value[field], field)
    if value["allowed_adapters"] != ["local-file"]:
        raise ContractError("allowed_adapters must equal ['local-file']")
    _enum(value["status"], RUN_STATES, "status")
    _timestamp(value["created_at"], "created_at")
    return value


def validate_receipt(document: object) -> dict:
    keys = V4_RECEIPT_KEYS if type(document) is dict and "manifest_contract_version" in document else RECEIPT_KEYS
    value = _closed(document, _context_keys(document, keys), "prototype validation receipt")
    _identifier(value["prototype_run_id"], "prototype_run_id")
    mode = _enum(value["validator_mode"], DELIVERY_LEVELS, "validator_mode")
    _path_hash_pair(value, "prototype_path", "prototype_sha256", required=True)
    _path_hash_pair(value, "skill_root", "skill_content_sha256", required=True)
    _path_hash_pair(value, "pack_root", "pack_content_sha256", required=True)
    if mode == "light":
        for pair in (
            ("manifest_path", "manifest_sha256"),
            ("evidence_path", "evidence_sha256"),
            ("independent_review_path", "independent_review_sha256"),
            ("validator_path", "validator_sha256"),
        ):
            _path_hash_pair(value, *pair, required=False)
    else:
        _path_hash_pair(value, "manifest_path", "manifest_sha256", required=True)
        _path_hash_pair(value, "evidence_path", "evidence_sha256", required=True)
        _path_hash_pair(
            value,
            "independent_review_path",
            "independent_review_sha256",
            required=None,
        )
        if keys is V4_RECEIPT_KEYS:
            _validate_v4_bindings(value)
        _path_hash_pair(value, "validator_path", "validator_sha256", required=True)
    if value["status"] != "pass":
        raise ContractError("persisted validation receipt must be pass")
    _timestamp(value["checked_at"], "checked_at")
    return value


def validate_builder_handoff(document: object) -> dict:
    keys = V4_BUILDER_HANDOFF_KEYS if type(document) is dict and "manifest_contract_version" in document else BUILDER_HANDOFF_KEYS
    value = _closed(document, _context_keys(document, keys), "builder handoff")
    for field in ("prototype_run_id", "builder_handoff_id", "builder_context_id"):
        _identifier(value[field], field)
    for path_field, hash_field in (
        ("entry_path", "entry_sha256"),
        ("source_set_path", "source_set_sha256"),
        ("snapshot_path", "snapshot_sha256"),
        ("prd_artifact_path", "prd_artifact_sha256"),
        ("review_artifact_path", "review_artifact_sha256"),
        ("prototype_path", "prototype_sha256"),
        ("validation_receipt_path", "validation_receipt_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=True)
    delivery = _enum(value["delivery"], DELIVERY_LEVELS, "delivery")
    for path_field, hash_field in (
        ("manifest_path", "manifest_sha256"),
        ("evidence_path", "evidence_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=delivery == "standard")
    _path_hash_pair(
        value,
        "independent_review_path",
        "independent_review_sha256",
        required=False if delivery == "light" else None,
    )
    if keys is V4_BUILDER_HANDOFF_KEYS:
        _validate_v4_bindings(value)
    _enum(value["coverage"], COVERAGE_LEVELS, "coverage")
    for field in (
        "baseline_evidence", "high_fidelity_readiness",
        "automated_validation", "prototype_sync",
    ):
        _enum(value[field], GATE_STATES, field)
    _string_list(value["pending_item_ids"], "pending_item_ids")
    _string_list(value["blocker_ids"], "blocker_ids")
    _timestamp(value["created_at"], "created_at")
    return value


def validate_review(document: object) -> dict:
    keys = V4_REVIEW_KEYS if type(document) is dict and "manifest_contract_version" in document else REVIEW_KEYS
    value = _closed(document, _execution_keys(document, keys), "prototype review", schema_version=2 if keys is V4_REVIEW_KEYS else SCHEMA_VERSION)
    _identifier(value["prototype_run_id"], "prototype_run_id")
    _identifier(value["review_context_id"], "review_context_id")
    _sha256(value["reviewed_builder_handoff_sha256"], "reviewed_builder_handoff_sha256")
    _sha256(value["reviewed_prototype_sha256"], "reviewed_prototype_sha256")
    _enum(value["verdict"], REVIEW_VERDICTS, "verdict")
    if type(value["findings"]) is not list:
        raise ContractError("findings must be a list")
    for finding in value["findings"]:
        if type(finding) is not dict or set(finding) != {
            "severity", "code", "message", "artifact_ref",
        }:
            raise ContractError("finding fields are invalid")
        _enum(finding["severity"], FINDING_SEVERITIES, "finding severity")
        _identifier(finding["code"], "finding code")
        _text(finding["message"], "finding message")
        _relative_path(finding["artifact_ref"], "finding artifact_ref")
    if value["verdict"] == "pass" and any(
        item["severity"] == "blocker" for item in value["findings"]
    ):
        raise ContractError("pass review cannot contain blocker findings")
    _timestamp(value["reviewed_at"], "reviewed_at")
    if keys is V4_REVIEW_KEYS:
        _validate_v4_bindings(value, final=True)
        for field in ("reviewed_manifest_sha256", "reviewed_reader_snapshot_sha256"):
            _sha256(value[field], field)
        for field in ("reviewed_previous_manifest_sha256", "reviewed_previous_snapshot_sha256"):
            if value[field] is not None:
                _sha256(value[field], field)
        required = ["unrelated-field-preservation", "empty-and-missing-values",
                    "historical-out-of-candidate-data", "unauthorized-actions",
                    "concurrent-conflict", "post-save-membership"]
        if value["negative_check_ids"] != required:
            raise ContractError("negative_check_ids must contain the six ordered v2 checks")
    return value


def validate_prototype_handoff(document: object) -> dict:
    keys = V4_PROTOTYPE_HANDOFF_KEYS if type(document) is dict and "manifest_contract_version" in document else PROTOTYPE_HANDOFF_KEYS
    value = _closed(document, _execution_keys(document, keys), "prototype handoff")
    for field in ("prototype_run_id", "review_context_id", "skill_name", "pack_name"):
        _identifier(value[field], field)
    for field in ("skill_version", "pack_version", "source_registry_version"):
        _semver(value[field], field)
    for field in (
        "skill_sha256", "skill_content_sha256", "pack_sha256",
        "pack_content_sha256", "source_registry_sha256",
    ):
        _sha256(value[field], field)
    for path_field, hash_field in (
        ("entry_path", "entry_sha256"),
        ("prd_artifact_path", "prd_artifact_sha256"),
        ("review_artifact_path", "review_artifact_sha256"),
        ("prototype_path", "prototype_sha256"),
        ("builder_handoff_path", "builder_handoff_sha256"),
        ("prototype_review_path", "prototype_review_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=True)
    for path_field, hash_field in (
        ("manifest_path", "manifest_sha256"),
        ("evidence_path", "evidence_sha256"),
        ("independent_review_path", "independent_review_sha256"),
    ):
        _path_hash_pair(value, path_field, hash_field, required=None)
    if keys is V4_PROTOTYPE_HANDOFF_KEYS:
        _validate_v4_bindings(value, final=True)
    for field in (
        "baseline_evidence", "high_fidelity_readiness", "automated_validation",
        "independent_review", "prototype_sync", "gate_status",
    ):
        if value[field] != "pass":
            raise ContractError(f"{field} must be pass")
    _enum(value["coverage"], COVERAGE_LEVELS, "coverage")
    if value["blocker_ids"] != []:
        raise ContractError("prototype handoff cannot contain blockers")
    _timestamp(value["created_at"], "created_at")
    return value
