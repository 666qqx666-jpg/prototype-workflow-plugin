from __future__ import annotations

from enum import StrEnum
from pathlib import PurePath
from typing import Iterable

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, IDENTITY_RE, aware_timestamp
from enterprise_prd.errors import ContractError
from enterprise_prd.baseline_registry.contracts import LifecycleStatus, SURFACE_ID_RE
from .errors import TaskKnowledgeError


class TaskRole(StrEnum):
    PRODUCTION_BASELINE = "production_baseline"
    SELECTED_PREREQUISITE = "selected_prerequisite"
    REFERENCE_CAPABILITY = "reference_capability"


class RoleAuthority(StrEnum):
    BASELINE_RESOLVER = "baseline_resolver"
    PRODUCT_OWNER = "product_owner"


class TaskConflictDecision(StrEnum):
    PREREQUISITE_TAKES_EFFECT_AFTER_RELEASE = "prerequisite_takes_effect_after_release"
    PRODUCTION_BASELINE_REMAINS_AUTHORITATIVE = "production_baseline_remains_authoritative"
    REFERENCE_ONLY = "reference_only"
    EXCLUDED_FROM_TASK = "excluded_from_task"
    REQUIRES_PRODUCT_DECISION = "requires_product_decision"


ROLE_ORDER = {TaskRole.PRODUCTION_BASELINE: 0, TaskRole.SELECTED_PREREQUISITE: 1, TaskRole.REFERENCE_CAPABILITY: 2}
BODY_ROLES = frozenset({TaskRole.SELECTED_PREREQUISITE, TaskRole.REFERENCE_CAPABILITY})
_DRAFT = {"schema_version", "task", "selected_prerequisites", "reference_capabilities", "excluded_candidates", "conflict_decisions", "task_intent"}
_CONFIRMATION = {"schema_version", "preview_payload_sha256", "confirmed_by", "confirmed_at"}
_PREVIEW = {"schema_version", "preview_payload", "preview_payload_sha256"}
_MANIFEST = {"schema_version", "manifest_id", "task_id", "task_label", "target_surface_ids", "registry_view_hash", "production_baseline_ref", "documents", "dependency_edges", "conflict_decisions", "excluded_candidates", "task_intent", "confirmation"}
_INDEX = {"schema_version", "task_knowledge_manifest_id", "local_context_session_id", "local_context_selection_sha256", "bindings", "index_sha256"}
_INDEX_BINDING = {"source_id", "block_id", "raw_path", "normalized_path", "task_roles", "acquisition_scope"}
_SNAPSHOT = {"schema_version", "snapshot_id", "run_id", "enterprise_run_snapshot", "task_knowledge_manifest", "local_context", "role_section_index", "capability_pack", "source_sections", "created_at", "snapshot_sha256"}
_FILE_REFERENCE = {"path", "sha256"}
_LOCAL_CONTEXT_REFERENCE = {"context", "consumption_receipt"}
_CAPABILITY_PACK = {"path", "tree_sha256", "run_snapshot"}
_RUN_SNAPSHOT = {"channel", "skill_version", "pack_version", "manifest_sha256", "pair_path"}
_FORBIDDEN_KEYS = frozenset({"writer_prompt", "content", "body", "locator", "locator_ref", "url", "token", "outline", "title", "block_id", "raw_path", "normalized_path"})


def _exact(value: object, fields: set[str], reason: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ContractError(reason)
    return value


def _hash(value: object, field: str) -> str:
    if type(value) is not str or HASH_RE.fullmatch(value) is None:
        raise ContractError(f"{field} is invalid")
    return value


def _id(value: object, field: str) -> str:
    if type(value) is not str or IDENTITY_RE.fullmatch(value) is None:
        raise ContractError(f"{field} is invalid")
    return value


def _actor_timestamp(actor: object, timestamp: object, prefix: str) -> None:
    if type(actor) is not str or not actor.strip() or type(timestamp) is not str:
        raise ContractError(f"{prefix} confirmation is invalid")
    try:
        aware_timestamp(timestamp, f"{prefix} confirmed_at")
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{prefix} confirmation is invalid") from exc


def _forbidden(value: object) -> None:
    if type(value) is dict:
        if set(value) & _FORBIDDEN_KEYS:
            raise ContractError("task knowledge contains forbidden material")
        for item in value.values(): _forbidden(item)
    elif type(value) is list:
        for item in value: _forbidden(item)
    elif type(value) is str and ("http://" in value or "https://" in value or value.startswith("/") or "/Users/" in value):
        raise ContractError("task knowledge contains forbidden material")


def normalize_task_roles(values: Iterable[TaskRole | str]) -> tuple[TaskRole, ...]:
    try:
        roles = {value if type(value) is TaskRole else TaskRole(value) for value in values}
    except (TypeError, ValueError) as exc:
        raise ContractError("task roles are invalid") from exc
    if not roles: raise ContractError("task roles are empty")
    return tuple(sorted(roles, key=ROLE_ORDER.__getitem__))


def validate_role_lock(lifecycle: LifecycleStatus, roles: Iterable[TaskRole | str], revision: str, active_revision: str | None, released_revision: str | None) -> None:
    roles = frozenset(normalize_task_roles(roles))
    if TaskRole.PRODUCTION_BASELINE in roles and TaskRole.SELECTED_PREREQUISITE in roles: raise TaskKnowledgeError("invalid_role_lifecycle")
    if TaskRole.PRODUCTION_BASELINE in roles and (lifecycle is not LifecycleStatus.RELEASED or revision != released_revision): raise TaskKnowledgeError("invalid_role_lifecycle")
    if TaskRole.SELECTED_PREREQUISITE in roles and (lifecycle not in {LifecycleStatus.IN_DEVELOPMENT, LifecycleStatus.UNSCHEDULED} or revision != active_revision): raise TaskKnowledgeError("invalid_role_lifecycle")
    if TaskRole.REFERENCE_CAPABILITY in roles:
        expected = released_revision if lifecycle is LifecycleStatus.RELEASED else active_revision
        if lifecycle not in {LifecycleStatus.RELEASED, LifecycleStatus.IN_DEVELOPMENT, LifecycleStatus.UNSCHEDULED} or revision != expected: raise TaskKnowledgeError("invalid_role_lifecycle")


def _task_intent(value: object) -> dict:
    fields = {"goal", "user_inputs", "system_generated_fields", "mutability", "excluded_capabilities"}
    result = _exact(value, fields, "task intent fields are invalid")
    if type(result["goal"]) is not str or not result["goal"].strip() or type(result["user_inputs"]) is not list or type(result["system_generated_fields"]) is not list or type(result["excluded_capabilities"]) is not list: raise ContractError("task intent is invalid")
    if type(result["mutability"]) is not dict or set(result["mutability"]) != {"editable", "deletable"} or any(type(v) is not bool for v in result["mutability"].values()): raise ContractError("task intent is invalid")
    for item in result["user_inputs"]:
        _exact(item, {"field", "label", "required"}, "task intent input fields are invalid")
        if type(item["field"]) is not str or type(item["label"]) is not str or type(item["required"]) is not bool: raise ContractError("task intent input is invalid")
    for item in result["system_generated_fields"]:
        _exact(item, {"field", "label"}, "task intent system field fields are invalid")
        if type(item["field"]) is not str or type(item["label"]) is not str: raise ContractError("task intent system field is invalid")
    return result


def validate_draft(value: object) -> dict:
    value = _exact(value, _DRAFT, "draft root fields are invalid")
    if value["schema_version"] != 1: raise ContractError("draft schema_version is invalid")
    task = _exact(value["task"], {"task_id", "task_label", "target_surface_ids"}, "draft task fields are invalid")
    _id(task["task_id"], "task_id")
    if type(task["task_label"]) is not str or not task["task_label"].strip() or type(task["target_surface_ids"]) is not list or not task["target_surface_ids"] or task["target_surface_ids"] != sorted(task["target_surface_ids"]) or len(set(task["target_surface_ids"])) != len(task["target_surface_ids"]) or any(type(item) is not str or SURFACE_ID_RE.fullmatch(item) is None for item in task["target_surface_ids"]): raise ContractError("draft task is invalid")
    for key in ("selected_prerequisites", "reference_capabilities"):
        values = value[key]
        if type(values) is not list or any(type(item) is not dict or set(item) != {"source_id"} for item in values): raise ContractError(f"draft {key} are invalid")
        ids = [_id(item["source_id"], "source_id") for item in values]
        if ids != sorted(ids) or len(ids) != len(set(ids)): raise ContractError(f"draft {key} are invalid")
    if type(value["excluded_candidates"]) is not list or type(value["conflict_decisions"]) is not list: raise ContractError("draft decisions are invalid")
    _task_intent(value["task_intent"]); _forbidden(value)
    return value


def validate_confirmation(value: object) -> dict:
    value = _exact(value, _CONFIRMATION, "confirmation root fields are invalid")
    if value["schema_version"] != 1: raise ContractError("confirmation schema_version is invalid")
    _hash(value["preview_payload_sha256"], "confirmation preview hash"); _actor_timestamp(value["confirmed_by"], value["confirmed_at"], "confirmation")
    return value


def validate_preview(value: object) -> dict:
    value = _exact(value, _PREVIEW, "preview root fields are invalid")
    if value["schema_version"] != 1 or type(value["preview_payload"]) is not dict: raise ContractError("preview fields are invalid")
    _forbidden(value["preview_payload"]); _hash(value["preview_payload_sha256"], "preview hash")
    if sha256_bytes(canonical_json(value["preview_payload"])) != value["preview_payload_sha256"]: raise ContractError("preview payload hash differs")
    return value


def validate_manifest(value: object) -> dict:
    value = _exact(value, _MANIFEST, "manifest root fields are invalid")
    if value["schema_version"] != 1 or type(value["manifest_id"]) is not str or not value["manifest_id"].startswith("task-knowledge-") or HASH_RE.fullmatch(value["manifest_id"][15:]) is None: raise ContractError("manifest identity is invalid")
    _id(value["task_id"], "manifest task_id"); _hash(value["registry_view_hash"], "manifest registry hash"); _task_intent(value["task_intent"]); confirmation = validate_confirmation(value["confirmation"])
    payload = {k: v for k, v in value.items() if k not in {"schema_version", "manifest_id"}}
    if value["manifest_id"] != "task-knowledge-" + sha256_bytes(canonical_json({"schema_version": 1, **payload})): raise ContractError("manifest content hash differs")
    if type(value["target_surface_ids"]) is not list or value["target_surface_ids"] != sorted(value["target_surface_ids"]) or not value["target_surface_ids"]: raise ContractError("manifest surfaces are invalid")
    ref = _exact(value["production_baseline_ref"], {"manifest_id", "manifest_sha256"}, "manifest baseline reference fields are invalid")
    if type(ref["manifest_id"]) is not str or not ref["manifest_id"].startswith("baseline-"): raise ContractError("manifest baseline reference is invalid")
    _hash(ref["manifest_sha256"], "baseline manifest hash")
    docs = value["documents"]
    if type(docs) is not list or not docs or [item.get("source_id") for item in docs] != sorted(item.get("source_id") for item in docs): raise ContractError("manifest documents are invalid")
    for doc in docs:
        _exact(doc, {"source_id", "revision", "lifecycle_status_at_lock", "baseline_role", "task_roles"}, "manifest document fields are invalid")
        _id(doc["source_id"], "source_id"); _hash(doc["revision"], "revision")
        try: lifecycle = LifecycleStatus(doc["lifecycle_status_at_lock"]); roles = normalize_task_roles(tuple(role["role"] for role in doc["task_roles"]))
        except (ValueError, KeyError, TypeError) as exc: raise ContractError("manifest document role is invalid") from exc
        if (TaskRole.PRODUCTION_BASELINE in roles) != (doc["baseline_role"] == "production"): raise ContractError("manifest baseline role is invalid")
        for role in doc["task_roles"]:
            _exact(role, {"role", "authority", "confirmed_by", "confirmed_at"}, "manifest task role fields are invalid")
            try: item_role, authority = TaskRole(role["role"]), RoleAuthority(role["authority"])
            except ValueError as exc: raise ContractError("manifest task role is invalid") from exc
            if item_role is TaskRole.PRODUCTION_BASELINE:
                if authority is not RoleAuthority.BASELINE_RESOLVER or role["confirmed_by"] is not None or role["confirmed_at"] is not None: raise ContractError("manifest baseline authority is invalid")
            elif authority is not RoleAuthority.PRODUCT_OWNER or role["confirmed_by"] != confirmation["confirmed_by"] or role["confirmed_at"] != confirmation["confirmed_at"]: raise ContractError("manifest product authority is invalid")
        validate_role_lock(lifecycle, roles, doc["revision"], doc["revision"] if lifecycle is not LifecycleStatus.RELEASED else None, doc["revision"] if lifecycle is LifecycleStatus.RELEASED else None)
    _forbidden(value); return value


def validate_role_section_index(value: object) -> dict:
    value = _exact(value, _INDEX, "role section index fields are invalid")
    if value["schema_version"] != 1: raise ContractError("role section index schema_version is invalid")
    for key in ("task_knowledge_manifest_id", "local_context_session_id"):
        if type(value[key]) is not str or not value[key]: raise ContractError("role section index identity is invalid")
    for key in ("local_context_selection_sha256", "index_sha256"): _hash(value[key], key)
    bindings = value["bindings"]
    if type(bindings) is not list: raise ContractError("role section index bindings are invalid")
    identities: list[tuple[str, str]] = []
    for binding in bindings:
        binding = _exact(binding, _INDEX_BINDING, "role section binding fields are invalid")
        source_id = _id(binding["source_id"], "role section source_id")
        for key in ("block_id", "raw_path", "normalized_path", "acquisition_scope"):
            if type(binding[key]) is not str or not binding[key]: raise ContractError("role section binding is invalid")
        if type(binding["task_roles"]) is not list or not binding["task_roles"]:
            raise ContractError("role section binding roles are invalid")
        try:
            roles = tuple(TaskRole(item) for item in binding["task_roles"])
        except (TypeError, ValueError) as exc:
            raise ContractError("role section binding roles are invalid") from exc
        if set(roles) - BODY_ROLES or tuple(role.value for role in normalize_task_roles(roles)) != tuple(binding["task_roles"]):
            raise ContractError("role section binding roles are invalid")
        identities.append((source_id, binding["normalized_path"]))
    if identities != sorted(identities) or len(set(identities)) != len(identities):
        raise ContractError("role section index bindings are invalid")
    payload = {k: v for k, v in value.items() if k != "index_sha256"}
    if sha256_bytes(canonical_json(payload)) != value["index_sha256"]: raise ContractError("role section index hash differs")
    return value


def _safe_absolute_path(value: object, field: str) -> str:
    if type(value) is not str or not value or not PurePath(value).is_absolute() or ".." in PurePath(value).parts:
        raise ContractError(f"{field} is invalid")
    return value


def _file_reference(value: object, field: str) -> dict:
    value = _exact(value, _FILE_REFERENCE, f"{field} fields are invalid")
    _safe_absolute_path(value["path"], f"{field} path")
    _hash(value["sha256"], f"{field} hash")
    return value


def _source_section(value: object) -> dict:
    value = _exact(value, _INDEX_BINDING | {"revision"}, "source section fields are invalid")
    _id(value["source_id"], "source section source_id")
    _hash(value["revision"], "source section revision")
    for key in ("block_id", "raw_path", "normalized_path", "acquisition_scope"):
        if type(value[key]) is not str or not value[key]: raise ContractError("source section is invalid")
    if type(value["task_roles"]) is not list or not value["task_roles"]:
        raise ContractError("source section roles are invalid")
    try:
        roles = tuple(TaskRole(item) for item in value["task_roles"])
    except (TypeError, ValueError) as exc:
        raise ContractError("source section roles are invalid") from exc
    if set(roles) - BODY_ROLES or tuple(role.value for role in normalize_task_roles(roles)) != tuple(value["task_roles"]):
        raise ContractError("source section roles are invalid")
    return value


def _validate_writer_input_snapshot_shape(value: object) -> dict:
    value = _exact(value, _SNAPSHOT, "writer input snapshot fields are invalid")
    if value["schema_version"] != 1: raise ContractError("writer input snapshot schema_version is invalid")
    if type(value["snapshot_id"]) is not str or not value["snapshot_id"].startswith("writer-input-"):
        raise ContractError("writer input snapshot identity is invalid")
    _hash(value["snapshot_sha256"], "snapshot_sha256")
    if type(value["run_id"]) is not str or not value["run_id"]:
        raise ContractError("writer input run_id is invalid")
    _file_reference(value["enterprise_run_snapshot"], "enterprise run snapshot")
    _file_reference(value["task_knowledge_manifest"], "task knowledge manifest")
    local_context = _exact(value["local_context"], _LOCAL_CONTEXT_REFERENCE, "local context fields are invalid")
    _file_reference(local_context["context"], "local context")
    _file_reference(local_context["consumption_receipt"], "consumption receipt")
    _file_reference(value["role_section_index"], "role section index")
    pack = _exact(value["capability_pack"], _CAPABILITY_PACK, "capability pack fields are invalid")
    _safe_absolute_path(pack["path"], "capability pack path")
    _hash(pack["tree_sha256"], "capability pack tree hash")
    run_snapshot = _exact(pack["run_snapshot"], _RUN_SNAPSHOT, "capability pack run snapshot fields are invalid")
    for key in ("channel", "skill_version", "pack_version"):
        if type(run_snapshot[key]) is not str or not run_snapshot[key]: raise ContractError("capability pack run snapshot is invalid")
    _hash(run_snapshot["manifest_sha256"], "capability pack manifest hash")
    _safe_absolute_path(run_snapshot["pair_path"], "capability pack pair path")
    source_sections = value["source_sections"]
    if type(source_sections) is not list: raise ContractError("source sections are invalid")
    identities = []
    for section in source_sections:
        section = _source_section(section)
        identities.append((section["source_id"], section["normalized_path"]))
    if identities != sorted(identities) or len(set(identities)) != len(identities):
        raise ContractError("source sections are invalid")
    _actor_timestamp("fixture", value["created_at"], "writer input")
    return value


def writer_input_canonical_payload(value: object) -> dict:
    value = _validate_writer_input_snapshot_shape(value)
    return {key: item for key, item in value.items() if key not in {"snapshot_id", "snapshot_sha256"}}


def writer_input_digest(value: object) -> str:
    return sha256_bytes(canonical_json(writer_input_canonical_payload(value)))


def validate_writer_input_snapshot(value: object) -> dict:
    value = _validate_writer_input_snapshot_shape(value)
    digest = writer_input_digest(value)
    if value["snapshot_id"] != "writer-input-" + digest or value["snapshot_sha256"] != digest: raise ContractError("writer input snapshot hash differs")
    return value
