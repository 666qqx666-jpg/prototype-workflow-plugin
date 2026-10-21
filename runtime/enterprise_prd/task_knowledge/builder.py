from __future__ import annotations

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.baseline_registry.contracts import BindingState, ConflictDecision, ConflictScope, DependencyState, LifecycleStatus, ObservationState
from enterprise_prd.baseline_registry.manifest import build_manifest as build_baseline_manifest, manifest_bytes, validate_manifest as validate_baseline_manifest
from enterprise_prd.baseline_registry.resolver import BaselineMode, BaselineRequest, BaselineResolver

from .contracts import RoleAuthority, TaskConflictDecision, TaskRole, normalize_task_roles, validate_confirmation, validate_draft, validate_manifest, validate_preview, validate_role_lock
from .errors import TaskKnowledgeError


def resolve_production_baseline(snapshot, *, task_label: str, surface_ids: tuple[str, ...], created_by: str, created_at: str) -> dict:
    source_ids = sorted({binding.source_id for binding in snapshot.bindings if binding.surface_id in surface_ids and binding.state is BindingState.CONFIRMED and snapshot.document(binding.source_id).lifecycle_status is LifecycleStatus.RELEASED})
    revisions = tuple((source_id, snapshot.document(source_id).released_revision) for source_id in source_ids)
    request = BaselineRequest(task_label=task_label, baseline_mode=BaselineMode.PRODUCTION, surface_ids=tuple(sorted(surface_ids)), explicit_target_source_ids=(), confirmed_auto_dependency_source_ids=(), conflict_resolution_ids=(), expected_registry_view_hash=snapshot.view_hash, expected_source_revisions=revisions, created_by=created_by, created_at=created_at)
    return build_baseline_manifest(BaselineResolver(snapshot).resolve(request))


def roles_from_baseline_and_draft(baseline: dict, draft: dict) -> dict[str, tuple[TaskRole, ...]]:
    roles: dict[str, set[TaskRole]] = {}
    for document in baseline["documents"]: roles.setdefault(document["source_id"], set()).add(TaskRole.PRODUCTION_BASELINE)
    for item in draft["selected_prerequisites"]: roles.setdefault(item["source_id"], set()).add(TaskRole.SELECTED_PREREQUISITE)
    for item in draft["reference_capabilities"]: roles.setdefault(item["source_id"], set()).add(TaskRole.REFERENCE_CAPABILITY)
    return {source_id: normalize_task_roles(values) for source_id, values in sorted(roles.items())}


def resolve_documents(snapshot, baseline: dict, roles_by_source: dict[str, tuple[TaskRole, ...]]) -> list[dict]:
    baseline_by_source = {item["source_id"]: item for item in baseline["documents"]}
    output = []
    for source_id, roles in roles_by_source.items():
        try: document = snapshot.document(source_id)
        except Exception as exc: raise TaskKnowledgeError("unknown_source") from exc
        revision = document.released_revision if document.lifecycle_status is LifecycleStatus.RELEASED else document.active_revision
        if revision is None: raise TaskKnowledgeError("source_revision_drift")
        validate_role_lock(document.lifecycle_status, roles, revision, document.active_revision, document.released_revision)
        if snapshot.revision(source_id, revision).observation_state is not ObservationState.AVAILABLE: raise TaskKnowledgeError("source_unavailable")
        bindings = [x for x in snapshot.bindings if x.source_id == source_id and x.surface_id in baseline["surface_ids"]]
        if len(bindings) != len(baseline["surface_ids"]) or any(x.state is not BindingState.CONFIRMED or x.based_on_revision != revision for x in bindings): raise TaskKnowledgeError("surface_binding_unconfirmed")
        baseline_item = baseline_by_source.get(source_id)
        if baseline_item and (baseline_item["revision"] != revision or baseline_item["role"] != "production"): raise TaskKnowledgeError("baseline_tampered")
        output.append({"source_id": source_id, "revision": revision, "lifecycle_status_at_lock": document.lifecycle_status.value, "baseline_role": "production" if baseline_item else None, "task_roles": [{"role": role.value, "authority": (RoleAuthority.BASELINE_RESOLVER if role is TaskRole.PRODUCTION_BASELINE else RoleAuthority.PRODUCT_OWNER).value} for role in roles]})
    return output


def resolve_relevant_dependencies(snapshot, documents: list[dict], excluded: list[dict]) -> list[dict]:
    ids = {item["source_id"] for item in documents}; excluded_ids = {item.get("source_id") for item in excluded}
    for edge in snapshot.dependencies:
        if edge.state is DependencyState.CONFIRMED and edge.source_id in ids and edge.depends_on not in ids:
            raise TaskKnowledgeError("dependency_required")
        if edge.state is DependencyState.CONFIRMED and edge.source_id in ids and edge.depends_on in excluded_ids: raise TaskKnowledgeError("dependency_required")
    result = [{"source_id": e.source_id, "depends_on": e.depends_on} for e in snapshot.dependencies if e.state is DependencyState.CONFIRMED and e.source_id in ids and e.depends_on in ids]
    graph = {source_id: [] for source_id in ids}
    for edge in result: graph[edge["source_id"]].append(edge["depends_on"])
    active, seen = set(), set()
    def visit(source_id: str) -> None:
        if source_id in active: raise TaskKnowledgeError("dependency_cycle")
        if source_id in seen: return
        active.add(source_id)
        for target in graph[source_id]: visit(target)
        active.remove(source_id); seen.add(source_id)
    for source_id in sorted(graph): visit(source_id)
    return sorted(result, key=lambda item: (item["source_id"], item["depends_on"]))


def _registry_conflict_mapping(conflict) -> dict:
    return {
        "resolution_id": conflict.resolution_id,
        "surface_id": conflict.surface_id,
        "source_ids": list(conflict.source_ids),
        "scope": conflict.scope.value,
        "decision": conflict.decision.value,
        "winner_source_id": conflict.winner_source_id,
        "excluded_source_ids": list(conflict.excluded_source_ids),
        "state": conflict.state.value,
        "confirmed_by": conflict.confirmed_by,
        "confirmed_at": conflict.confirmed_at,
        "record_version": conflict.record_version,
    }


def _task_decisions_by_resolution(decisions: list[dict], applicable_ids: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in decisions:
        if type(item) is not dict or set(item) != {"resolution_id", "decision"}:
            raise TaskKnowledgeError("unresolved_conflict")
        resolution_id, decision = item["resolution_id"], item["decision"]
        if type(resolution_id) is not str or resolution_id not in applicable_ids or resolution_id in result:
            raise TaskKnowledgeError("unresolved_conflict")
        try:
            task_decision = TaskConflictDecision(decision)
        except (TypeError, ValueError) as exc:
            raise TaskKnowledgeError("unresolved_conflict") from exc
        if task_decision is TaskConflictDecision.REQUIRES_PRODUCT_DECISION:
            raise TaskKnowledgeError("unresolved_conflict")
        result[resolution_id] = task_decision.value
    return result


def resolve_relevant_conflicts(snapshot, documents: list[dict], decisions: list[dict]) -> list[dict]:
    ids = {item["source_id"] for item in documents}
    surface_ids = {binding.surface_id for binding in snapshot.bindings if binding.source_id in ids}
    applicable = [
        item for item in snapshot.conflicts
        if item.state is BindingState.CONFIRMED and item.surface_id in surface_ids and set(item.source_ids) <= ids
    ]
    choices = _task_decisions_by_resolution(decisions, {item.resolution_id for item in applicable})
    result = []
    for item in applicable:
        registry = _registry_conflict_mapping(item)
        if item.scope is ConflictScope.GLOBAL and item.decision is ConflictDecision.COMPATIBLE:
            if item.resolution_id in choices:
                raise TaskKnowledgeError("unresolved_conflict")
            result.append(registry)
            continue
        if item.scope is ConflictScope.GLOBAL or item.resolution_id not in choices:
            raise TaskKnowledgeError("unresolved_conflict")
        result.append({**registry, "task_decision": choices[item.resolution_id]})
    return sorted(result, key=lambda item: item["resolution_id"])


def canonical_preview_payload(snapshot, baseline: dict, draft: dict, documents: list[dict], dependencies: list[dict], conflicts: list[dict]) -> dict:
    return {"task_id": draft["task"]["task_id"], "task_label": draft["task"]["task_label"], "target_surface_ids": draft["task"]["target_surface_ids"], "registry_view_hash": snapshot.view_hash, "production_baseline_ref": {"manifest_id": baseline["manifest_id"], "manifest_sha256": sha256_bytes(manifest_bytes(baseline))}, "documents": documents, "dependency_edges": dependencies, "conflict_decisions": conflicts, "excluded_candidates": draft["excluded_candidates"], "task_intent": draft["task_intent"]}


def build_preview(snapshot, baseline: dict, draft: dict) -> dict:
    checked = validate_draft(draft); validate_baseline_manifest(baseline)
    if baseline["registry_view_hash"] != snapshot.view_hash: raise TaskKnowledgeError("registry_view_drift")
    if tuple(baseline["surface_ids"]) != tuple(checked["task"]["target_surface_ids"]): raise TaskKnowledgeError("baseline_tampered")
    documents = resolve_documents(snapshot, baseline, roles_from_baseline_and_draft(baseline, checked))
    dependencies = resolve_relevant_dependencies(snapshot, documents, checked["excluded_candidates"])
    conflicts = resolve_relevant_conflicts(snapshot, documents, checked["conflict_decisions"])
    payload = canonical_preview_payload(snapshot, baseline, checked, documents, dependencies, conflicts)
    value = {"schema_version": 1, "preview_payload": payload, "preview_payload_sha256": sha256_bytes(canonical_json(payload))}
    validate_preview(value); return value


def manifest_payload(payload: dict, confirmation: dict) -> dict:
    documents = []
    for document in payload["documents"]:
        roles = []
        for role in document["task_roles"]:
            is_baseline = role["role"] == TaskRole.PRODUCTION_BASELINE.value
            roles.append({**role, "confirmed_by": None if is_baseline else confirmation["confirmed_by"], "confirmed_at": None if is_baseline else confirmation["confirmed_at"]})
        documents.append({**document, "task_roles": roles})
    conflicts = [dict(item) for item in payload["conflict_decisions"]]
    excluded = [{**item, "confirmed_by": confirmation["confirmed_by"], "confirmed_at": confirmation["confirmed_at"]} for item in payload["excluded_candidates"]]
    return {**payload, "documents": documents, "conflict_decisions": conflicts, "excluded_candidates": excluded, "confirmation": confirmation}


def build_manifest(preview: dict, confirmation: dict) -> dict:
    validate_preview(preview); confirmation = validate_confirmation(confirmation)
    if confirmation["preview_payload_sha256"] != preview["preview_payload_sha256"]: raise TaskKnowledgeError("confirmed_payload_drift")
    payload = manifest_payload(preview["preview_payload"], confirmation)
    digest = sha256_bytes(canonical_json({"schema_version": 1, **payload}))
    value = {"schema_version": 1, "manifest_id": f"task-knowledge-{digest}", **payload}
    validate_manifest(value); return value
