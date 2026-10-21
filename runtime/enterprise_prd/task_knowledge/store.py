from __future__ import annotations

import os
import tempfile
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.baseline_registry.contracts import LifecycleStatus

from .builder import build_manifest, build_preview
from .contracts import validate_confirmation, validate_manifest, validate_preview, validate_writer_input_snapshot
from .errors import TaskKnowledgeError


def _secure_directory(path: Path) -> Path:
    if path.exists() and (path.is_symlink() or not path.is_dir()): raise TaskKnowledgeError("private_root_is_unsafe")
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir(): raise TaskKnowledgeError("private_root_is_unsafe")
    os.chmod(path, 0o700)
    return path.resolve()


def _safe_child(root: Path, name: str) -> Path:
    path = root / name
    if path.parent.resolve() != root.resolve(): raise TaskKnowledgeError("private_path_escapes")
    return path


def exclusive_private_bytes(path: Path, payload: bytes, conflict_reason: str) -> bool:
    parent = _secure_directory(path.parent)
    if path.parent.resolve() != parent or path.is_symlink():
        if path.exists() or path.is_symlink(): raise TaskKnowledgeError(conflict_reason)
    if os.path.lexists(path):
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload: raise TaskKnowledgeError(conflict_reason)
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".task-knowledge-", dir=parent)
    try:
        os.fchmod(descriptor, 0o600); os.write(descriptor, payload); os.fsync(descriptor); os.close(descriptor); descriptor = -1
        try: os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload: raise TaskKnowledgeError(conflict_reason)
            return False
        directory = os.open(parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        return True
    finally:
        if descriptor != -1: os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def exclusive_private_json(path: Path, value: dict, conflict_reason: str) -> bool:
    return exclusive_private_bytes(path, canonical_json(value) + b"\n", conflict_reason)


class TaskKnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = _secure_directory(root.expanduser())
        self.manifests = _secure_directory(self.root / "manifests")
        self.writer_inputs = _secure_directory(self.root / "writer-inputs")
        if self.manifests.parent != self.root or self.writer_inputs.parent != self.root: raise TaskKnowledgeError("private_root_is_unsafe")

    def _install(self, path: Path, payload: bytes, conflict_reason: str) -> bool:
        return exclusive_private_bytes(path, payload, conflict_reason)

    def save_manifest(self, manifest: dict) -> tuple[Path, bool]:
        validate_manifest(manifest)
        path = _safe_child(self.manifests, manifest["manifest_id"] + ".json")
        return path, self._install(path, canonical_json(manifest) + b"\n", "immutable_manifest_conflict")

    def save_writer_input(self, snapshot: dict) -> tuple[Path, bool]:
        validate_writer_input_snapshot(snapshot)
        path = _safe_child(self.writer_inputs, snapshot["snapshot_id"] + ".json")
        return path, self._install(path, canonical_json(snapshot) + b"\n", "immutable_writer_input_conflict")


def revalidate_locked_sources(snapshot, preview: dict) -> None:
    for item in preview["preview_payload"]["documents"]:
        try: document = snapshot.document(item["source_id"])
        except Exception as exc: raise TaskKnowledgeError("unknown_source") from exc
        if document.lifecycle_status.value != item["lifecycle_status_at_lock"]: raise TaskKnowledgeError("source_lifecycle_drift")
        expected = document.released_revision if document.lifecycle_status is LifecycleStatus.RELEASED else document.active_revision
        if expected != item["revision"]: raise TaskKnowledgeError("source_revision_drift")


def commit_manifest(snapshot, baseline: dict, draft: dict, approved_preview: dict, confirmation: dict, store: TaskKnowledgeStore) -> dict:
    validate_preview(approved_preview); confirmation = validate_confirmation(confirmation)
    if confirmation["preview_payload_sha256"] != approved_preview["preview_payload_sha256"]: raise TaskKnowledgeError("confirmed_payload_drift")
    revalidate_locked_sources(snapshot, approved_preview)
    current = build_preview(snapshot, baseline, draft)
    if current["preview_payload"]["production_baseline_ref"] != approved_preview["preview_payload"]["production_baseline_ref"]: raise TaskKnowledgeError("baseline_manifest_drift")
    if current["preview_payload_sha256"] != approved_preview["preview_payload_sha256"]: raise TaskKnowledgeError("confirmed_payload_drift")
    manifest = build_manifest(approved_preview, confirmation)
    path, created = store.save_manifest(manifest)
    return {"manifest": manifest, "path": path, "created": created}
