from __future__ import annotations
from dataclasses import dataclass
from enterprise_prd.errors import ContractError
from enterprise_prd.baseline_registry.contracts import LifecycleStatus
from enterprise_prd.local_context.contracts import DirectoryEntry
from .contracts import BODY_ROLES, TaskRole, normalize_task_roles, validate_manifest
from .errors import TaskKnowledgeError

@dataclass(frozen=True)
class ManifestDirectoryView:
    manifest_id: str; registry_view_hash: str; entries: tuple[DirectoryEntry, ...]; expected_revisions: dict[str, str]; task_roles: dict[str, tuple[TaskRole, ...]]
    def list_entries(self) -> tuple[DirectoryEntry, ...]: return self.entries
    def resolve(self, source_id: str) -> DirectoryEntry:
        found = tuple(item for item in self.entries if item.source_id == source_id)
        if len(found) != 1: raise ContractError("source is outside manifest directory")
        return found[0]
    def to_mapping(self) -> dict:
        return {"schema_version": 1, "task_knowledge_manifest_id": self.manifest_id, "registry_view_hash": self.registry_view_hash, "entries": [{"source_id": entry.source_id, "display_name": entry.display_name, "adapter": entry.adapter, "locator_ref": entry.locator_ref, "expected_revision": self.expected_revisions[entry.source_id], "task_roles": [role.value for role in self.task_roles[entry.source_id]]} for entry in self.entries]}

def build_manifest_directory(snapshot, manifest: dict) -> ManifestDirectoryView:
    validate_manifest(manifest)
    if manifest["registry_view_hash"] != snapshot.view_hash: raise TaskKnowledgeError("registry_view_drift")
    entries, revisions, roles_by_source = [], {}, {}
    for item in manifest["documents"]:
        roles = normalize_task_roles(role["role"] for role in item["task_roles"])
        if not set(roles) & BODY_ROLES: continue
        document = snapshot.document(item["source_id"])
        revision = document.released_revision if document.lifecycle_status is LifecycleStatus.RELEASED else document.active_revision
        if document.lifecycle_status.value != item["lifecycle_status_at_lock"] or revision != item["revision"]: raise TaskKnowledgeError("source_revision_drift")
        entries.append(DirectoryEntry(document.source_id, document.display_name, document.adapter, document.locator_ref)); revisions[document.source_id] = revision; roles_by_source[document.source_id] = roles
    entries.sort(key=lambda entry: entry.source_id)
    return ManifestDirectoryView(manifest["manifest_id"], snapshot.view_hash, tuple(entries), revisions, roles_by_source)
