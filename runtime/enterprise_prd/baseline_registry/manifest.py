from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, IDENTITY_RE, aware_timestamp
from enterprise_prd.errors import ContractError

from .contracts import BaselineMode, BaselineRole, ConflictDecision, ConflictScope, LifecycleStatus, RESOLUTION_ID_RE, SURFACE_ID_RE
from .errors import ImmutableManifestConflict
from .resolver import BaselineResolution


ROOT_FIELDS = {
    "schema_version", "manifest_id", "task_label", "baseline_mode", "surface_ids",
    "registry_view_hash", "documents", "dependency_edges",
    "excluded_candidate_source_ids", "conflict_decisions", "created_by", "created_at",
}
DOCUMENT_FIELDS = {"source_id", "revision", "role", "lifecycle_status_at_lock"}
DEPENDENCY_FIELDS = {"source_id", "depends_on"}
CONFLICT_FIELDS = {"resolution_id", "scope", "decision", "excluded_source_ids"}
FORBIDDEN = ("locator", "/Users/", "https://", "http://", "slm-followup-records")


def _payload(resolution: BaselineResolution) -> dict:
    return {
        "schema_version": 1,
        "task_label": resolution.task_label,
        "baseline_mode": resolution.baseline_mode.value,
        "surface_ids": list(resolution.surface_ids),
        "registry_view_hash": resolution.registry_view_hash,
        "documents": [
            {
                "source_id": item.source_id,
                "revision": item.revision,
                "role": item.role.value,
                "lifecycle_status_at_lock": item.lifecycle_status_at_lock.value,
            }
            for item in resolution.documents
        ],
        "dependency_edges": [
            {"source_id": item.source_id, "depends_on": item.depends_on}
            for item in resolution.dependency_edges
        ],
        "excluded_candidate_source_ids": list(resolution.excluded_candidate_source_ids),
        "conflict_decisions": [
            {
                "resolution_id": item.resolution_id,
                "scope": item.scope.value,
                "decision": item.decision.value,
                "excluded_source_ids": list(item.excluded_source_ids),
            }
            for item in resolution.conflict_decisions
        ],
        "created_by": resolution.created_by,
        "created_at": resolution.created_at,
    }


def build_manifest(resolution: BaselineResolution) -> dict:
    payload = _payload(resolution)
    digest = sha256_bytes(canonical_json(payload))
    value = {"schema_version": 1, "manifest_id": f"baseline-{digest}", **{key: value for key, value in payload.items() if key != "schema_version"}}
    validate_manifest(value)
    return value


def validate_manifest(value: object) -> None:
    if type(value) is not dict or set(value) != ROOT_FIELDS or value["schema_version"] != 1:
        raise ContractError("manifest root fields are invalid")
    if type(value["task_label"]) is not str or not value["task_label"].strip() or type(value["created_by"]) is not str or not value["created_by"].strip():
        raise ContractError("manifest actor fields are invalid")
    aware_timestamp(value["created_at"], "manifest created_at")
    manifest_id = value["manifest_id"]
    if type(manifest_id) is not str or not manifest_id.startswith("baseline-") or HASH_RE.fullmatch(manifest_id[9:]) is None:
        raise ContractError("manifest_id is invalid")
    payload = {"schema_version": 1, **{key: item for key, item in value.items() if key not in {"schema_version", "manifest_id"}}}
    if manifest_id != "baseline-" + sha256_bytes(canonical_json(payload)):
        raise ContractError("manifest content hash differs")
    if type(value["registry_view_hash"]) is not str or HASH_RE.fullmatch(value["registry_view_hash"]) is None:
        raise ContractError("manifest Registry Hash is invalid")
    surface_ids = value["surface_ids"]
    if type(surface_ids) is not list or not surface_ids or any(type(item) is not str for item in surface_ids):
        raise ContractError("manifest surfaces are invalid")
    if surface_ids != sorted(surface_ids) or len(surface_ids) != len(set(surface_ids)) or any(SURFACE_ID_RE.fullmatch(item) is None for item in surface_ids):
        raise ContractError("manifest surfaces are invalid")
    rendered = canonical_json(value).decode("utf-8")
    if any(marker in rendered for marker in FORBIDDEN):
        raise ContractError("manifest contains forbidden material")
    documents = value["documents"]
    if type(documents) is not list or not documents or any(type(item) is not dict or set(item) != DOCUMENT_FIELDS for item in documents):
        raise ContractError("manifest documents are invalid")
    if any(type(item["source_id"]) is not str or type(item["revision"]) is not str or type(item["role"]) is not str or type(item["lifecycle_status_at_lock"]) is not str for item in documents):
        raise ContractError("manifest document values are invalid")
    if [item["source_id"] for item in documents] != sorted(item["source_id"] for item in documents):
        raise ContractError("manifest documents are not canonical")
    if len({item["source_id"] for item in documents}) != len(documents) or any(IDENTITY_RE.fullmatch(item["source_id"]) is None for item in documents):
        raise ContractError("manifest document identities are invalid")
    try:
        BaselineMode(value["baseline_mode"])
        for item in documents:
            BaselineRole(item["role"])
            LifecycleStatus(item["lifecycle_status_at_lock"])
            if HASH_RE.fullmatch(item["revision"]) is None:
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise ContractError("manifest enum or revision is invalid") from exc
    excluded_values = value["excluded_candidate_source_ids"]
    if type(excluded_values) is not list or any(type(item) is not str for item in excluded_values):
        raise ContractError("manifest exclusions are invalid")
    if excluded_values != sorted(excluded_values) or len(excluded_values) != len(set(excluded_values)) or any(IDENTITY_RE.fullmatch(item) is None for item in excluded_values):
        raise ContractError("manifest exclusions are invalid")
    excluded = set(excluded_values)
    included = {item["source_id"] for item in documents}
    if excluded & included:
        raise ContractError("manifest includes an excluded source")
    dependencies = value["dependency_edges"]
    if type(dependencies) is not list or any(type(item) is not dict or set(item) != DEPENDENCY_FIELDS for item in dependencies):
        raise ContractError("manifest dependencies are invalid")
    if any(item["source_id"] not in included or item["depends_on"] not in included for item in dependencies):
        raise ContractError("manifest dependency reference is invalid")
    conflicts = value["conflict_decisions"]
    if type(conflicts) is not list or any(type(item) is not dict or set(item) != CONFLICT_FIELDS for item in conflicts):
        raise ContractError("manifest conflicts are invalid")
    try:
        for item in conflicts:
            if type(item["resolution_id"]) is not str or type(item["scope"]) is not str or type(item["decision"]) is not str:
                raise ValueError
            scope = ConflictScope(item["scope"])
            decision = ConflictDecision(item["decision"])
            if RESOLUTION_ID_RE.fullmatch(item["resolution_id"]) is None:
                raise ValueError
            if type(item["excluded_source_ids"]) is not list or any(type(source_id) is not str for source_id in item["excluded_source_ids"]):
                raise ValueError
            if item["excluded_source_ids"] != sorted(item["excluded_source_ids"]):
                raise ValueError
            if decision is ConflictDecision.COMPATIBLE and item["excluded_source_ids"]:
                raise ValueError
            if decision is ConflictDecision.EXCLUDE and (scope is not ConflictScope.TASK or not item["excluded_source_ids"]):
                raise ValueError
            if decision is ConflictDecision.SOURCE_WINS:
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise ContractError("manifest conflict enum is invalid") from exc
    conflict_excluded = {
        source_id
        for item in conflicts
        for source_id in item["excluded_source_ids"]
    }
    if not conflict_excluded <= excluded:
        raise ContractError("manifest conflict exclusion is not recorded")


def manifest_bytes(value: dict) -> bytes:
    validate_manifest(value)
    return canonical_json(value) + b"\n"


class ManifestStore:
    def __init__(self, root: Path) -> None:
        if root.exists() and root.is_symlink():
            raise ContractError("manifest root cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()
        self.directory = self.root / "manifests"
        if self.directory.exists() and self.directory.is_symlink():
            raise ContractError("manifest directory cannot be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.resolve().parent != self.root:
            raise ContractError("manifest directory escapes root")

    def save(self, value: dict) -> Path:
        data = manifest_bytes(value)
        path = self.directory / f"{value['manifest_id']}.json"
        if path.exists():
            if path.is_symlink() or path.read_bytes() != data:
                raise ImmutableManifestConflict("manifest bytes conflict")
            return path
        descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, data)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != data:
                    raise ImmutableManifestConflict("manifest bytes conflict")
            directory = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return path
        finally:
            if descriptor != -1:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)
