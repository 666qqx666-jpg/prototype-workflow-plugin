from __future__ import annotations

import json
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes, sha256_file
from enterprise_prd.contracts import aware_timestamp, runtime_id
from enterprise_prd.task_start import require_snapshot_pack

from .contracts import BODY_ROLES, normalize_task_roles, validate_manifest, validate_role_section_index, validate_writer_input_snapshot, writer_input_digest
from .errors import TaskKnowledgeError


def _regular(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TaskKnowledgeError("context_integrity_failed")
    return path.resolve(strict=True)


def _json(path: Path) -> dict:
    try:
        value = json.loads(_regular(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskKnowledgeError("context_integrity_failed") from exc
    if type(value) is not dict:
        raise TaskKnowledgeError("context_integrity_failed")
    return value


def file_ref(path: Path) -> dict:
    path = _regular(path)
    return {"path": str(path), "sha256": sha256_file(path)}


def tree_sha256(root: Path) -> str:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise TaskKnowledgeError("pack_drift")
    root = root.resolve(strict=True)
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TaskKnowledgeError("pack_drift")
        if path.is_file():
            records.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)})
    if not records:
        raise TaskKnowledgeError("pack_drift")
    return sha256_bytes(canonical_json(records))


def _source_sections(manifest: dict, index: dict, context: dict) -> list[dict]:
    body_revisions = {item["source_id"]: item["revision"] for item in manifest["documents"] if set(normalize_task_roles(role["role"] for role in item["task_roles"])) & BODY_ROLES}
    context_sources = {item.get("source_id"): item for item in context.get("sources", []) if type(item) is dict}
    bindings = {(item["source_id"], item["normalized_path"]): item for item in index["bindings"]}
    if not bindings or set(context_sources) != {item[0] for item in bindings} or set(context_sources) != set(body_revisions):
        raise TaskKnowledgeError("context_integrity_failed")
    sections: list[dict] = []
    for key, binding in sorted(bindings.items()):
        source = context_sources[key[0]]
        if source.get("revision") != body_revisions[key[0]] or source.get("acquisition_scope") != binding["acquisition_scope"]:
            raise TaskKnowledgeError("source_revision_drift")
        matches = [section for section in source.get("sections", []) if type(section) is dict and section.get("raw_path") == binding["raw_path"] and section.get("normalized_path") == binding["normalized_path"]]
        if len(matches) != 1:
            raise TaskKnowledgeError("role_section_binding_invalid")
        sections.append({"source_id": key[0], "revision": body_revisions[key[0]], **binding})
    return sections


def build_writer_input_snapshot(*, run_id: str, enterprise_snapshot_path: Path, manifest_path: Path, context_path: Path, receipt_path: Path, role_index_path: Path, pack_path: Path, created_at: str) -> dict:
    try:
        runtime_id(run_id, "run_id")
        aware_timestamp(created_at, "created_at")
        run_metadata = require_snapshot_pack(_regular(enterprise_snapshot_path), Path(pack_path))
    except Exception as exc:
        raise TaskKnowledgeError("context_integrity_failed") from exc
    if run_metadata["run_id"] != run_id:
        raise TaskKnowledgeError("context_integrity_failed")
    manifest, context, receipt, index = _json(manifest_path), _json(context_path), _json(receipt_path), _json(role_index_path)
    try:
        validate_manifest(manifest)
        validate_role_section_index(index)
    except Exception as exc:
        raise TaskKnowledgeError("context_integrity_failed") from exc
    if receipt.get("status") != "consumed" or receipt.get("context_sha256") != sha256_file(_regular(context_path)) or context.get("grant_id") != receipt.get("grant_id") or context.get("grant_sha256") != receipt.get("grant_sha256") or index["task_knowledge_manifest_id"] != manifest["manifest_id"] or index["local_context_session_id"] != context.get("session_id"):
        raise TaskKnowledgeError("context_integrity_failed")
    payload = {
        "schema_version": 1,
        "snapshot_id": "writer-input-" + "0" * 64,
        "run_id": run_id,
        "enterprise_run_snapshot": file_ref(enterprise_snapshot_path),
        "task_knowledge_manifest": file_ref(manifest_path),
        "local_context": {"context": file_ref(context_path), "consumption_receipt": file_ref(receipt_path)},
        "role_section_index": file_ref(role_index_path),
        "capability_pack": {"path": str(Path(pack_path).resolve(strict=True)), "tree_sha256": tree_sha256(pack_path), "run_snapshot": {"channel": run_metadata["channel"], "skill_version": run_metadata["skill_version"], "pack_version": run_metadata["pack_version"], "manifest_sha256": run_metadata["manifest_sha256"], "pair_path": str(Path(run_metadata["pair_path"]).resolve(strict=True))}},
        "source_sections": _source_sections(manifest, index, context),
        "created_at": created_at,
        "snapshot_sha256": "0" * 64,
    }
    digest = writer_input_digest(payload)
    snapshot = {**payload, "snapshot_id": "writer-input-" + digest, "snapshot_sha256": digest}
    validate_writer_input_snapshot(snapshot)
    return snapshot
