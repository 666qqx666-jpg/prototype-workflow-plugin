from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import yaml

from enterprise_prd.canonical import sha256_bytes
from enterprise_prd.contracts import compatible_version
from enterprise_prd.errors import EnterprisePrdError
from enterprise_prd.manifest import manifest_from_file
from enterprise_prd.pack import load_pack
from enterprise_prd.registry import FileRegistry

from .contracts import SOURCE_ROLES, validate_entry, validate_snapshot, validate_source_set
from .errors import (
    ContractError,
    IntegrityError,
    PathBoundaryError,
    PrototypeError,
    RestartRequiredError,
)
from .readiness import load_assessment, assert_entry_binding, load_locked_entry
from .local_sources import validate_source_set_records
from .store import load_json, safe_run_path, sha256_file, write_once_json
from .version import COMPONENT_VERSION


SKILL_GENERATED_PREFIXES = (
    "prototype-design-workflow/assets/developer-reader-shell/node_modules",
)


def _existing_file(path: Path, field: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise IntegrityError(f"{field} is missing")
    return resolved


def _require_hash(path: Path, expected: str, field: str) -> None:
    if sha256_file(path) != expected:
        raise IntegrityError(f"{field} hash changed")


def create_entry(*, run_root: Path, prototype_run_id: str, prd_run_id: str,
                 review_context_id: str, prd_path: Path, review_path: Path,
                 delivery: str, coverage: str, readiness_assessment_path: Path,
                 stage_authorized_by: str, stage_authorized_at: str) -> Path:
    prd = _existing_file(prd_path, "PRD artifact")
    review = _existing_file(review_path, "review artifact")
    assessment_path = _existing_file(readiness_assessment_path, "Prototype readiness assessment")
    assessment, assessment_sha256 = load_assessment(assessment_path)
    document = {
        "schema_version": 2, "prototype_run_id": prototype_run_id,
        "prd_run_id": prd_run_id, "review_context_id": review_context_id,
        "prd_artifact_path": str(prd), "prd_artifact_sha256": sha256_file(prd),
        "review_artifact_path": str(review), "review_artifact_sha256": sha256_file(review),
        "review_evidence": assessment["review_evidence"],
        "prototype_readiness": assessment["prototype_readiness"],
        "spec_readiness": assessment["spec_readiness"],
        "readiness_assessment_path": str(assessment_path),
        "readiness_assessment_sha256": assessment_sha256,
        "stage_authorized_by": stage_authorized_by,
        "stage_authorized_at": stage_authorized_at,
        "requested_mode": "existing-system-increment",
        "requested_delivery": delivery, "requested_coverage": coverage,
    }
    validate_entry(document)
    assert_entry_binding(document)
    output = safe_run_path(run_root, prototype_run_id, "prototype-entry.json")
    write_once_json(output, document, binding_check=lambda: assert_entry_binding(document))
    return output


def create_source_set(
    *,
    run_root: Path,
    source_set_id: str,
    entry_path: Path,
    sources: dict[str, Path],
    selected_at: str,
) -> Path:
    entry, entry_sha256 = load_locked_entry(entry_path)
    if set(sources) != set(SOURCE_ROLES):
        raise ContractError("sources must contain exactly the six required roles")
    locked = []
    for role in SOURCE_ROLES:
        path = _existing_file(sources[role], f"source {role}")
        locked.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
    current_prd = locked[SOURCE_ROLES.index("current-product-requirement")]
    if (
        current_prd["path"] != entry["prd_artifact_path"]
        or current_prd["sha256"] != entry["prd_artifact_sha256"]
    ):
        raise IntegrityError("current-product-requirement differs from Prototype Entry")
    document = {
        "schema_version": 1,
        "prototype_run_id": entry["prototype_run_id"],
        "source_set_id": source_set_id,
        "sources": locked,
        "selected_at": selected_at,
    }
    validate_source_set(document)
    validate_source_set_records(document, require_absent=True)
    output = safe_run_path(
        run_root,
        entry["prototype_run_id"],
        "prototype-source-set.json",
    )
    def check_sources():
        load_locked_entry(entry_path, entry_sha256)
        validate_source_set_records(document, require_absent=True)
    write_once_json(output, document, binding_check=check_sources)
    return output


def _assert_inside(child: Path, parent: Path, field: str) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise PathBoundaryError(f"{field} escapes its allowed root") from exc


def _archive_entries(payload: bytes, field: str) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries: dict[str, bytes] = {}
            for member in archive.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                path = Path(name)
                if path.is_absolute() or ".." in path.parts or name in entries:
                    raise IntegrityError(f"{field} contains an unsafe or duplicate path")
                entries[name] = archive.read(member)
    except zipfile.BadZipFile as exc:
        raise IntegrityError(f"{field} is not a valid ZIP") from exc
    if not entries:
        raise IntegrityError(f"{field} is empty")
    return entries


def _content_sha256(entries: dict[str, bytes]) -> str:
    payload = [
        {"path": name, "sha256": sha256_bytes(entries[name])}
        for name in sorted(entries)
    ]
    return sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _allowed_extra(relative: str, prefixes: tuple[str, ...]) -> bool:
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix in prefixes)


def bind_archive_to_tree(
    payload: bytes,
    root: Path,
    field: str,
    *,
    allowed_extra_prefixes: tuple[str, ...] = (),
) -> str:
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise IntegrityError(f"installed {field} root is missing")
    entries = _archive_entries(payload, field)
    for name, expected in entries.items():
        relative = Path(name)
        target = resolved_root.joinpath(*relative.parts)
        try:
            target.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise PathBoundaryError(f"installed {field} path escapes its root") from exc
        current = target
        while current != resolved_root:
            if current.is_symlink():
                raise IntegrityError(f"installed {field} contains a symlinked release path")
            current = current.parent
        if not target.is_file() or target.read_bytes() != expected:
            raise IntegrityError(f"installed {field} differs from its release archive")
    expected_names = set(entries)
    for path in resolved_root.rglob("*"):
        relative = path.relative_to(resolved_root).as_posix()
        if _allowed_extra(relative, allowed_extra_prefixes):
            continue
        if path.is_symlink():
            raise IntegrityError(f"installed {field} contains an unexpected symlink")
        if path.is_file() and relative not in expected_names:
            raise IntegrityError(f"installed {field} contains unpublished content")
    return _content_sha256(entries)


def _archive_path(manifest_path: Path, relative: str, field: str) -> Path:
    root = manifest_path.parent.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PathBoundaryError(f"{field} archive escapes the Registry") from exc
    if not path.is_file():
        raise IntegrityError(f"{field} archive is missing")
    return path


def revalidate_snapshot_release(snapshot: dict) -> None:
    try:
        source_set_path = Path(snapshot["source_set_path"])
        _require_hash(source_set_path, snapshot["source_set_sha256"], "Source Set")
        validate_source_set_records(validate_source_set(load_json(source_set_path)))
        manifest_path = Path(snapshot["release_manifest_path"]).resolve()
        if sha256_file(manifest_path) != snapshot["release_manifest_sha256"]:
            raise IntegrityError("Prototype Canary Manifest changed")
        manifest = manifest_from_file(manifest_path)
        actual_identity = (
            manifest.channel,
            manifest.skill.name,
            manifest.skill.version,
            manifest.skill.archive,
            manifest.skill.sha256,
            manifest.company_pack.name,
            manifest.company_pack.version,
            manifest.company_pack.archive,
            manifest.company_pack.sha256,
        )
        expected_identity = (
            "canary",
            snapshot["skill_name"],
            snapshot["skill_version"],
            snapshot["skill_archive"],
            snapshot["skill_sha256"],
            snapshot["pack_name"],
            snapshot["pack_version"],
            snapshot["pack_archive"],
            snapshot["pack_sha256"],
        )
        if actual_identity != expected_identity:
            raise IntegrityError("Prototype release identity changed")
        skill_archive = _archive_path(manifest_path, snapshot["skill_archive"], "Skill")
        pack_archive = _archive_path(manifest_path, snapshot["pack_archive"], "Pack")
        skill_payload = skill_archive.read_bytes()
        pack_payload = pack_archive.read_bytes()
        if sha256_bytes(skill_payload) != snapshot["skill_sha256"]:
            raise IntegrityError("Skill archive changed")
        if sha256_bytes(pack_payload) != snapshot["pack_sha256"]:
            raise IntegrityError("Pack archive changed")
        skill_content = bind_archive_to_tree(
            skill_payload,
            Path(snapshot["skill_root"]),
            "Skill",
            allowed_extra_prefixes=SKILL_GENERATED_PREFIXES,
        )
        pack_content = bind_archive_to_tree(
            pack_payload,
            Path(snapshot["pack_root"]),
            "Pack",
        )
        if skill_content != snapshot["skill_content_sha256"]:
            raise IntegrityError("installed Skill content digest changed")
        if pack_content != snapshot["pack_content_sha256"]:
            raise IntegrityError("installed Pack content digest changed")
    except RestartRequiredError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, EnterprisePrdError, PrototypeError) as exc:
        raise RestartRequiredError(
            "Prototype release content changed during the run"
        ) from exc


def _rehash_entry_and_sources(entry: dict, source_set: dict) -> None:
    _require_hash(
        Path(entry["prd_artifact_path"]),
        entry["prd_artifact_sha256"],
        "PRD artifact",
    )
    _require_hash(
        Path(entry["review_artifact_path"]),
        entry["review_artifact_sha256"],
        "review artifact",
    )
    for source in source_set["sources"]:
        _require_hash(Path(source["path"]), source["sha256"], source["role"])
    validate_source_set_records(source_set, require_absent=True)


def start_run(
    *,
    run_root: Path,
    prototype_run_id: str,
    entry_path: Path,
    source_set_path: Path,
    manifest_path: Path,
    registry_root: Path,
    skill_root: Path,
    pack_root: Path,
    source_registry_path: Path,
    source_registry_version: str,
    workspace_root_id: str,
    created_at: str,
) -> Path:
    entry_path = _existing_file(entry_path, "Prototype Entry")
    source_set_path = _existing_file(source_set_path, "Prototype Source Set")
    entry, entry_sha256 = load_locked_entry(entry_path)
    source_set = validate_source_set(load_json(source_set_path))
    if (
        entry["prototype_run_id"] != prototype_run_id
        or source_set["prototype_run_id"] != prototype_run_id
    ):
        raise IntegrityError("run identities do not match")
    _rehash_entry_and_sources(entry, source_set)

    manifest_path = _existing_file(manifest_path, "Canary Manifest")
    manifest = manifest_from_file(manifest_path)
    if manifest.channel != "canary":
        raise ContractError("Prototype Candidate requires canary channel")
    if (
        manifest.skill.name != "enterprise-prototype-skills"
        or manifest.company_pack.name != "company-prototype"
        or manifest.skill.version != COMPONENT_VERSION
        or manifest.company_pack.version != COMPONENT_VERSION
    ):
        raise ContractError("Prototype Candidate release pair is invalid")
    registry = FileRegistry(registry_root)
    skill_payload = registry.fetch_bytes(manifest.skill.archive)
    pack_payload = registry.fetch_bytes(manifest.company_pack.archive)
    if sha256_bytes(skill_payload) != manifest.skill.sha256:
        raise IntegrityError("skill archive hash changed")
    if sha256_bytes(pack_payload) != manifest.company_pack.sha256:
        raise IntegrityError("pack archive hash changed")

    skill_root = skill_root.resolve()
    pack_root = pack_root.resolve()
    skill_content_sha256 = bind_archive_to_tree(
        skill_payload,
        skill_root,
        "Skill",
        allowed_extra_prefixes=SKILL_GENERATED_PREFIXES,
    )
    pack_content_sha256 = bind_archive_to_tree(pack_payload, pack_root, "Pack")
    loaded_pack = load_pack(pack_root)
    if (
        loaded_pack.manifest.pack_id != manifest.company_pack.name
        or loaded_pack.manifest.version != manifest.company_pack.version
        or not compatible_version(manifest.skill.version, loaded_pack.manifest.compatible_skill)
    ):
        raise ContractError("installed pack differs from release pair")
    contract_path = pack_root / "contracts/prototype-source-selection.yaml"
    contract = yaml.safe_load(_existing_file(contract_path, "Prototype contract").read_text())
    if type(contract) is not dict or contract.get("allowed_adapters") != ["local-file"]:
        raise ContractError("Prototype Candidate adapter contract is invalid")

    source_registry = _existing_file(source_registry_path, "Source Registry")
    _assert_inside(source_registry, pack_root, "Source Registry")
    source_registry_document = yaml.safe_load(source_registry.read_text(encoding="utf-8"))
    if (
        type(source_registry_document) is not dict
        or source_registry_document.get("registry_version") != source_registry_version
    ):
        raise ContractError("Source Registry version is invalid")
    relative_registry = source_registry.relative_to(pack_root).as_posix()
    with zipfile.ZipFile(io.BytesIO(pack_payload)) as archive:
        try:
            archived_registry = archive.read(relative_registry)
        except KeyError as exc:
            raise IntegrityError("Source Registry is absent from pack archive") from exc
    if sha256_bytes(archived_registry) != sha256_file(source_registry):
        raise IntegrityError("installed Source Registry differs from pack archive")

    document = {
        "schema_version": 1,
        "prototype_run_id": prototype_run_id,
        "skill_name": manifest.skill.name,
        "skill_version": manifest.skill.version,
        "skill_archive": manifest.skill.archive,
        "skill_sha256": manifest.skill.sha256,
        "skill_root": str(skill_root),
        "skill_content_sha256": skill_content_sha256,
        "pack_name": manifest.company_pack.name,
        "pack_version": manifest.company_pack.version,
        "pack_archive": manifest.company_pack.archive,
        "pack_sha256": manifest.company_pack.sha256,
        "pack_root": str(pack_root),
        "pack_content_sha256": pack_content_sha256,
        "release_manifest_path": str(manifest_path.resolve()),
        "release_manifest_sha256": sha256_file(manifest_path),
        "entry_path": str(entry_path),
        "entry_sha256": entry_sha256,
        "source_set_path": str(source_set_path),
        "source_set_sha256": sha256_file(source_set_path),
        "source_registry_path": str(source_registry),
        "source_registry_version": source_registry_version,
        "source_registry_sha256": sha256_file(source_registry),
        "allowed_adapters": ["local-file"],
        "workspace_root_id": workspace_root_id,
        "status": "ready",
        "created_at": created_at,
    }
    validate_snapshot(document)
    output = safe_run_path(
        run_root,
        prototype_run_id,
        "prototype-run-snapshot.json",
    )
    def check_start_sources():
        load_locked_entry(entry_path, entry_sha256)
        _require_hash(source_set_path, document["source_set_sha256"], "Source Set")
        validate_source_set_records(source_set, require_absent=True)
    write_once_json(output, document, binding_check=check_start_sources)
    return output
