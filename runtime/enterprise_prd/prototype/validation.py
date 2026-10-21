from __future__ import annotations

import subprocess
from pathlib import Path

from .contracts import validate_receipt, validate_snapshot
from .errors import (
    ContractError,
    GateBlockedError,
    IntegrityError,
    PathBoundaryError,
    RestartRequiredError,
)
from .start import revalidate_snapshot_release
from .readiness import load_locked_entry
from .local_sources import validate_source_set_records, assert_standard_source_binding, assert_locked_sources
from .store import load_json, safe_run_path, sha256_file, write_once_json
from .validation_context import workspace_identity


BANNED_LIGHT_TEXT = (
    "file://",
    "/Users/",
    "Obsidian" + " Vault",
    "BEGIN PRIVATE" + " KEY",
)


def _require_file(path: Path | None, field: str) -> Path:
    if path is None:
        raise ContractError(f"{field} is required")
    resolved = path.resolve()
    if not resolved.is_file():
        raise IntegrityError(f"{field} is missing")
    return resolved


def assert_receipt_release_binding(snapshot: dict, receipt: dict) -> None:
    for field in (
        "skill_root",
        "skill_content_sha256",
        "pack_root",
        "pack_content_sha256",
    ):
        if receipt[field] != snapshot[field]:
            raise IntegrityError(f"Validation Receipt {field} differs from Snapshot")
    if receipt["validator_mode"] == "standard":
        expected = (
            Path(snapshot["skill_root"])
            / "prototype-design-workflow/scripts/validate-prototype-handoff.mjs"
        ).resolve()
        if receipt["validator_path"] != str(expected):
            raise IntegrityError("Validation Receipt does not bind the released validator")
        try:
            current_sha256 = sha256_file(expected)
        except OSError as exc:
            raise RestartRequiredError(
                "released Prototype validator is unavailable"
            ) from exc
        if current_sha256 != receipt["validator_sha256"]:
            raise RestartRequiredError("released Prototype validator changed during the run")


def validate_output(
    *,
    run_root: Path,
    snapshot_path: Path,
    prototype_path: Path,
    validator_mode: str,
    manifest_path: Path | None,
    evidence_path: Path | None,
    independent_review_path: Path | None,
    skill_root: Path,
    workspace_root: Path | None,
    evidence_roots: tuple[Path, ...],
    checked_at: str,
    reader_snapshot_path: Path | None = None,
    reader_metadata_path: Path | None = None,
    previous_manifest_path: Path | None = None,
    previous_snapshot_path: Path | None = None,
    lineage_baseline_receipt_path: Path | None = None,
    product_freeze_manifest_path: Path | None = None,
) -> Path:
    snapshot_path = _require_file(snapshot_path, "Snapshot")
    snapshot = validate_snapshot(load_json(snapshot_path))
    if snapshot["status"] != "ready":
        raise GateBlockedError("Snapshot is not ready")
    skill_root = skill_root.resolve()
    if skill_root != Path(snapshot["skill_root"]).resolve():
        raise RestartRequiredError("running Skill root differs from Snapshot")
    revalidate_snapshot_release(snapshot)
    entry, entry_sha256 = load_locked_entry(Path(snapshot["entry_path"]), snapshot["entry_sha256"])
    if entry["prototype_run_id"] != snapshot["prototype_run_id"]:
        raise IntegrityError("Entry and Snapshot runs differ")
    if entry["requested_delivery"] != validator_mode:
        raise ContractError("validator mode differs from Entry delivery")

    prototype = _require_file(prototype_path, "Prototype")
    validate_source_set_records(load_json(Path(snapshot["source_set_path"])), prototype_path=prototype)
    if prototype.suffix.lower() != ".html" or prototype.stat().st_size < 512:
        raise GateBlockedError("Prototype must be an HTML file of at least 512 bytes")
    try:
        prototype_text = prototype.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GateBlockedError("Prototype must be UTF-8") from exc
    for token in BANNED_LIGHT_TEXT:
        if token in prototype_text:
            raise GateBlockedError(f"Prototype contains banned token: {token}")

    manifest = evidence = independent_review = validator = None
    if validator_mode == "light":
        if (
            manifest_path is not None
            or evidence_path is not None
            or independent_review_path is not None
            or any((reader_snapshot_path, reader_metadata_path, previous_manifest_path,
                    previous_snapshot_path, lineage_baseline_receipt_path, product_freeze_manifest_path))
            or workspace_root is not None
            or evidence_roots
        ):
            raise ContractError("light validation cannot accept standard artifacts")
    elif validator_mode == "standard":
        manifest = _require_file(manifest_path, "v4 Manifest")
        evidence = _require_file(evidence_path, "Evidence Bundle")
        manifest_document = load_json(manifest)
        if manifest_document.get("schemaVersion") != "4.0.0":
            raise RestartRequiredError("current Standard Run requires Manifest v4.0.0")
        if independent_review_path is not None:
            raise ContractError("Independent Review v2 is created after Builder Handoff")
        reader_snapshot = _require_new_file(reader_snapshot_path, "Reader Snapshot")
        reader_metadata = _require_new_file(reader_metadata_path, "Reader metadata")
        lineage_mode = manifest_document.get("delivery", {}).get("lineage", {}).get("mode")
        previous_inputs = (previous_manifest_path, previous_snapshot_path, lineage_baseline_receipt_path)
        if lineage_mode == "initial" and any(previous_inputs):
            raise ContractError("initial lineage cannot accept previous inputs")
        if lineage_mode == "revision" and any(item is None for item in previous_inputs):
            raise ContractError("revision lineage requires previous manifest, snapshot and baseline receipt")
        if lineage_mode not in {"initial", "revision"}:
            raise GateBlockedError("Manifest lineage mode is invalid")
        previous_manifest = _require_file(previous_manifest_path, "Previous Manifest") if previous_manifest_path else None
        previous_snapshot = _require_file(previous_snapshot_path, "Previous Reader Snapshot") if previous_snapshot_path else None
        lineage_receipt = _require_file(lineage_baseline_receipt_path, "Lineage baseline receipt") if lineage_baseline_receipt_path else None
        product_freeze = _require_file(product_freeze_manifest_path, "Product freeze manifest") if product_freeze_manifest_path else None
        assert_standard_source_binding(load_json(Path(snapshot["source_set_path"])), manifest, evidence)
        workspace = workspace_root.resolve() if workspace_root is not None else None
        if workspace is None or not workspace.is_dir() or not evidence_roots:
            raise ContractError("standard validation needs workspace and evidence roots")
        workspace_identity(manifest_document, prototype, workspace)
        roots = tuple(path.resolve() for path in evidence_roots)
        if any(not path.is_dir() for path in roots):
            raise ContractError("every evidence root must be a directory")
        independent_review = None
        core = skill_root / "prototype-design-workflow"
        script = core / "scripts/validate-prototype-handoff.mjs"
        if not (core / "SKILL.md").is_file() or not script.is_file():
            raise IntegrityError("installed Prototype Skill is incomplete")
        try:
            script.relative_to(core)
        except ValueError as exc:
            raise PathBoundaryError("validator script escapes Prototype Skill") from exc
        command = [
            "node",
            str(script),
            "--manifest",
            str(manifest),
            "--prototype",
            str(prototype),
            "--evidence",
            str(evidence),
            "--workspace-root",
            str(workspace),
            "--snapshot-out", str(reader_snapshot),
            "--metadata-out", str(reader_metadata),
            "--defer-independent-review",
        ]
        for root in roots:
            command.extend(("--evidence-root", str(root)))
        if previous_manifest is not None:
            command.extend(("--previous-manifest", str(previous_manifest), "--previous-snapshot", str(previous_snapshot), "--lineage-baseline-receipt", str(lineage_receipt)))
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "validator failed").splitlines()[0]
            raise GateBlockedError(f"v4 validator failed: {detail[:300]}")
        validator = script.resolve()
    else:
        raise ContractError("validator_mode must be light or standard")

    revalidate_snapshot_release(snapshot)
    if validator_mode == "standard":
        metadata_document = load_json(reader_metadata)
        if (metadata_document.get("schemaVersion") != "4.0.0"
                or metadata_document.get("manifestSha256") != sha256_file(manifest)
                or metadata_document.get("prototypeSha256") != sha256_file(prototype)
                or metadata_document.get("outputSha256") != sha256_file(reader_snapshot)):
            raise IntegrityError("Reader metadata does not bind current v4 inputs")
    document = {
        "schema_version": 1,
        "prototype_run_id": snapshot["prototype_run_id"],
        "validator_mode": validator_mode,
        "prototype_path": str(prototype),
        "prototype_sha256": sha256_file(prototype),
        "manifest_path": str(manifest) if manifest is not None else None,
        "manifest_sha256": sha256_file(manifest) if manifest is not None else None,
        "evidence_path": str(evidence) if evidence is not None else None,
        "evidence_sha256": sha256_file(evidence) if evidence is not None else None,
        "independent_review_path": str(independent_review) if independent_review is not None else None,
        "independent_review_sha256": (
            sha256_file(independent_review) if independent_review is not None else None
        ),
        "skill_root": snapshot["skill_root"],
        "skill_content_sha256": snapshot["skill_content_sha256"],
        "pack_root": snapshot["pack_root"],
        "pack_content_sha256": snapshot["pack_content_sha256"],
        "validator_path": str(validator) if validator is not None else None,
        "validator_sha256": sha256_file(validator) if validator is not None else None,
        "status": "pass",
        "checked_at": checked_at,
    }
    if validator_mode == "standard":
        document.update({
            "validation_context": {"workspace_root": str(workspace), "evidence_roots": sorted({str(path) for path in roots})},
            "manifest_contract_version": "4.0.0",
            "reader_snapshot_path": str(reader_snapshot),
            "reader_snapshot_sha256": sha256_file(reader_snapshot),
            "reader_metadata_path": str(reader_metadata),
            "reader_metadata_sha256": sha256_file(reader_metadata),
            "previous_manifest_path": str(previous_manifest) if previous_manifest else None,
            "previous_manifest_sha256": sha256_file(previous_manifest) if previous_manifest else None,
            "previous_snapshot_path": str(previous_snapshot) if previous_snapshot else None,
            "previous_snapshot_sha256": sha256_file(previous_snapshot) if previous_snapshot else None,
            "lineage_baseline_receipt_path": str(lineage_receipt) if lineage_receipt else None,
            "lineage_baseline_receipt_sha256": sha256_file(lineage_receipt) if lineage_receipt else None,
            "product_freeze_manifest_path": str(product_freeze) if product_freeze else None,
            "product_freeze_manifest_sha256": sha256_file(product_freeze) if product_freeze else None,
        })
    validate_receipt(document)
    assert_receipt_release_binding(snapshot, document)
    output = safe_run_path(run_root, snapshot["prototype_run_id"], "validation-receipt.json")
    def check_receipt_sources():
        load_locked_entry(Path(snapshot["entry_path"]), entry_sha256)
        assert_locked_sources(snapshot, prototype_path=prototype)
    write_once_json(output, document, binding_check=check_receipt_sources)
    return output


def _require_new_file(path: Path | None, label: str) -> Path:
    if path is None:
        raise ContractError(f"standard validation requires {label} output path")
    resolved = path.resolve()
    if resolved.exists():
        raise GateBlockedError(f"{label} output already exists")
    return resolved
