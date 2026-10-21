from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager
import fcntl
import json
import os
from enterprise_prd.canonical import canonical_json, sha256_bytes, sha256_file
from enterprise_prd.contracts import runtime_id
from enterprise_prd.local_context.contracts import AcquisitionScope, RequestedSection
from enterprise_prd.local_context.workflow import LocalContextWorkflow
from enterprise_prd.local_context.grant import ReadGrantService
from enterprise_prd.local_context.assembler import ContextAssembler
from .directory import build_manifest_directory
from .contracts import BODY_ROLES, normalize_task_roles, validate_role_section_index
from .errors import TaskKnowledgeError
from .store import exclusive_private_json


_TASK_GRANT_FIELDS = {
    "schema_version", "task_knowledge_manifest_id", "local_context_session_id",
    "local_context_selection_sha256", "role_section_index_sha256",
    "local_context_grant_path", "local_context_grant_sha256", "sources",
    "task_knowledge_grant_sha256",
}

@dataclass(frozen=True)
class RoleSectionResult:
    role_section_index_path: Path

@contextmanager
def bridge_session_lock(runtime_root: Path, session_id: str):
    checked = runtime_id(session_id, "session_id")
    root = runtime_root / ".task-knowledge-locks"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink(): raise TaskKnowledgeError("task_knowledge_lock_invalid")
    path = root / f"{checked}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX); yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)

class TaskKnowledgeBridge:
    def __init__(self, runtime_root: Path, snapshot, manifest: dict, readers: dict, *, grant_id_factory=None) -> None:
        self.runtime_root = Path(runtime_root).expanduser().resolve(); self.snapshot = snapshot; self.manifest = manifest; self.readers = readers; self.grant_id_factory = grant_id_factory
        self.directory = build_manifest_directory(snapshot, manifest); self.workflow = LocalContextWorkflow(self.runtime_root, self.directory, readers)
    def _workflow_for(self, session_id: str) -> LocalContextWorkflow:
        return LocalContextWorkflow(self.runtime_root, self.directory, self.readers, id_factory=lambda: session_id)
    def prepare_outlines(self, session_id: str) -> dict:
        runtime_id(session_id, "session_id"); self.workflow = self._workflow_for(session_id)
        candidates = tuple((entry.source_id, "+".join(role.value for role in self.directory.task_roles[entry.source_id])) for entry in self.directory.entries)
        try: self.workflow.create(self.manifest["task_intent"]["goal"], candidates)
        except Exception:
            state = self.workflow.load(session_id)
            if state["status"] != "created": raise
        session = self.runtime_root / session_id; exclusive_private_json(session / "task-directory.json", self.directory.to_mapping(), "directory_scope_expanded")
        state = self.workflow.load(session_id)
        if state["status"] == "created": self.workflow.confirm_documents(session_id, tuple(entry.source_id for entry in self.directory.entries))
        state = self.workflow.collect_outlines(session_id); actual = {item["source_id"]: item["revision"] for item in state["outlines"]}
        if actual != self.directory.expected_revisions: self.workflow.cancel(session_id); raise TaskKnowledgeError("source_revision_drift")
        return state
    def confirm_sections(self, session_id: str, requests: tuple[dict, ...]) -> dict:
        parsed = tuple(RequestedSection(item["source_id"], item["raw_path"], AcquisitionScope(item["acquisition_scope"])) for item in requests)
        if {item.source_id for item in parsed} != set(self.directory.expected_revisions): raise TaskKnowledgeError("section_role_mismatch")
        return self.workflow.confirm_sections(session_id, parsed)
    def confirm_role_sections(self, session_id: str, value: dict) -> RoleSectionResult:
        if type(value) is not dict or set(value) != {"schema_version", "selections"} or value["schema_version"] != 1 or type(value["selections"]) is not list: raise TaskKnowledgeError("role_section_binding_invalid")
        with bridge_session_lock(self.runtime_root, session_id):
            state = self.workflow.load(session_id)
            if state["status"] != "outlines_ready": raise TaskKnowledgeError("role_section_binding_invalid")
            headings = {(outline["source_id"], heading["normalized_path"]): heading for outline in state["outlines"] for heading in outline["headings"]}
            bindings = []; requests = []
            for item in value["selections"]:
                if type(item) is not dict or set(item) != {"source_id", "normalized_path", "task_roles", "acquisition_scope"}: raise TaskKnowledgeError("role_section_binding_invalid")
                source_id, path = item["source_id"], item["normalized_path"]
                heading = headings.get((source_id, path))
                if heading is None or source_id not in self.directory.task_roles: raise TaskKnowledgeError("role_section_binding_invalid")
                roles = tuple(item["task_roles"])
                try: normalized_roles = normalize_task_roles(roles)
                except Exception as exc: raise TaskKnowledgeError("role_section_binding_invalid") from exc
                if not roles or set(normalized_roles) - BODY_ROLES or not set(normalized_roles) <= set(self.directory.task_roles[source_id]): raise TaskKnowledgeError("role_section_binding_invalid")
                requests.append({"source_id": source_id, "raw_path": heading["raw_path"], "acquisition_scope": item["acquisition_scope"]})
                bindings.append({"source_id": source_id, "block_id": heading["block_id"], "raw_path": heading["raw_path"], "normalized_path": path, "task_roles": [role.value for role in normalized_roles], "acquisition_scope": item["acquisition_scope"]})
            if {item["source_id"] for item in requests} != set(self.directory.expected_revisions) or len({(item["source_id"], item["raw_path"]) for item in requests}) != len(requests): raise TaskKnowledgeError("role_section_binding_invalid")
            projected = []
            for request in requests:
                heading = next(heading for (source_id, _), heading in headings.items() if source_id == request["source_id"] and heading["raw_path"] == request["raw_path"])
                projected.append({"source_id": request["source_id"], "purpose": "+".join(role.value for role in self.directory.task_roles[request["source_id"]]), "block_id": heading["block_id"], "raw_path": heading["raw_path"], "normalized_path": heading["normalized_path"], "heading_level": heading["level"], "acquisition_scope": request["acquisition_scope"]})
            payload = {"schema_version": 1, "task_knowledge_manifest_id": self.manifest["manifest_id"], "local_context_session_id": session_id, "local_context_selection_sha256": sha256_bytes(canonical_json(projected)), "bindings": sorted(bindings, key=lambda item: (item["source_id"], item["normalized_path"]))}
            index = {**payload, "index_sha256": sha256_bytes(canonical_json(payload))}
            try: validate_role_section_index(index)
            except Exception as exc: raise TaskKnowledgeError("role_section_binding_invalid") from exc
            index_path = self.runtime_root / session_id / "role-section-index.json"
            exclusive_private_json(index_path, index, "role_section_binding_invalid")
            self.confirm_sections(session_id, tuple(requests))
            if sha256_bytes(canonical_json(self.workflow.load(session_id)["selections"])) != index["local_context_selection_sha256"]: raise TaskKnowledgeError("role_section_binding_invalid")
            return RoleSectionResult(index_path)

    def _load_index(self, session_id: str, path: Path) -> dict:
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
            expected = self.runtime_root / session_id / "role-section-index.json"
            if resolved != expected or Path(path).is_symlink() or not resolved.is_file(): raise ValueError
            index = json.loads(resolved.read_text(encoding="utf-8"))
            validate_role_section_index(index)
        except Exception as exc:
            raise TaskKnowledgeError("role_section_binding_invalid") from exc
        return index

    def _require_index_matches_state(self, session_id: str, path: Path) -> tuple[dict, dict]:
        index = self._load_index(session_id, path); state = self.workflow.load(session_id)
        if index["task_knowledge_manifest_id"] != self.manifest["manifest_id"] or index["local_context_session_id"] != session_id or index["local_context_selection_sha256"] != sha256_bytes(canonical_json(state["selections"])):
            raise TaskKnowledgeError("role_section_binding_invalid")
        headings = {(outline["source_id"], heading["normalized_path"]): heading for outline in state["outlines"] for heading in outline["headings"]}
        expected = {(selection["source_id"], selection["normalized_path"]): selection for selection in state["selections"]}
        actual = {(binding["source_id"], binding["normalized_path"]): binding for binding in index["bindings"]}
        if set(actual) != set(expected): raise TaskKnowledgeError("role_section_binding_invalid")
        for key, selection in expected.items():
            binding = actual[key]; heading = headings.get(key)
            if heading is None or any(binding[field] != selection[field] for field in ("source_id", "block_id", "raw_path", "normalized_path", "acquisition_scope")):
                raise TaskKnowledgeError("role_section_binding_invalid")
            try: roles = normalize_task_roles(binding["task_roles"])
            except Exception as exc: raise TaskKnowledgeError("role_section_binding_invalid") from exc
            if not set(roles) <= set(self.directory.task_roles[selection["source_id"]]): raise TaskKnowledgeError("role_section_binding_invalid")
        return index, state

    def _validate_grant_against_index(self, grant: dict, index: dict, state: dict) -> None:
        bindings = {(item["source_id"], item["normalized_path"]): item for item in index["bindings"]}
        outlines = {item["source_id"]: item for item in state["outlines"]}
        actual: dict[tuple[str, str], tuple[str, str, str]] = {}
        for source in grant["sources"]:
            outline = outlines.get(source["source_id"])
            if outline is None or source["revision"] != outline["revision"]: raise TaskKnowledgeError("role_section_binding_invalid")
            for section in source["sections"]:
                actual[(source["source_id"], section["normalized_path"])] = (section["raw_path"], source["acquisition_scope"], source["revision"])
        if set(actual) != set(bindings): raise TaskKnowledgeError("role_section_binding_invalid")
        for key, binding in bindings.items():
            raw_path, scope, _ = actual[key]
            if raw_path != binding["raw_path"] or scope != binding["acquisition_scope"]: raise TaskKnowledgeError("role_section_binding_invalid")

    def _load_task_grant(self, path: Path) -> dict:
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
            if resolved.name != "task-knowledge-grant.json" or resolved.parent.parent != self.runtime_root or Path(path).is_symlink() or not resolved.is_file(): raise ValueError
            value = json.loads(resolved.read_text(encoding="utf-8"))
            if type(value) is not dict or set(value) != _TASK_GRANT_FIELDS or value["schema_version"] != 1: raise ValueError
            payload = {key: item for key, item in value.items() if key != "task_knowledge_grant_sha256"}
            if value["task_knowledge_grant_sha256"] != sha256_bytes(canonical_json(payload)): raise ValueError
        except Exception as exc:
            raise TaskKnowledgeError("role_section_binding_invalid") from exc
        return value

    def issue_grant(self, session_id: str, index_path: Path) -> Path:
        if index_path is None: raise TaskKnowledgeError("role_section_binding_invalid")
        with bridge_session_lock(self.runtime_root, session_id):
            index, state = self._require_index_matches_state(session_id, index_path)
            grants = ReadGrantService(self.runtime_root, self.workflow, id_factory=self.grant_id_factory)
            local_grant_path = grants.issue(session_id)
            local_grant = grants.load(local_grant_path)
            self._validate_grant_against_index(local_grant, index, state)
            payload = {
                "schema_version": 1,
                "task_knowledge_manifest_id": self.manifest["manifest_id"],
                "local_context_session_id": session_id,
                "local_context_selection_sha256": index["local_context_selection_sha256"],
                "role_section_index_sha256": index["index_sha256"],
                "local_context_grant_path": str(local_grant_path),
                "local_context_grant_sha256": sha256_file(local_grant_path),
                "sources": local_grant["sources"],
            }
            wrapper = {**payload, "task_knowledge_grant_sha256": sha256_bytes(canonical_json(payload))}
            path = self.runtime_root / session_id / "task-knowledge-grant.json"
            exclusive_private_json(path, wrapper, "role_section_binding_invalid")
            return path

    def assemble_context(self, grant_path: Path, index_path: Path) -> Path:
        if index_path is None: raise TaskKnowledgeError("role_section_binding_invalid")
        wrapper = self._load_task_grant(grant_path); session_id = wrapper["local_context_session_id"]
        with bridge_session_lock(self.runtime_root, session_id):
            index, state = self._require_index_matches_state(session_id, index_path)
            if wrapper["task_knowledge_manifest_id"] != self.manifest["manifest_id"] or wrapper["local_context_selection_sha256"] != index["local_context_selection_sha256"] or wrapper["role_section_index_sha256"] != index["index_sha256"]: raise TaskKnowledgeError("role_section_binding_invalid")
            local_grant_path = Path(wrapper["local_context_grant_path"])
            if local_grant_path.is_symlink() or not local_grant_path.is_file() or sha256_file(local_grant_path) != wrapper["local_context_grant_sha256"]: raise TaskKnowledgeError("role_section_binding_invalid")
            grant = ReadGrantService(self.runtime_root, self.workflow).load(local_grant_path)
            if grant["session_id"] != session_id or grant["sources"] != wrapper["sources"]: raise TaskKnowledgeError("role_section_binding_invalid")
            self._validate_grant_against_index(grant, index, state)
        return ContextAssembler(self.runtime_root, self.workflow, self.readers).assemble(local_grant_path)

    def assemble(self, grant_path: Path) -> Path:
        raise TaskKnowledgeError("role_section_binding_invalid")
