from __future__ import annotations

import json
from dataclasses import dataclass

from enterprise_prd.contracts import HASH_RE, IDENTITY_RE, aware_timestamp, need
from enterprise_prd.errors import ContractError

from .contracts import (
    BaselineMode, BaselineRole, BindingState, ConflictDecision, ConflictScope,
    DependencyState, LifecycleStatus, ObservationState, RESOLUTION_ID_RE, SURFACE_ID_RE,
)
from .errors import BaselineResolutionError
from .snapshot import RegistrySnapshot


@dataclass(frozen=True)
class BaselineRequest:
    task_label: str
    baseline_mode: BaselineMode
    surface_ids: tuple[str, ...]
    explicit_target_source_ids: tuple[str, ...]
    confirmed_auto_dependency_source_ids: tuple[str, ...]
    conflict_resolution_ids: tuple[str, ...]
    expected_registry_view_hash: str
    expected_source_revisions: tuple[tuple[str, str], ...]
    created_by: str
    created_at: str

    def validate(self) -> None:
        need(type(self.task_label) is str and bool(self.task_label.strip()), "task_label is invalid")
        need(type(self.baseline_mode) is BaselineMode, "baseline_mode is invalid")
        for values, label in (
            (self.surface_ids, "surface_ids"),
            (self.explicit_target_source_ids, "explicit targets"),
            (self.confirmed_auto_dependency_source_ids, "confirmed dependencies"),
            (self.conflict_resolution_ids, "conflict resolutions"),
        ):
            need(type(values) is tuple and tuple(sorted(values)) == values and len(values) == len(set(values)), f"{label} are invalid")
        need(bool(self.surface_ids), "surface_ids are required")
        need(all(SURFACE_ID_RE.fullmatch(item) is not None for item in self.surface_ids), "surface_ids contain an invalid ID")
        need(all(IDENTITY_RE.fullmatch(item) is not None for item in self.explicit_target_source_ids + self.confirmed_auto_dependency_source_ids), "request source IDs are invalid")
        need(all(RESOLUTION_ID_RE.fullmatch(item) is not None for item in self.conflict_resolution_ids), "conflict resolution IDs are invalid")
        need(HASH_RE.fullmatch(self.expected_registry_view_hash) is not None, "registry view hash is invalid")
        need(type(self.expected_source_revisions) is tuple and tuple(sorted(self.expected_source_revisions)) == self.expected_source_revisions, "expected revisions are invalid")
        need(len({item[0] for item in self.expected_source_revisions}) == len(self.expected_source_revisions), "expected revision sources repeat")
        need(all(type(item) is tuple and len(item) == 2 and HASH_RE.fullmatch(item[1]) is not None for item in self.expected_source_revisions), "expected revision values are invalid")
        need(all(IDENTITY_RE.fullmatch(item[0]) is not None for item in self.expected_source_revisions), "expected revision source IDs are invalid")
        need(type(self.created_by) is str and bool(self.created_by.strip()), "created_by is invalid")
        aware_timestamp(self.created_at, "created_at")
        if self.baseline_mode is BaselineMode.PRODUCTION:
            need(not self.explicit_target_source_ids and not self.confirmed_auto_dependency_source_ids and not self.conflict_resolution_ids, "production request has target fields")
        else:
            need(bool(self.explicit_target_source_ids), "target_design needs explicit targets")


@dataclass(frozen=True)
class ResolvedDocument:
    source_id: str
    revision: str
    role: BaselineRole
    lifecycle_status_at_lock: LifecycleStatus


@dataclass(frozen=True)
class ResolvedDependency:
    source_id: str
    depends_on: str


@dataclass(frozen=True)
class ResolvedConflict:
    resolution_id: str
    scope: ConflictScope
    decision: ConflictDecision
    excluded_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class BaselineResolution:
    task_label: str
    baseline_mode: BaselineMode
    surface_ids: tuple[str, ...]
    registry_view_hash: str
    documents: tuple[ResolvedDocument, ...]
    dependency_edges: tuple[ResolvedDependency, ...]
    excluded_candidate_source_ids: tuple[str, ...]
    conflict_decisions: tuple[ResolvedConflict, ...]
    created_by: str
    created_at: str

    @property
    def rendered(self) -> str:
        return json.dumps(self, default=lambda value: value.value if hasattr(value, "value") else value.__dict__, ensure_ascii=False, sort_keys=True)


def _fail(code: str) -> None:
    raise BaselineResolutionError(code)


class BaselineResolver:
    def __init__(self, snapshot: RegistrySnapshot) -> None:
        snapshot.validate()
        self.snapshot = snapshot
        self.documents = {item.source_id: item for item in snapshot.documents}
        self.dependencies = tuple(item for item in snapshot.dependencies if item.state is DependencyState.CONFIRMED)

    def _related(self, surface_ids: tuple[str, ...]) -> set[str]:
        known = {item.surface_id for item in self.snapshot.surfaces}
        if not set(surface_ids) <= known:
            raise ContractError("surface is outside Registry")
        return {
            item.source_id
            for item in self.snapshot.bindings
            if item.surface_id in surface_ids and item.state is BindingState.CONFIRMED
        }

    def _closure(self, selected: set[str]) -> set[str]:
        result = set(selected)
        changed = True
        while changed:
            changed = False
            for edge in self.dependencies:
                if edge.source_id in result and edge.depends_on not in result:
                    if edge.depends_on not in self.documents:
                        _fail("dependency_missing")
                    result.add(edge.depends_on)
                    changed = True
        return result

    def _check_cycle(self, selected: set[str]) -> None:
        adjacency = {source_id: [] for source_id in selected}
        for edge in self.dependencies:
            if edge.source_id in selected and edge.depends_on in selected:
                adjacency[edge.source_id].append(edge.depends_on)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(source_id: str) -> None:
            if source_id in visiting:
                _fail("dependency_cycle")
            if source_id in visited:
                return
            visiting.add(source_id)
            for dependency in adjacency[source_id]:
                visit(dependency)
            visiting.remove(source_id)
            visited.add(source_id)

        for source_id in sorted(selected):
            visit(source_id)

    def _revision(self, source_id: str, production: set[str]) -> str:
        document = self.documents[source_id]
        if document.lifecycle_status is LifecycleStatus.UNCONFIRMED:
            _fail("lifecycle_unconfirmed")
        revision = document.released_revision if source_id in production else document.active_revision
        if revision is None:
            _fail("released_revision_missing" if source_id in production else "revision_changed")
        if self.snapshot.revision(source_id, revision).observation_state is not ObservationState.AVAILABLE:
            _fail("source_unavailable")
        return revision

    def resolve(self, request: BaselineRequest) -> BaselineResolution:
        request.validate()
        related = self._related(request.surface_ids)
        production = {
            source_id for source_id in related
            if self.documents[source_id].lifecycle_status is LifecycleStatus.RELEASED
        }
        production = self._closure(production)
        for source_id in production:
            if self.documents[source_id].lifecycle_status is not LifecycleStatus.RELEASED:
                _fail("dependency_missing")
        self._check_cycle(production)

        selected = set(production)
        explicit = set(request.explicit_target_source_ids)
        if request.baseline_mode is BaselineMode.TARGET_DESIGN:
            for source_id in explicit:
                if source_id not in self.documents:
                    _fail("dependency_missing")
                document = self.documents[source_id]
                if document.lifecycle_status is LifecycleStatus.UNCONFIRMED:
                    _fail("lifecycle_unconfirmed")
                if source_id not in related:
                    _fail("surface_binding_unconfirmed")
                if document.lifecycle_status is LifecycleStatus.WITHDRAWN:
                    _fail("source_unavailable")
            selected |= explicit
            selected = self._closure(selected)
            auto = selected - production - explicit
            if auto != set(request.confirmed_auto_dependency_source_ids):
                _fail("dependency_confirmation_required")
        else:
            auto = set()
        self._check_cycle(selected)

        applicable = tuple(
            item for item in self.snapshot.conflicts
            if item.surface_id in request.surface_ids and set(item.source_ids) <= selected
        )
        applied = []
        excluded: set[str] = set()
        requested_conflicts = set(request.conflict_resolution_ids)
        for conflict in applicable:
            if conflict.state is not BindingState.CONFIRMED:
                _fail("conflict_decision_required")
            if conflict.scope is ConflictScope.GLOBAL:
                if conflict.decision is not ConflictDecision.COMPATIBLE:
                    _fail("conflict_decision_unsupported")
                applied.append(conflict)
            else:
                if conflict.resolution_id not in requested_conflicts:
                    _fail("conflict_decision_required")
                if conflict.decision is ConflictDecision.SOURCE_WINS:
                    _fail("conflict_decision_unsupported")
                excluded.update(conflict.excluded_source_ids)
                applied.append(conflict)
        if requested_conflicts - {item.resolution_id for item in applicable}:
            raise ContractError("requested conflict is not applicable")
        for edge in self.dependencies:
            if edge.source_id in selected - excluded and edge.depends_on in excluded:
                _fail("excluded_dependency_required")
        selected -= excluded

        locked = {source_id: self._revision(source_id, production) for source_id in selected | excluded}
        expected = dict(request.expected_source_revisions)
        if set(expected) != set(locked) or any(expected[source_id] != revision for source_id, revision in locked.items()):
            _fail("revision_changed")
        if request.expected_registry_view_hash != self.snapshot.view_hash:
            _fail("registry_view_changed")

        documents = tuple(
            ResolvedDocument(
                source_id,
                locked[source_id],
                BaselineRole.PRODUCTION if source_id in production else BaselineRole.EXPLICIT_TARGET if source_id in explicit else BaselineRole.AUTO_DEPENDENCY,
                self.documents[source_id].lifecycle_status,
            )
            for source_id in sorted(selected)
        )
        dependency_edges = tuple(
            ResolvedDependency(edge.source_id, edge.depends_on)
            for edge in sorted(self.dependencies, key=lambda item: (item.source_id, item.depends_on))
            if edge.source_id in selected and edge.depends_on in selected
        )
        candidate_exclusions = set(excluded)
        if request.baseline_mode is BaselineMode.TARGET_DESIGN:
            candidate_exclusions |= {
                source_id for source_id in related - selected
                if self.documents[source_id].lifecycle_status in {LifecycleStatus.UNSCHEDULED, LifecycleStatus.IN_DEVELOPMENT}
            }
        conflicts = tuple(
            ResolvedConflict(item.resolution_id, item.scope, item.decision, item.excluded_source_ids)
            for item in sorted(applied, key=lambda value: value.resolution_id)
        )
        return BaselineResolution(
            request.task_label, request.baseline_mode, request.surface_ids,
            self.snapshot.view_hash, documents, dependency_edges,
            tuple(sorted(candidate_exclusions)), conflicts,
            request.created_by, request.created_at,
        )
