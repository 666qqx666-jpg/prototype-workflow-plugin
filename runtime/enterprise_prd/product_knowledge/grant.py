from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, aware_timestamp, runtime_id
from enterprise_prd.errors import ContractError, IntegrityError

from .models import (
    AcquisitionScope,
    ContextBinding,
    GrantReceiptStatus,
    SelectionStatus,
)


_GRANT_FIELDS = {
    "schema_version",
    "grant_id",
    "session_id",
    "run_id",
    "run_snapshot_sha256",
    "registry_sha256",
    "selection_sha256",
    "required_bindings",
    "optional_bindings",
    "missing_optional_bindings",
    "issued_at",
    "sources",
    "grant_sha256",
}
_SOURCE_FIELDS = {
    "source_id",
    "binding",
    "adapter",
    "revision",
    "outline_sha256",
    "acquisition_scope",
    "sections",
}
_SECTION_FIELDS = {
    "block_id",
    "raw_path",
    "normalized_path",
    "heading_level",
    "remote_read",
}


def _closed_string(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ContractError(f"{name} is required")
    return value


def _hash(value: object, name: str) -> str:
    if type(value) is not str or HASH_RE.fullmatch(value) is None:
        raise ContractError(f"{name} is invalid")
    return value


def _bindings(value: object, name: str) -> tuple[ContextBinding, ...]:
    if type(value) is not list:
        raise ContractError(f"{name} must be a list")
    try:
        result = tuple(ContextBinding(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} is invalid") from exc
    if len(result) != len(set(result)):
        raise ContractError(f"{name} has duplicates")
    return result


class ReadGrantService:
    def __init__(
        self,
        root: Path,
        selection,
        store,
        registry,
        adapters: dict,
    ) -> None:
        self.root = root.resolve()
        self.selection = selection
        self.store = store
        self.registry = registry
        self.adapters = adapters

    def _grant_path(self, session_id: str, grant_id: str) -> Path:
        session = (self.root / runtime_id(session_id, "session_id")).resolve()
        if session.parent != self.root:
            raise ContractError("grant session path escapes root")
        directory = (session / "grants").resolve()
        if directory.parent != session:
            raise ContractError("grant directory escapes session")
        path = (
            directory / f"{runtime_id(grant_id, 'grant_id')}.json"
        ).resolve()
        if path.parent != directory:
            raise ContractError("grant path escapes session")
        return path

    def issue(self, session_id: str, *, grant_id: str) -> Path:
        runtime_id(session_id, "session_id")
        runtime_id(grant_id, "grant_id")
        state = self.selection.load(session_id)
        if state.status is not SelectionStatus.SECTIONS_CONFIRMED:
            raise ContractError("sections must be confirmed before grant")
        sources = []
        by_source: dict[str, list] = {}
        for section in state.section_selections:
            by_source.setdefault(section.source_id, []).append(section)
        for source_id in sorted(by_source):
            target = self.registry.resolve(source_id)
            outline = next(
                item for item in state.outlines if item.source_id == source_id
            )
            observed_revision = self.adapters[target.locator.adapter].revision(
                target.token
            )
            if observed_revision != outline.revision:
                self.selection.fail(
                    session_id,
                    SelectionStatus.REVISION_CHANGED,
                    "revision changed before grant",
                )
                raise ContractError("revision changed before grant")
            sections = by_source[source_id]
            for binding in dict.fromkeys(item.binding for item in sections):
                bound = tuple(item for item in sections if item.binding is binding)
                sources.append(
                    {
                        "source_id": source_id,
                        "binding": binding.value,
                        "adapter": target.locator.adapter,
                        "revision": outline.revision,
                        "outline_sha256": outline.outline_sha256,
                        "acquisition_scope": bound[0].acquisition_scope.value,
                        "sections": [
                            {
                                "block_id": item.block_id,
                                "raw_path": item.raw_path,
                                "normalized_path": item.normalized_path,
                                "heading_level": item.heading_level,
                                "remote_read": item.remote_read,
                            }
                            for item in bound
                        ],
                    }
                )
        payload = {
            "schema_version": 1,
            "grant_id": grant_id,
            "session_id": session_id,
            "run_id": state.run_id,
            "run_snapshot_sha256": state.run_snapshot_sha256,
            "registry_sha256": state.registry_sha256,
            "selection_sha256": state.event_sha256,
            "required_bindings": [
                item.value for item in state.need.required_bindings
            ],
            "optional_bindings": [
                item.value for item in state.need.optional_bindings
            ],
            "missing_optional_bindings": [
                item.value for item in state.missing_optional_bindings
            ],
            "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": sources,
        }
        digest = sha256_bytes(canonical_json(payload))
        value = {**payload, "grant_sha256": digest}
        self._validate(value)
        path = self._grant_path(session_id, grant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(canonical_json(value))
        try:
            self.selection.mark_grant_ready(session_id, grant_id, digest)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def _validate(self, raw: object) -> dict:
        if type(raw) is not dict or set(raw) != _GRANT_FIELDS:
            raise ContractError("grant fields are invalid")
        if raw["schema_version"] != 1:
            raise ContractError("grant schema version is invalid")
        for field in ("grant_id", "session_id", "run_id"):
            runtime_id(raw[field], field)
        for field in (
            "run_snapshot_sha256",
            "registry_sha256",
            "selection_sha256",
            "grant_sha256",
        ):
            _hash(raw[field], field)
        aware_timestamp(raw["issued_at"], "grant issued_at")
        required = _bindings(raw["required_bindings"], "required bindings")
        optional = _bindings(raw["optional_bindings"], "optional bindings")
        missing = _bindings(
            raw["missing_optional_bindings"], "missing optional bindings"
        )
        if not required and not optional:
            raise ContractError("grant bindings are empty")
        if set(required) & set(optional):
            raise ContractError("grant required and optional bindings overlap")
        if not set(missing) <= set(optional):
            raise ContractError("missing optional bindings are invalid")
        if type(raw["sources"]) is not list:
            raise ContractError("grant sources must be a list")
        declared = set(required) | set(optional)
        present: set[ContextBinding] = set()
        pairs: set[tuple[str, ContextBinding]] = set()
        source_contracts: dict[str, tuple[str, str, str, AcquisitionScope]] = {}
        remote_by_source: dict[str, bool] = {}
        for source in raw["sources"]:
            if type(source) is not dict or set(source) != _SOURCE_FIELDS:
                raise ContractError("grant source fields are invalid")
            source_id = _closed_string(source["source_id"], "source_id")
            adapter = _closed_string(source["adapter"], "source adapter")
            revision = _closed_string(source["revision"], "source revision")
            _hash(source["outline_sha256"], "outline hash")
            try:
                binding = ContextBinding(source["binding"])
                scope = AcquisitionScope(source["acquisition_scope"])
            except (TypeError, ValueError) as exc:
                raise ContractError("grant source enum is invalid") from exc
            if binding not in declared or binding in missing:
                raise ContractError("grant source binding is not authorized")
            pair = (source_id, binding)
            if pair in pairs:
                raise ContractError("grant source binding is duplicated")
            pairs.add(pair)
            present.add(binding)
            if self.registry is not None:
                target = self.registry.resolve(source_id)
                if target.locator.adapter != adapter:
                    raise IntegrityError("grant adapter differs from registry")
            contract = (adapter, revision, source["outline_sha256"], scope)
            previous = source_contracts.setdefault(source_id, contract)
            if previous != contract:
                raise ContractError("same source has conflicting grant locks")
            sections = source["sections"]
            if type(sections) is not list or not sections:
                raise ContractError("grant source sections are required")
            seen_sections: set[tuple[str, str]] = set()
            for section in sections:
                if type(section) is not dict or set(section) != _SECTION_FIELDS:
                    raise ContractError("grant section fields are invalid")
                block_id = _closed_string(section["block_id"], "section block_id")
                raw_path = _closed_string(section["raw_path"], "section raw_path")
                normalized_path = _closed_string(
                    section["normalized_path"], "section normalized_path"
                )
                if (
                    type(section["heading_level"]) is not int
                    or not 1 <= section["heading_level"] <= 6
                    or type(section["remote_read"]) is not bool
                ):
                    raise ContractError("grant section metadata is invalid")
                key = (block_id, normalized_path)
                if key in seen_sections:
                    raise ContractError("grant sections are duplicated")
                seen_sections.add(key)
                remote_by_source[source_id] = (
                    remote_by_source.get(source_id, False)
                    or section["remote_read"]
                )
                del raw_path
        if not set(required) <= present:
            raise ContractError("required grant bindings are missing")
        if (set(optional) - set(missing)) != (present & set(optional)):
            raise ContractError("optional grant bindings are inconsistent")
        if any(not value for value in remote_by_source.values()):
            raise ContractError("each grant source requires a remote read")
        return raw

    def load(
        self,
        grant_path: Path,
        *,
        expected_run_snapshot_sha256: str | None = None,
        require_active: bool = False,
    ) -> dict:
        raw = json.loads(grant_path.read_text(encoding="utf-8"))
        if type(raw) is not dict or type(raw.get("grant_sha256")) is not str:
            raise ContractError("grant hash is invalid")
        digest = raw["grant_sha256"]
        unsigned = {key: item for key, item in raw.items() if key != "grant_sha256"}
        if digest != sha256_bytes(canonical_json(unsigned)):
            raise ContractError("grant hash is invalid")
        value = self._validate(raw)
        if (
            self.registry is not None
            and value["registry_sha256"] != self.registry.sha256
        ):
            raise IntegrityError("grant registry differs from locked registry")
        if (
            expected_run_snapshot_sha256 is not None
            and value["run_snapshot_sha256"] != expected_run_snapshot_sha256
        ):
            raise IntegrityError("grant run snapshot differs from current run")
        if require_active:
            if self.selection is None:
                raise ContractError("active grant validation requires selection")
            state = self.selection.load(value["session_id"])
            if (
                state.status is not SelectionStatus.GRANT_READY
                or state.active_grant_id != value["grant_id"]
                or state.run_id != value["run_id"]
                or state.run_snapshot_sha256 != value["run_snapshot_sha256"]
                or state.registry_sha256 != value["registry_sha256"]
            ):
                raise IntegrityError("grant is not active for this selection")
            events = self.selection.events(value["session_id"])
            last = events[-1]
            if (
                last.get("event") != "grant_ready"
                or last.get("previous_sha256") != value["selection_sha256"]
                or last.get("payload", {}).get("grant_id") != value["grant_id"]
                or last.get("payload", {}).get("grant_sha256") != digest
            ):
                raise IntegrityError("grant does not match selection event chain")
        return value

    def reserve(
        self,
        grant_path: Path,
        *,
        expected_run_snapshot_sha256: str | None = None,
    ) -> Path:
        grant = self.load(
            grant_path,
            expected_run_snapshot_sha256=expected_run_snapshot_sha256,
            require_active=self.selection is not None,
        )
        path = grant_path.with_suffix(".receipt.json")
        payload = {
            "schema_version": 1,
            "grant_id": grant["grant_id"],
            "grant_sha256": grant["grant_sha256"],
            "status": "reserved",
            "reason": None,
        }
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ContractError("grant cannot be replayed") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(payload))
        return path

    def finish(
        self,
        receipt_path: Path,
        *,
        status: GrantReceiptStatus,
        reason: str | None = None,
    ) -> Path:
        if status is GrantReceiptStatus.RESERVED:
            raise ContractError("receipt final status is invalid")
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        if value.get("status") != "reserved":
            raise ContractError("receipt already finalized")
        temporary = receipt_path.with_name(f".{receipt_path.name}.new")
        temporary.write_bytes(
            canonical_json({**value, "status": status.value, "reason": reason})
        )
        os.replace(temporary, receipt_path)
        return receipt_path
