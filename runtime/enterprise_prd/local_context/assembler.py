from __future__ import annotations

from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_file
from enterprise_prd.errors import AccessDenied, ContractError, RevisionConflict

from .contracts import (
    AcquisitionScope,
    AdapterSection,
    ApprovedPath,
    DirectoryEntry,
    SessionStatus,
)
from .errors import SourceUnavailable
from .grant import ReadGrantService
from .runtime import atomic_json, exclusive_json, load_json, session_lock
from .version import COMPONENT_VERSION


_RECEIPT_FIELDS = {
    "schema_version",
    "component_version",
    "grant_id",
    "grant_sha256",
    "context_sha256",
    "status",
}


class ContextAssembler:
    def __init__(self, root: Path, workflow, readers: dict) -> None:
        self.root = root.expanduser().resolve()
        self.workflow = workflow
        self.readers = readers
        self.grants = ReadGrantService(self.root, workflow)

    def _expected_grant_sources(self, state: dict) -> list[dict]:
        candidates = {item["source_id"]: item for item in state["candidates"]}
        outlines = {item["source_id"]: item for item in state["outlines"]}
        return [
            {
                "source_id": source_id,
                "adapter": candidates[source_id]["adapter"],
                "revision": outlines[source_id]["revision"],
                "acquisition_scope": next(
                    item["acquisition_scope"]
                    for item in state["selections"]
                    if item["source_id"] == source_id
                ),
                "sections": [
                    {
                        "raw_path": item["raw_path"],
                        "normalized_path": item["normalized_path"],
                    }
                    for item in state["selections"]
                    if item["source_id"] == source_id
                ],
            }
            for source_id in state["confirmed_source_ids"]
        ]

    def _require_grant_state_match(self, grant: dict, state: dict) -> None:
        if (
            state["status"] != SessionStatus.GRANT_READY.value
            or state["active_grant_id"] != grant["grant_id"]
            or grant["session_id"] != state["session_id"]
            or grant["sources"] != self._expected_grant_sources(state)
        ):
            raise ContractError("grant does not match the active session snapshot")

    def _reject_existing_receipt(
        self,
        receipt_path: Path,
        context_path: Path,
        grant: dict,
    ) -> None:
        if not receipt_path.exists():
            return
        receipt = load_json(receipt_path)
        if (
            set(receipt) != _RECEIPT_FIELDS
            or receipt["schema_version"] != 1
            or receipt["component_version"] != COMPONENT_VERSION
            or receipt["grant_id"] != grant["grant_id"]
            or receipt["grant_sha256"] != grant["grant_sha256"]
            or receipt["status"] != "consumed"
            or type(receipt["context_sha256"]) is not str
            or not context_path.is_file()
            or sha256_file(context_path) != receipt["context_sha256"]
        ):
            raise ContractError("consumption receipt is invalid")
        raise ContractError("grant cannot be replayed")

    def assemble(self, grant_path: Path) -> Path:
        grant = self.grants.load(grant_path)
        session_id = grant["session_id"]
        session_directory = self.workflow._directory(session_id)
        context_path = session_directory / "context.json"
        receipt_path = session_directory / "consumption.json"
        grant_bytes = canonical_json(grant)

        def reject_existing_receipt() -> None:
            self._reject_existing_receipt(receipt_path, context_path, grant)

        with session_lock(self.root, session_id):
            self._reject_existing_receipt(receipt_path, context_path, grant)
            state = self.workflow._load_unlocked(session_id)
            self._require_grant_state_match(grant, state)
            snapshot = self.workflow._snapshot_from_state(
                state, SessionStatus.GRANT_READY, "assemble_context"
            )

        candidates = {item["source_id"]: item for item in state["candidates"]}
        staged_sources = []
        for grant_source in grant["sources"]:
            candidate = candidates[grant_source["source_id"]]
            if candidate["adapter"] != grant_source["adapter"]:
                raise ContractError("grant adapter does not match session candidate")
            entry = DirectoryEntry(
                candidate["source_id"],
                candidate["display_name"],
                candidate["adapter"],
                candidate["locator_ref"],
            )
            reader = self.readers.get(entry.adapter)
            if reader is None:
                raise ContractError("source adapter is not configured")
            scope = AcquisitionScope(grant_source["acquisition_scope"])
            approved_paths = tuple(
                ApprovedPath(item["normalized_path"], scope)
                for item in grant_source["sections"]
            )
            try:
                sections = reader.read_sections(
                    entry,
                    grant_source["revision"],
                    approved_paths,
                )
            except AccessDenied:
                self.workflow._terminal_from_snapshot(
                    snapshot,
                    SessionStatus.ACCESS_DENIED,
                    before_compare=reject_existing_receipt,
                )
                raise
            except SourceUnavailable:
                raise
            if type(sections) is not tuple or any(
                type(item) is not AdapterSection for item in sections
            ):
                raise ContractError("adapter sections are invalid")
            if any(item.revision != grant_source["revision"] for item in sections):
                self.workflow._terminal_from_snapshot(
                    snapshot,
                    SessionStatus.REVISION_CHANGED,
                    before_compare=reject_existing_receipt,
                )
                raise RevisionConflict(
                    f"source revision changed: {grant_source['source_id']}"
                )
            expected_keys = [
                (
                    grant_source["source_id"],
                    item.normalized_path,
                    item.acquisition_scope,
                )
                for item in approved_paths
            ]
            actual_keys = [
                (item.source_id, item.normalized_path, item.acquisition_scope)
                for item in sections
            ]
            if (
                actual_keys != expected_keys
                or len(actual_keys) != len(set(actual_keys))
                or any(type(item.content) is not str or not item.content for item in sections)
            ):
                raise ContractError("adapter sections do not match approved paths")
            raw_by_normalized = {
                item["normalized_path"]: item["raw_path"]
                for item in grant_source["sections"]
            }
            staged_sources.append(
                {
                    "source_id": grant_source["source_id"],
                    "display_name": candidate["display_name"],
                    "purpose": candidate["purpose"],
                    "revision": grant_source["revision"],
                    "acquisition_scope": grant_source["acquisition_scope"],
                    "sections": [
                        {
                            "raw_path": raw_by_normalized[item.normalized_path],
                            "normalized_path": item.normalized_path,
                            "content": item.content,
                        }
                        for item in sections
                    ],
                }
            )

        context = {
            "schema_version": 1,
            "component_version": COMPONENT_VERSION,
            "session_id": session_id,
            "grant_id": grant["grant_id"],
            "grant_sha256": grant["grant_sha256"],
            "requirement": state["requirement"],
            "sources": staged_sources,
        }
        def commit(current: dict) -> Path:
            current_grant = self.grants.load(grant_path)
            if canonical_json(current_grant) != grant_bytes:
                raise ContractError("read grant changed while sections were read")
            self._require_grant_state_match(grant, current)
            atomic_json(context_path, context)
            receipt = {
                "schema_version": 1,
                "component_version": COMPONENT_VERSION,
                "grant_id": grant["grant_id"],
                "grant_sha256": grant["grant_sha256"],
                "context_sha256": sha256_file(context_path),
                "status": "consumed",
            }
            try:
                exclusive_json(receipt_path, receipt)
            except FileExistsError:
                self._reject_existing_receipt(receipt_path, context_path, grant)
                raise AssertionError("unreachable")
            updated = {**current, "status": SessionStatus.CONSUMED.value}
            self.workflow._validate(updated, session_id)
            atomic_json(self.workflow._state_path(session_id), updated)
            return context_path

        return self.workflow._commit_if_snapshot_current(
            snapshot,
            commit,
            before_compare=reject_existing_receipt,
        )
