from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import (
    HASH_RE,
    IDENTITY_RE,
    aware_timestamp,
    runtime_id,
)
from enterprise_prd.errors import ContractError
from enterprise_prd.heading_selection import normalize_heading

from .contracts import AcquisitionScope, SessionStatus
from .runtime import exclusive_json, load_json, session_lock
from .version import COMPONENT_VERSION


_ROOT_FIELDS = {
    "schema_version",
    "component_version",
    "grant_id",
    "session_id",
    "issued_at",
    "sources",
    "grant_sha256",
}
_SOURCE_FIELDS = {
    "source_id",
    "adapter",
    "revision",
    "acquisition_scope",
    "sections",
}
_SECTION_FIELDS = {"raw_path", "normalized_path"}


class ReadGrantService:
    def __init__(
        self,
        root: Path,
        workflow,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.workflow = workflow
        self.id_factory = id_factory or (
            lambda: f"GRANT-{uuid4().hex.upper()}"
        )

    def _validate(self, value: dict, expected_session_id: str) -> dict:
        if (
            type(value) is not dict
            or set(value) != _ROOT_FIELDS
            or value["schema_version"] != 1
            or value["component_version"] != COMPONENT_VERSION
            or runtime_id(value["session_id"], "session_id")
            != expected_session_id
        ):
            raise ContractError("read grant fields are invalid")
        runtime_id(value["grant_id"], "grant_id")
        if type(value["issued_at"]) is not str:
            raise ContractError("read grant issued_at is invalid")
        try:
            aware_timestamp(value["issued_at"], "issued_at")
        except (TypeError, ValueError) as exc:
            raise ContractError("read grant issued_at is invalid") from exc
        if type(value["sources"]) is not list or not value["sources"]:
            raise ContractError("read grant sources are invalid")
        source_ids: list[str] = []
        for source in value["sources"]:
            if type(source) is not dict or set(source) != _SOURCE_FIELDS:
                raise ContractError("read grant source fields are invalid")
            if (
                type(source["source_id"]) is not str
                or IDENTITY_RE.fullmatch(source["source_id"]) is None
                or type(source["adapter"]) is not str
                or IDENTITY_RE.fullmatch(source["adapter"]) is None
                or type(source["revision"]) is not str
                or not source["revision"].strip()
                or type(source["sections"]) is not list
                or not source["sections"]
            ):
                raise ContractError("read grant source values are invalid")
            try:
                AcquisitionScope(source["acquisition_scope"])
            except ValueError as exc:
                raise ContractError("read grant scope is invalid") from exc
            section_keys: list[str] = []
            for section in source["sections"]:
                if type(section) is not dict or set(section) != _SECTION_FIELDS:
                    raise ContractError("read grant section fields are invalid")
                raw_path = section["raw_path"]
                normalized_path = section["normalized_path"]
                if (
                    type(raw_path) is not str
                    or not raw_path.strip()
                    or type(normalized_path) is not str
                    or not normalized_path.strip()
                ):
                    raise ContractError("read grant section values are invalid")
                parts = raw_path.split("/")
                if (
                    any(not part.strip() for part in parts)
                    or "/".join(normalize_heading(part) for part in parts)
                    != normalized_path
                ):
                    raise ContractError("read grant section path is invalid")
                section_keys.append(normalized_path)
            if len(section_keys) != len(set(section_keys)):
                raise ContractError("read grant sections duplicate")
            source_ids.append(source["source_id"])
        if len(source_ids) != len(set(source_ids)):
            raise ContractError("read grant sources duplicate")
        digest = value["grant_sha256"]
        if type(digest) is not str or HASH_RE.fullmatch(digest) is None:
            raise ContractError("read grant hash is invalid")
        payload = {key: item for key, item in value.items() if key != "grant_sha256"}
        if sha256_bytes(canonical_json(payload)) != digest:
            raise ContractError("read grant hash mismatch")
        return value

    def issue(self, session_id: str) -> Path:
        checked_session_id = runtime_id(session_id, "session_id")
        grant_id = runtime_id(self.id_factory(), "grant_id")
        with session_lock(self.root, checked_session_id):
            state = self.workflow._load_unlocked(checked_session_id)
            if state["status"] != SessionStatus.SECTIONS_CONFIRMED.value:
                raise ContractError("read grant is not issuable now")
            candidates = {item["source_id"]: item for item in state["candidates"]}
            outlines = {item["source_id"]: item for item in state["outlines"]}
            selections_by_source = {
                source_id: [
                    item
                    for item in state["selections"]
                    if item["source_id"] == source_id
                ]
                for source_id in state["confirmed_source_ids"]
            }
            payload = {
                "schema_version": 1,
                "component_version": COMPONENT_VERSION,
                "grant_id": grant_id,
                "session_id": checked_session_id,
                "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sources": [
                    {
                        "source_id": source_id,
                        "adapter": candidates[source_id]["adapter"],
                        "revision": outlines[source_id]["revision"],
                        "acquisition_scope": selections_by_source[source_id][0][
                            "acquisition_scope"
                        ],
                        "sections": [
                            {
                                "raw_path": item["raw_path"],
                                "normalized_path": item["normalized_path"],
                            }
                            for item in selections_by_source[source_id]
                        ],
                    }
                    for source_id in state["confirmed_source_ids"]
                ],
            }
            digest = sha256_bytes(canonical_json(payload))
            value = {**payload, "grant_sha256": digest}
            self._validate(value, checked_session_id)
            path = self.workflow._directory(checked_session_id) / "read-grant.json"
            try:
                exclusive_json(path, value)
            except FileExistsError as exc:
                raise ContractError("read grant already exists") from exc
            updated = {
                **state,
                "status": SessionStatus.GRANT_READY.value,
                "active_grant_id": grant_id,
            }
            try:
                self.workflow._validate(updated, checked_session_id)
                from .runtime import atomic_json

                atomic_json(self.workflow._state_path(checked_session_id), updated)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return path

    def load(self, path: Path) -> dict:
        resolved = path.expanduser().resolve()
        if resolved.name != "read-grant.json":
            raise ContractError("read grant filename is invalid")
        parent = resolved.parent
        if parent.parent != self.root:
            raise ContractError("read grant path is outside runtime root")
        session_id = runtime_id(parent.name, "session_id")
        if self.workflow._directory(session_id) != parent:
            raise ContractError("read grant session path is invalid")
        return self._validate(load_json(resolved), session_id)
