from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, TypeVar
from uuid import uuid4

from enterprise_prd.contracts import HASH_RE, runtime_id
from enterprise_prd.canonical import canonical_json
from enterprise_prd.errors import AccessDenied, ContractError
from enterprise_prd.heading_selection import normalize_heading

from .contracts import (
    AcquisitionScope,
    Candidate,
    DirectoryEntry,
    Heading,
    RequestedSection,
    SessionStatus,
)
from .runtime import atomic_json, contained, load_json, session_lock
from .errors import ConcurrentStateConflict
from .snapshot import SessionSnapshot
from .version import COMPONENT_VERSION


_STATE_FIELDS = {
    "schema_version",
    "component_version",
    "session_id",
    "requirement",
    "status",
    "candidates",
    "confirmed_source_ids",
    "outlines",
    "selections",
    "active_grant_id",
}
_CANDIDATE_FIELDS = {
    "source_id",
    "display_name",
    "adapter",
    "locator_ref",
    "purpose",
}
_OUTLINE_FIELDS = {
    "source_id",
    "document_id_sha256",
    "title",
    "revision",
    "headings",
    "acquisition_scopes",
}
_HEADING_FIELDS = {
    "block_id",
    "title",
    "normalized_title",
    "level",
    "raw_path",
    "normalized_path",
}
_SELECTION_FIELDS = {
    "source_id",
    "purpose",
    "block_id",
    "raw_path",
    "normalized_path",
    "heading_level",
    "acquisition_scope",
}
_TERMINAL_STATUSES = {
    SessionStatus.ACCESS_DENIED,
    SessionStatus.REVISION_CHANGED,
    SessionStatus.INVALID_SOURCE,
    SessionStatus.INVALID_OUTLINE,
    SessionStatus.CANCELLED,
}

_CommitResult = TypeVar("_CommitResult")


class LocalContextWorkflow:
    def __init__(
        self,
        root: Path,
        directory,
        readers: dict,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.directory = directory
        self.readers = readers
        self.id_factory = id_factory or (
            lambda: f"SESSION-{uuid4().hex.upper()}"
        )

    def _directory(self, session_id: str) -> Path:
        return contained(self.root, session_id, "session_id")

    def _state_path(self, session_id: str) -> Path:
        return self._directory(session_id) / "session.json"

    def _validate(self, value: dict, expected_session_id: str) -> dict:
        if (
            set(value) != _STATE_FIELDS
            or value["schema_version"] != 1
            or value["component_version"] != COMPONENT_VERSION
        ):
            raise ContractError("session fields are invalid")
        if runtime_id(value["session_id"], "session_id") != expected_session_id:
            raise ContractError("session identity is invalid")
        if type(value["requirement"]) is not str or not value["requirement"].strip():
            raise ContractError("session requirement is invalid")
        try:
            status = SessionStatus(value["status"])
        except ValueError as exc:
            raise ContractError("session status is invalid") from exc
        if type(value["candidates"]) is not list or not value["candidates"]:
            raise ContractError("session candidates are invalid")
        candidates: list[Candidate] = []
        for item in value["candidates"]:
            if type(item) is not dict or set(item) != _CANDIDATE_FIELDS:
                raise ContractError("session candidate fields are invalid")
            candidate = Candidate(
                DirectoryEntry(
                    item["source_id"],
                    item["display_name"],
                    item["adapter"],
                    item["locator_ref"],
                ),
                item["purpose"],
            )
            candidate.validate()
            candidates.append(candidate)
        candidate_ids = [item.entry.source_id for item in candidates]
        candidate_locators = [
            (item.entry.adapter, item.entry.locator_ref) for item in candidates
        ]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ContractError("session candidate source IDs duplicate")
        if len(candidate_locators) != len(set(candidate_locators)):
            raise ContractError("session candidate locators duplicate")
        if type(value["confirmed_source_ids"]) is not list or any(
            type(item) is not str for item in value["confirmed_source_ids"]
        ):
            raise ContractError("confirmed sources are invalid")
        confirmed = value["confirmed_source_ids"]
        if len(confirmed) != len(set(confirmed)) or not set(confirmed) <= set(candidate_ids):
            raise ContractError("confirmed sources are invalid")
        if type(value["outlines"]) is not list or type(value["selections"]) is not list:
            raise ContractError("session collections are invalid")
        outlines = value["outlines"]
        outline_ids: list[str] = []
        for outline in outlines:
            if type(outline) is not dict or set(outline) != _OUTLINE_FIELDS:
                raise ContractError("session outline fields are invalid")
            if (
                type(outline["source_id"]) is not str
                or outline["source_id"] not in confirmed
                or type(outline["document_id_sha256"]) is not str
                or HASH_RE.fullmatch(outline["document_id_sha256"]) is None
                or type(outline["title"]) is not str
                or not outline["title"].strip()
                or type(outline["revision"]) is not str
                or not outline["revision"].strip()
                or type(outline["headings"]) is not list
                or not outline["headings"]
                or type(outline["acquisition_scopes"]) is not list
                or not outline["acquisition_scopes"]
            ):
                raise ContractError("session outline values are invalid")
            headings: list[Heading] = []
            for heading_raw in outline["headings"]:
                if type(heading_raw) is not dict or set(heading_raw) != _HEADING_FIELDS:
                    raise ContractError("session heading fields are invalid")
                try:
                    heading = Heading(**heading_raw)
                except TypeError as exc:
                    raise ContractError("session heading fields are invalid") from exc
                heading.validate()
                headings.append(heading)
            for attribute in ("block_id", "raw_path", "normalized_path"):
                values = [getattr(item, attribute) for item in headings]
                if len(values) != len(set(values)):
                    raise ContractError("session outline heading identities duplicate")
            try:
                scopes = [AcquisitionScope(item) for item in outline["acquisition_scopes"]]
            except ValueError as exc:
                raise ContractError("session acquisition scope is invalid") from exc
            if len(scopes) != len(set(scopes)):
                raise ContractError("session acquisition scopes duplicate")
            outline_ids.append(outline["source_id"])
        if len(outline_ids) != len(set(outline_ids)):
            raise ContractError("session outline sources duplicate")
        selections = value["selections"]
        candidate_by_id = {
            item.entry.source_id: item for item in candidates
        }
        outline_by_id = {
            item["source_id"]: item for item in outlines
        }
        selection_keys: list[tuple[str, str]] = []
        scopes_by_source: dict[str, set[AcquisitionScope]] = {}
        for selection in selections:
            if type(selection) is not dict or set(selection) != _SELECTION_FIELDS:
                raise ContractError("session selection fields are invalid")
            source_id = selection["source_id"]
            outline = outline_by_id.get(source_id)
            candidate = candidate_by_id.get(source_id)
            if outline is None or candidate is None:
                raise ContractError("session selection source is invalid")
            if selection["purpose"] != candidate.purpose:
                raise ContractError("session selection purpose is invalid")
            try:
                scope = AcquisitionScope(selection["acquisition_scope"])
            except ValueError as exc:
                raise ContractError("session selection scope is invalid") from exc
            if scope.value not in outline["acquisition_scopes"]:
                raise ContractError("session selection scope is undeclared")
            matches = [
                heading
                for heading in outline["headings"]
                if heading["block_id"] == selection["block_id"]
                and heading["raw_path"] == selection["raw_path"]
                and heading["normalized_path"] == selection["normalized_path"]
                and heading["level"] == selection["heading_level"]
            ]
            if len(matches) != 1:
                raise ContractError("session selection heading is invalid")
            selection_keys.append((source_id, selection["normalized_path"]))
            scopes_by_source.setdefault(source_id, set()).add(scope)
        if len(selection_keys) != len(set(selection_keys)):
            raise ContractError("session selections duplicate")
        if any(len(scopes) != 1 for scopes in scopes_by_source.values()):
            raise ContractError("session source selection scopes differ")

        active_grant_id = value["active_grant_id"]
        if active_grant_id is not None:
            runtime_id(active_grant_id, "active_grant_id")

        if status is SessionStatus.CREATED:
            if confirmed or value["outlines"] or value["selections"] or active_grant_id is not None:
                raise ContractError("created session contains derived state")
        elif status is SessionStatus.DOCUMENTS_CONFIRMED:
            if (
                not confirmed
                or value["outlines"]
                or value["selections"]
                or active_grant_id is not None
            ):
                raise ContractError("documents-confirmed state is invalid")
        elif status is SessionStatus.OUTLINES_READY:
            if (
                not confirmed
                or outline_ids != confirmed
                or value["selections"]
                or active_grant_id is not None
            ):
                raise ContractError("outlines-ready state is invalid")
        elif status is SessionStatus.SECTIONS_CONFIRMED:
            if (
                not confirmed
                or outline_ids != confirmed
                or not selections
                or set(scopes_by_source) != set(confirmed)
                or active_grant_id is not None
            ):
                raise ContractError("sections-confirmed state is invalid")
        elif status in {SessionStatus.GRANT_READY, SessionStatus.CONSUMED}:
            if (
                not confirmed
                or outline_ids != confirmed
                or not selections
                or set(scopes_by_source) != set(confirmed)
                or active_grant_id is None
            ):
                raise ContractError("grant-bound state is invalid")
        elif status in _TERMINAL_STATUSES:
            if outlines and outline_ids != confirmed:
                raise ContractError("terminal outline snapshot is invalid")
            if selections and (
                outline_ids != confirmed
                or set(scopes_by_source) != set(confirmed)
            ):
                raise ContractError("terminal selection snapshot is invalid")
        else:
            raise ContractError("session state is not supported by this component stage")
        return value

    def _load_unlocked(self, session_id: str) -> dict:
        checked_session_id = runtime_id(session_id, "session_id")
        return self._validate(
            load_json(self._state_path(checked_session_id)),
            checked_session_id,
        )

    def load(self, session_id: str) -> dict:
        with session_lock(self.root, session_id):
            return self._load_unlocked(session_id)

    def create(
        self,
        requirement: str,
        candidates: tuple[tuple[str, str], ...],
    ) -> dict:
        if (
            type(requirement) is not str
            or not requirement.strip()
            or type(candidates) is not tuple
            or not candidates
            or any(type(item) is not tuple or len(item) != 2 for item in candidates)
        ):
            raise ContractError("session input is invalid")
        ids = tuple(item[0] for item in candidates)
        if len(ids) != len(set(ids)):
            raise ContractError("candidate source IDs duplicate")
        resolved = []
        for source_id, purpose in candidates:
            value = Candidate(self.directory.resolve(source_id), purpose)
            value.validate()
            resolved.append(
                {
                    "source_id": value.entry.source_id,
                    "display_name": value.entry.display_name,
                    "adapter": value.entry.adapter,
                    "locator_ref": value.entry.locator_ref,
                    "purpose": value.purpose,
                }
            )
        session_id = runtime_id(self.id_factory(), "session_id")
        with session_lock(self.root, session_id):
            directory = self._directory(session_id)
            if directory.exists():
                raise ContractError("session already exists")
            directory.mkdir(parents=True)
            state = {
                "schema_version": 1,
                "component_version": COMPONENT_VERSION,
                "session_id": session_id,
                "requirement": requirement.strip(),
                "status": SessionStatus.CREATED.value,
                "candidates": resolved,
                "confirmed_source_ids": [],
                "outlines": [],
                "selections": [],
                "active_grant_id": None,
            }
            self._validate(state, session_id)
            atomic_json(self._state_path(session_id), state)
            return state

    def confirm_documents(
        self,
        session_id: str,
        source_ids: tuple[str, ...],
    ) -> dict:
        if (
            type(source_ids) is not tuple
            or not source_ids
            or any(type(item) is not str for item in source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            raise ContractError("confirmed sources are invalid")
        with session_lock(self.root, session_id):
            state = self._load_unlocked(session_id)
            if state["status"] != SessionStatus.CREATED.value:
                raise ContractError("documents are not confirmable now")
            candidate_ids = {item["source_id"] for item in state["candidates"]}
            if not set(source_ids) <= candidate_ids:
                raise ContractError("confirmed source is outside candidates")
            updated = {
                **state,
                "status": SessionStatus.DOCUMENTS_CONFIRMED.value,
                "confirmed_source_ids": list(source_ids),
            }
            self._validate(updated, session_id)
            atomic_json(self._state_path(session_id), updated)
            return updated

    def _snapshot_from_state(
        self,
        state: dict,
        expected_status: SessionStatus,
        action: str,
    ) -> SessionSnapshot:
        if type(expected_status) is not SessionStatus:
            raise ContractError("snapshot status is invalid")
        if type(action) is not str or not action.strip():
            raise ContractError("snapshot action is invalid")
        session_id = state.get("session_id")
        self._validate(state, session_id)
        if state["status"] != expected_status.value:
            raise ContractError("session is not in the expected snapshot state")
        return SessionSnapshot(
            session_id=session_id,
            status=expected_status,
            state_bytes=canonical_json(state),
            action=action,
        )

    def _commit_if_snapshot_current(
        self,
        snapshot: SessionSnapshot,
        commit: Callable[[dict], _CommitResult],
        *,
        before_compare: Callable[[], None] | None = None,
    ) -> _CommitResult:
        if type(snapshot) is not SessionSnapshot or not callable(commit):
            raise ContractError("snapshot commit arguments are invalid")
        if before_compare is not None and not callable(before_compare):
            raise ContractError("snapshot precedence callback is invalid")
        with session_lock(self.root, snapshot.session_id):
            if before_compare is not None:
                before_compare()
            current = self._load_unlocked(snapshot.session_id)
            if canonical_json(current) != snapshot.state_bytes:
                raise ConcurrentStateConflict(
                    f"session {snapshot.session_id} changed during {snapshot.action}"
                )
            return commit(current)

    def _terminal_from_snapshot(
        self,
        snapshot: SessionSnapshot,
        status: SessionStatus,
        *,
        before_compare: Callable[[], None] | None = None,
    ) -> dict:
        if status not in _TERMINAL_STATUSES:
            raise ContractError("terminal status is invalid")

        def commit(current: dict) -> dict:
            updated = {**current, "status": status.value}
            self._validate(updated, snapshot.session_id)
            atomic_json(self._state_path(snapshot.session_id), updated)
            return updated

        return self._commit_if_snapshot_current(
            snapshot, commit, before_compare=before_compare
        )

    def _terminal(self, session_id: str, status: SessionStatus) -> dict:
        if status not in _TERMINAL_STATUSES:
            raise ContractError("terminal status is invalid")
        with session_lock(self.root, session_id):
            state = self._load_unlocked(session_id)
            current = SessionStatus(state["status"])
            if current in _TERMINAL_STATUSES or current is SessionStatus.CONSUMED:
                raise ContractError("session is already terminal")
            updated = {**state, "status": status.value}
            self._validate(updated, session_id)
            atomic_json(self._state_path(session_id), updated)
            return updated

    def collect_outlines(self, session_id: str) -> dict:
        state = self.load(session_id)
        if state["status"] != SessionStatus.DOCUMENTS_CONFIRMED.value:
            raise ContractError("outlines are not readable now")
        snapshot = self._snapshot_from_state(
            state, SessionStatus.DOCUMENTS_CONFIRMED, "collect_outlines"
        )
        candidates = {item["source_id"]: item for item in state["candidates"]}
        staged = []
        for source_id in state["confirmed_source_ids"]:
            item = candidates[source_id]
            entry = DirectoryEntry(
                item["source_id"],
                item["display_name"],
                item["adapter"],
                item["locator_ref"],
            )
            reader = self.readers.get(entry.adapter)
            if reader is None:
                raise ContractError("source adapter is not configured")
            try:
                outline = reader.get_outline(entry)
            except AccessDenied:
                self._terminal_from_snapshot(snapshot, SessionStatus.ACCESS_DENIED)
                raise
            try:
                if (
                    outline.source_id != source_id
                    or type(outline.document_id_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", outline.document_id_sha256) is None
                    or type(outline.title) is not str
                    or not outline.title.strip()
                    or type(outline.revision) is not str
                    or not outline.revision.strip()
                    or type(outline.headings) is not tuple
                    or not outline.headings
                    or type(outline.acquisition_scopes) is not tuple
                    or not outline.acquisition_scopes
                    or any(
                        type(scope) is not AcquisitionScope
                        for scope in outline.acquisition_scopes
                    )
                    or len(outline.acquisition_scopes)
                    != len(set(outline.acquisition_scopes))
                ):
                    raise ContractError("outline identity or values are invalid")
                for heading in outline.headings:
                    if type(heading) is not Heading:
                        raise ContractError("outline heading type is invalid")
                    heading.validate()
                if any(
                    len({getattr(heading, attribute) for heading in outline.headings})
                    != len(outline.headings)
                    for attribute in ("block_id", "raw_path", "normalized_path")
                ):
                    raise ContractError("outline paths must be unique")
                staged.append(
                    {
                        "source_id": outline.source_id,
                        "document_id_sha256": outline.document_id_sha256,
                        "title": outline.title,
                        "revision": outline.revision,
                        "headings": [heading.__dict__ for heading in outline.headings],
                        "acquisition_scopes": [
                            scope.value for scope in outline.acquisition_scopes
                        ],
                    }
                )
            except ContractError:
                self._terminal_from_snapshot(snapshot, SessionStatus.INVALID_OUTLINE)
                raise

        def commit(current: dict) -> dict:
            updated = {
                **current,
                "status": SessionStatus.OUTLINES_READY.value,
                "outlines": staged,
            }
            self._validate(updated, session_id)
            atomic_json(
                self._directory(session_id) / "outline.json",
                {"schema_version": 1, "sources": staged},
            )
            atomic_json(self._state_path(session_id), updated)
            return updated

        return self._commit_if_snapshot_current(snapshot, commit)

    def confirm_sections(
        self,
        session_id: str,
        requests: tuple[RequestedSection, ...],
    ) -> dict:
        if (
            type(requests) is not tuple
            or not requests
            or any(type(item) is not RequestedSection for item in requests)
        ):
            raise ContractError("section requests are invalid")
        with session_lock(self.root, session_id):
            state = self._load_unlocked(session_id)
            if state["status"] != SessionStatus.OUTLINES_READY.value:
                raise ContractError("sections are not confirmable now")
            outlines = {item["source_id"]: item for item in state["outlines"]}
            candidates = {item["source_id"]: item for item in state["candidates"]}
            staged = []
            keys: list[tuple[str, str]] = []
            scopes_by_source: dict[str, set[AcquisitionScope]] = {}
            for request in requests:
                if (
                    type(request.source_id) is not str
                    or request.source_id not in state["confirmed_source_ids"]
                    or type(request.raw_path) is not str
                    or not request.raw_path.strip()
                    or type(request.acquisition_scope) is not AcquisitionScope
                ):
                    raise ContractError("section request values are invalid")
                parts = request.raw_path.split("/")
                if any(not part.strip() for part in parts):
                    raise ContractError("section path hierarchy is invalid")
                normalized = "/".join(normalize_heading(part) for part in parts)
                outline = outlines[request.source_id]
                matches = [
                    item
                    for item in outline["headings"]
                    if item["normalized_path"] == normalized
                ]
                if len(matches) != 1:
                    raise ContractError("heading path must match exactly once")
                if request.acquisition_scope.value not in outline["acquisition_scopes"]:
                    raise ContractError("acquisition scope is not declared")
                key = (request.source_id, normalized)
                if key in keys:
                    raise ContractError("selected section duplicates")
                keys.append(key)
                scopes_by_source.setdefault(request.source_id, set()).add(
                    request.acquisition_scope
                )
                heading = matches[0]
                staged.append(
                    {
                        "source_id": request.source_id,
                        "purpose": candidates[request.source_id]["purpose"],
                        "block_id": heading["block_id"],
                        "raw_path": heading["raw_path"],
                        "normalized_path": heading["normalized_path"],
                        "heading_level": heading["level"],
                        "acquisition_scope": request.acquisition_scope.value,
                    }
                )
            if set(scopes_by_source) != set(state["confirmed_source_ids"]):
                raise ContractError("every confirmed source needs a section")
            if any(len(scopes) != 1 for scopes in scopes_by_source.values()):
                raise ContractError("all selections for a source need one scope")
            updated = {
                **state,
                "status": SessionStatus.SECTIONS_CONFIRMED.value,
                "selections": staged,
            }
            self._validate(updated, session_id)
            atomic_json(self._state_path(session_id), updated)
            return updated

    def cancel(self, session_id: str) -> dict:
        with session_lock(self.root, session_id):
            state = self._load_unlocked(session_id)
            status = SessionStatus(state["status"])
            if status in _TERMINAL_STATUSES or status is SessionStatus.CONSUMED:
                raise ContractError("session cannot be cancelled")
            updated = {**state, "status": SessionStatus.CANCELLED.value}
            self._validate(updated, session_id)
            atomic_json(self._state_path(session_id), updated)
            return updated
