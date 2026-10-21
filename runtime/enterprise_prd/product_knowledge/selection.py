from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import runtime_id
from enterprise_prd.errors import ContractError
from enterprise_prd.source_catalog import SourceCatalog

from .models import (
    AcquisitionScope,
    AdapterHeading,
    ConfirmedDocument,
    ContextBinding,
    DocumentCandidate,
    KnowledgeNeed,
    SectionSelection,
    SelectionEventType,
    SelectionState,
    SelectionStatus,
    SourceOutline,
)


_TERMINAL = {
    SelectionStatus.SOURCE_SELECTION_INVALID,
    SelectionStatus.REVISION_CHANGED,
    SelectionStatus.ACCESS_DENIED,
    SelectionStatus.CANCELLED,
    SelectionStatus.CONSUMED,
}
_EXPECTED_ORIGIN = {
    SelectionEventType.CANDIDATES_SET: SelectionStatus.CREATED,
    SelectionEventType.DOCUMENTS_CONFIRMED: SelectionStatus.CANDIDATES_READY,
    SelectionEventType.OUTLINES_RECORDED: SelectionStatus.DOCUMENTS_CONFIRMED,
    SelectionEventType.SECTIONS_CONFIRMED: SelectionStatus.OUTLINES_READY,
    SelectionEventType.GRANT_READY: SelectionStatus.SECTIONS_CONFIRMED,
    SelectionEventType.GRANT_ABANDONED: SelectionStatus.GRANT_READY,
    SelectionEventType.CONTEXT_CONSUMED: SelectionStatus.GRANT_READY,
}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(canonical_json(value))
    os.replace(temporary, path)


class SelectionService:
    def __init__(self, root: Path, registry: SourceCatalog) -> None:
        self.root = root.resolve()
        self.registry = registry

    def _path(self, session_id: str) -> Path:
        value = runtime_id(session_id, "session_id")
        path = (self.root / value).resolve()
        if path.parent != self.root:
            raise ContractError("session path escapes selection root")
        return path

    def _event_dir(self, session_id: str) -> Path:
        session = self._path(session_id)
        directory = (session / "events").resolve()
        if directory.parent != session:
            raise ContractError("selection event path escapes session")
        return directory

    def _state_path(self, session_id: str) -> Path:
        session = self._path(session_id)
        path = (session / "state.json").resolve()
        if path.parent != session:
            raise ContractError("selection state path escapes session")
        return path

    def _lock_path(self, session_id: str) -> Path:
        runtime_id(session_id, "session_id")
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.root / ".locks"
        directory.mkdir(exist_ok=True)
        resolved_directory = directory.resolve()
        if resolved_directory.parent != self.root:
            raise ContractError("selection lock directory escapes root")
        path = (resolved_directory / f"{session_id}.lock").resolve()
        if path.parent != resolved_directory:
            raise ContractError("selection lock path escapes root")
        return path

    @contextmanager
    def _lock(self, session_id: str, *, exclusive: bool) -> Iterator[None]:
        path = self._lock_path(session_id)
        with path.open("a+b") as handle:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
            )
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _events_unlocked(self, session_id: str) -> tuple[dict, ...]:
        directory = self._event_dir(session_id)
        if not directory.exists():
            return ()
        return tuple(
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        )

    def _events(self, session_id: str) -> tuple[dict, ...]:
        with self._lock(session_id, exclusive=False):
            return self._events_unlocked(session_id)

    def _append_unlocked(
        self,
        session_id: str,
        event: SelectionEventType,
        payload: dict,
    ) -> SelectionState:
        old = self._events_unlocked(session_id)
        if not old:
            if event is not SelectionEventType.SESSION_CREATED:
                raise ContractError("selection session is absent")
            previous = None
        else:
            state = self._from_events(session_id, old)
            if state.status in _TERMINAL:
                raise ContractError("selection session is terminal")
            if event is SelectionEventType.SESSION_CREATED:
                raise ContractError("selection session already exists")
            expected = _EXPECTED_ORIGIN.get(event)
            if expected is not None and state.status is not expected:
                raise ContractError(
                    f"selection event {event.value} is not allowed from "
                    f"{state.status.value}"
                )
            previous = state.event_sha256

        value = {
            "schema_version": 1,
            "event": event.value,
            "previous_sha256": previous,
            "payload": payload,
        }
        digest = sha256_bytes(canonical_json(value))
        directory = self._event_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{len(old) + 1:06d}-{digest}.json"
        with path.open("xb") as handle:
            handle.write(canonical_json({**value, "event_sha256": digest}))
        result = self._from_events(session_id, self._events_unlocked(session_id))
        _write(self._state_path(session_id), self._serialize(result))
        return result

    def _append(
        self,
        session_id: str,
        event: SelectionEventType,
        payload: dict,
    ) -> SelectionState:
        with self._lock(session_id, exclusive=True):
            return self._append_unlocked(session_id, event, payload)

    def create(
        self,
        *,
        session_id: str,
        run_id: str,
        run_snapshot_sha256: str,
        registry_sha256: str,
        need: KnowledgeNeed,
    ) -> SelectionState:
        runtime_id(session_id, "session_id")
        runtime_id(run_id, "run_id")
        need.validate()
        if registry_sha256 != self.registry.sha256:
            raise ContractError(
                "selection registry hash does not match service registry"
            )
        with self._lock(session_id, exclusive=True):
            path = self._path(session_id)
            if path.exists():
                raise ContractError("session already exists")
            path.mkdir(parents=True)
            return self._append_unlocked(
                session_id,
                SelectionEventType.SESSION_CREATED,
                {
                    "run_id": run_id,
                    "run_snapshot_sha256": run_snapshot_sha256,
                    "registry_sha256": registry_sha256,
                    "need": {
                        "requirement": need.requirement,
                        "required_bindings": [
                            item.value for item in need.required_bindings
                        ],
                        "optional_bindings": [
                            item.value for item in need.optional_bindings
                        ],
                    },
                },
            )

    def load(self, session_id: str) -> SelectionState:
        with self._lock(session_id, exclusive=False):
            state = self._from_events(
                session_id,
                self._events_unlocked(session_id),
            )
        if state.registry_sha256 != self.registry.sha256:
            raise ContractError(
                "selection registry changed after session creation"
            )
        return state

    def events(self, session_id: str) -> tuple[dict, ...]:
        return self._events(session_id)

    def set_candidates(
        self,
        session_id: str,
        candidates: tuple[DocumentCandidate, ...],
    ) -> SelectionState:
        state = self.load(session_id)
        if state.status is not SelectionStatus.CREATED or not candidates:
            raise ContractError("candidates are not allowed now")
        for item in candidates:
            item.validate()
            self.registry.resolve(item.source_id)
        if len({(item.source_id, item.binding) for item in candidates}) != len(
            candidates
        ):
            raise ContractError("candidates duplicate")
        return self._append(
            session_id,
            SelectionEventType.CANDIDATES_SET,
            {
                "candidates": [
                    {
                        "source_id": item.source_id,
                        "binding": item.binding.value,
                        "reason": item.reason,
                    }
                    for item in candidates
                ]
            },
        )

    def confirm_documents(
        self,
        session_id: str,
        source_ids: tuple[str, ...],
    ) -> SelectionState:
        state = self.load(session_id)
        if (
            state.status is not SelectionStatus.CANDIDATES_READY
            or not source_ids
            or len(source_ids) != len(set(source_ids))
        ):
            raise ContractError("documents are not confirmable")
        candidates = [
            item for item in state.candidates if item.source_id in source_ids
        ]
        if (
            {item.source_id for item in candidates} != set(source_ids)
            or not set(state.need.required_bindings)
            <= {item.binding for item in candidates}
        ):
            raise ContractError(
                "required binding missing or source unregistered"
            )
        return self._append(
            session_id,
            SelectionEventType.DOCUMENTS_CONFIRMED,
            {"source_ids": list(source_ids)},
        )

    def record_outlines(
        self,
        session_id: str,
        outlines: tuple[SourceOutline, ...],
        missing_optional: tuple[ContextBinding, ...] = (),
    ) -> SelectionState:
        state = self.load(session_id)
        if state.status is not SelectionStatus.DOCUMENTS_CONFIRMED:
            raise ContractError("outlines are not allowed now")
        outlined = {item.source_id for item in outlines}
        confirmed = {item.source_id for item in state.confirmed_documents}
        absent = confirmed - outlined
        optional_by_source = {
            item.source_id
            for item in state.confirmed_documents
            if not item.required
        }
        if outlined - confirmed or not absent <= optional_by_source:
            raise ContractError("outlines do not match confirmed sources")
        expected_missing = {
            item.binding
            for item in state.confirmed_documents
            if item.source_id in absent
        }
        if (
            set(missing_optional) != expected_missing
            or not set(missing_optional) <= set(state.need.optional_bindings)
        ):
            raise ContractError("missing optional outline bindings are invalid")
        return self._append(
            session_id,
            SelectionEventType.OUTLINES_RECORDED,
            {
                "outlines": [self._outline(item) for item in outlines],
                "missing_optional_bindings": [
                    item.value for item in missing_optional
                ],
            },
        )

    def confirm_sections(
        self,
        session_id: str,
        selections: tuple[SectionSelection, ...],
    ) -> SelectionState:
        state = self.load(session_id)
        if state.status is not SelectionStatus.OUTLINES_READY or not selections:
            raise ContractError("sections are not confirmable")
        confirmed = {
            (item.source_id, item.binding)
            for item in state.confirmed_documents
            if item.binding not in state.missing_optional_bindings
        }
        selected = {(item.source_id, item.binding) for item in selections}
        if not selected >= confirmed or any(
            (item.source_id, item.binding) not in confirmed
            for item in selections
        ):
            raise ContractError(
                "section binding does not match confirmed source"
            )
        if len(
            {
                (item.source_id, item.binding, item.normalized_path)
                for item in selections
            }
        ) != len(selections):
            raise ContractError("section selections duplicate")
        return self._append(
            session_id,
            SelectionEventType.SECTIONS_CONFIRMED,
            {"selections": [self._selection(item) for item in selections]},
        )

    def mark_grant_ready(
        self,
        session_id: str,
        grant_id: str,
        grant_sha256: str,
    ) -> SelectionState:
        runtime_id(grant_id, "grant_id")
        if self.load(session_id).status is not SelectionStatus.SECTIONS_CONFIRMED:
            raise ContractError("grant is not allowed now")
        return self._append(
            session_id,
            SelectionEventType.GRANT_READY,
            {"grant_id": grant_id, "grant_sha256": grant_sha256},
        )

    def mark_consumed(self, session_id: str, grant_id: str) -> SelectionState:
        runtime_id(grant_id, "grant_id")
        state = self.load(session_id)
        if (
            state.status is not SelectionStatus.GRANT_READY
            or state.active_grant_id != grant_id
        ):
            raise ContractError("grant is not active")
        return self._append(
            session_id,
            SelectionEventType.CONTEXT_CONSUMED,
            {"grant_id": grant_id},
        )

    def abandon_grant(
        self,
        session_id: str,
        grant_id: str,
        reason: str,
    ) -> SelectionState:
        runtime_id(grant_id, "grant_id")
        state = self.load(session_id)
        if (
            state.status is not SelectionStatus.GRANT_READY
            or state.active_grant_id != grant_id
        ):
            raise ContractError("grant is not active")
        return self._append(
            session_id,
            SelectionEventType.GRANT_ABANDONED,
            {"grant_id": grant_id, "reason": reason},
        )

    def fail(
        self,
        session_id: str,
        status: SelectionStatus,
        reason: str,
    ) -> SelectionState:
        mapping = {
            SelectionStatus.SOURCE_SELECTION_INVALID:
                SelectionEventType.SOURCE_SELECTION_INVALID,
            SelectionStatus.REVISION_CHANGED:
                SelectionEventType.REVISION_CHANGED,
            SelectionStatus.ACCESS_DENIED: SelectionEventType.ACCESS_DENIED,
            SelectionStatus.CANCELLED: SelectionEventType.CANCELLED,
        }
        if status not in mapping:
            raise ContractError("failure status is invalid")
        return self._append(
            session_id,
            mapping[status],
            {"reason": reason},
        )

    def _from_events(
        self,
        session_id: str,
        events: tuple[dict, ...],
    ) -> SelectionState:
        if not events:
            raise ContractError("selection session is absent")
        previous = ""
        data: dict = {}
        for index, item in enumerate(events):
            digest = item.get("event_sha256")
            unsigned = {
                key: value
                for key, value in item.items()
                if key != "event_sha256"
            }
            if (
                sha256_bytes(canonical_json(unsigned)) != digest
                or item.get("previous_sha256") != (previous or None)
            ):
                raise ContractError("selection event chain is invalid")
            previous = digest
            event = item["event"]
            payload = item["payload"]
            if index == 0 and event != "session_created":
                raise ContractError("selection event chain is invalid")
            if index > 0 and event == "session_created":
                raise ContractError("selection event chain is invalid")
            if event == "session_created":
                data = payload | {
                    "status": "created",
                    "candidates": [],
                    "confirmed": [],
                    "outlines": [],
                    "selections": [],
                    "missing_optional_bindings": [],
                    "grant": None,
                }
            elif event == "candidates_set":
                data["candidates"] = payload["candidates"]
                data["status"] = "candidates_ready"
            elif event == "documents_confirmed":
                data["confirmed"] = [
                    item
                    for item in data["candidates"]
                    if item["source_id"] in payload["source_ids"]
                ]
                data["status"] = "documents_confirmed"
            elif event == "outlines_recorded":
                data["outlines"] = payload["outlines"]
                data["missing_optional_bindings"] = payload.get(
                    "missing_optional_bindings",
                    [],
                )
                data["status"] = "outlines_ready"
            elif event == "sections_confirmed":
                data["selections"] = payload["selections"]
                data["status"] = "sections_confirmed"
            elif event == "grant_ready":
                data["grant"] = payload["grant_id"]
                data["status"] = "grant_ready"
            elif event == "grant_abandoned":
                data["grant"] = None
                data["status"] = "sections_confirmed"
            elif event == "context_consumed":
                data["status"] = "consumed"
            else:
                data["status"] = event
        need = KnowledgeNeed(
            data["need"]["requirement"],
            tuple(
                ContextBinding(item)
                for item in data["need"]["required_bindings"]
            ),
            tuple(
                ContextBinding(item)
                for item in data["need"]["optional_bindings"]
            ),
        )
        result = SelectionState(
            session_id,
            data["run_id"],
            data["run_snapshot_sha256"],
            data["registry_sha256"],
            need,
            SelectionStatus(data["status"]),
            tuple(
                DocumentCandidate(
                    item["source_id"],
                    ContextBinding(item["binding"]),
                    item["reason"],
                )
                for item in data["candidates"]
            ),
            tuple(
                ConfirmedDocument(
                    item["source_id"],
                    ContextBinding(item["binding"]),
                    ContextBinding(item["binding"]) in need.required_bindings,
                )
                for item in data["confirmed"]
            ),
            tuple(self._parse_outline(item) for item in data["outlines"]),
            tuple(
                self._parse_selection(item) for item in data["selections"]
            ),
            tuple(
                ContextBinding(item)
                for item in data["missing_optional_bindings"]
            ),
            data["grant"],
            previous,
        )
        result.validate()
        return result

    def _outline(self, value: SourceOutline) -> dict:
        return {
            "source_id": value.source_id,
            "title": value.title,
            "revision": value.revision,
            "adapter": value.adapter,
            "document_id_sha256": value.document_id_sha256,
            "outline_sha256": value.outline_sha256,
            "headings": [heading.__dict__ for heading in value.headings],
        }

    def _parse_outline(self, value: dict) -> SourceOutline:
        return SourceOutline(
            1,
            "document-outline",
            "outline-v1",
            "",
            value["source_id"],
            value["document_id_sha256"],
            value["title"],
            value["revision"],
            value["adapter"],
            "",
            tuple(AdapterHeading(**heading) for heading in value["headings"]),
            value["outline_sha256"],
        )

    def _selection(self, value: SectionSelection) -> dict:
        return {
            "source_id": value.source_id,
            "binding": value.binding.value,
            "block_id": value.block_id,
            "raw_path": value.raw_path,
            "normalized_path": value.normalized_path,
            "heading_level": value.heading_level,
            "acquisition_scope": value.acquisition_scope.value,
            "remote_read": value.remote_read,
        }

    def _parse_selection(self, value: dict) -> SectionSelection:
        return SectionSelection(
            value["source_id"],
            ContextBinding(value["binding"]),
            value["block_id"],
            value["raw_path"],
            value["normalized_path"],
            value["heading_level"],
            AcquisitionScope(value["acquisition_scope"]),
            value["remote_read"],
        )

    def _serialize(self, state: SelectionState) -> dict:
        return {
            "schema_version": 1,
            "session_id": state.session_id,
            "status": state.status.value,
            "event_sha256": state.event_sha256,
        }
