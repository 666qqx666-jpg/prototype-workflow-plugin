from __future__ import annotations

from pathlib import Path

from .contracts import (
    validate_builder_handoff,
    validate_receipt,
    validate_snapshot,
    validate_source_set,
)
from .errors import GateBlockedError, IntegrityError
from .start import revalidate_snapshot_release
from .readiness import load_locked_entry
from .store import load_json, safe_run_path, sha256_file, write_once_json
from .validation import assert_receipt_release_binding
from .local_sources import assert_locked_sources


def _locked(path: str, expected: str, field: str) -> Path:
    artifact = Path(path).resolve()
    if sha256_file(artifact) != expected:
        raise IntegrityError(f"{field} hash changed")
    return artifact


def _assert_freeze_validation_binding(freeze: dict, validation_receipt_path: Path) -> None:
    if freeze.get("validationReceiptSha256") != sha256_file(validation_receipt_path):
        raise IntegrityError("Product Freeze does not bind the current Validation Receipt")


def create_builder_handoff(
    *,
    run_root: Path,
    builder_handoff_id: str,
    builder_context_id: str,
    entry_path: Path,
    source_set_path: Path,
    snapshot_path: Path,
    validation_receipt_path: Path,
    baseline_evidence: str,
    high_fidelity_readiness: str,
    prototype_sync: str,
    pending_item_ids: list[str],
    blocker_ids: list[str],
    created_at: str,
    product_freeze_manifest_path: Path | None = None,
) -> Path:
    entry_path = entry_path.resolve()
    source_set_path = source_set_path.resolve()
    snapshot_path = snapshot_path.resolve()
    validation_receipt_path = validation_receipt_path.resolve()
    source_set = validate_source_set(load_json(source_set_path))
    snapshot = validate_snapshot(load_json(snapshot_path))
    entry, entry_sha256 = load_locked_entry(entry_path, snapshot["entry_sha256"])
    receipt = validate_receipt(load_json(validation_receipt_path))
    run_ids = {
        entry["prototype_run_id"],
        source_set["prototype_run_id"],
        snapshot["prototype_run_id"],
        receipt["prototype_run_id"],
    }
    if len(run_ids) != 1:
        raise IntegrityError("Builder artifacts belong to different runs")
    if (
        snapshot["entry_path"] != str(entry_path)
        or snapshot["entry_sha256"] != entry_sha256
        or snapshot["source_set_path"] != str(source_set_path)
        or snapshot["source_set_sha256"] != sha256_file(source_set_path)
    ):
        raise IntegrityError("Snapshot bindings changed")
    if snapshot["status"] != "ready":
        raise GateBlockedError("Snapshot is not ready")
    revalidate_snapshot_release(snapshot)
    assert_receipt_release_binding(snapshot, receipt)
    _locked(entry["prd_artifact_path"], entry["prd_artifact_sha256"], "PRD artifact")
    _locked(entry["review_artifact_path"], entry["review_artifact_sha256"], "PRD Review artifact")
    for source in source_set["sources"]:
        _locked(source["path"], source["sha256"], source["role"])
    _locked(receipt["prototype_path"], receipt["prototype_sha256"], "Prototype")
    for path_field, hash_field, label in (
        ("manifest_path", "manifest_sha256", "Manifest"),
        ("evidence_path", "evidence_sha256", "Evidence Bundle"),
        ("independent_review_path", "independent_review_sha256", "v3 Independent Review"),
    ):
        if receipt[path_field] is not None:
            _locked(receipt[path_field], receipt[hash_field], label)
    if product_freeze_manifest_path is not None and receipt.get("manifest_contract_version") != "4.0.0":
        raise IntegrityError("Product Freeze can only bind a v4 Standard receipt")
    if receipt.get("manifest_contract_version") == "4.0.0":
        for path_field, hash_field, label in (
            ("reader_snapshot_path", "reader_snapshot_sha256", "Reader Snapshot"),
            ("reader_metadata_path", "reader_metadata_sha256", "Reader metadata"),
        ):
            _locked(receipt[path_field], receipt[hash_field], label)
        for path_field, hash_field, label in (
            ("previous_manifest_path", "previous_manifest_sha256", "Previous Manifest"),
            ("previous_snapshot_path", "previous_snapshot_sha256", "Previous Snapshot"),
            ("lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256", "Lineage baseline receipt"),
            ("product_freeze_manifest_path", "product_freeze_manifest_sha256", "Product Freeze"),
        ):
            if receipt[path_field] is not None:
                _locked(receipt[path_field], receipt[hash_field], label)
        metadata = load_json(Path(receipt["reader_metadata_path"]))
        if metadata.get("manifestSha256") != receipt["manifest_sha256"] or metadata.get("outputSha256") != receipt["reader_snapshot_sha256"]:
            raise IntegrityError("Reader metadata changed its v4 entity bindings")
        if receipt["product_freeze_manifest_path"] is not None:
            freeze = load_json(Path(receipt["product_freeze_manifest_path"]))
            _assert_freeze_validation_binding(freeze, validation_receipt_path)
            expected_freeze = {
                "prdSha256": entry["prd_artifact_sha256"],
                "prototypeSha256": receipt["prototype_sha256"],
                "readerSha256": receipt["reader_snapshot_sha256"],
            }
            if (freeze.get("inputs") != expected_freeze
                    or freeze.get("manifestSha256") != receipt["manifest_sha256"]
                    or freeze.get("evidenceSha256") != receipt["evidence_sha256"]
                    or freeze.get("validationReceiptSha256") != sha256_file(validation_receipt_path)):
                raise IntegrityError("Product Freeze does not bind current Builder inputs")
        if product_freeze_manifest_path is not None:
            freeze_path = product_freeze_manifest_path.resolve()
            freeze_sha256 = sha256_file(freeze_path)
            freeze = load_json(freeze_path)
            _assert_freeze_validation_binding(freeze, validation_receipt_path)
            expected_freeze = {
                "prdSha256": entry["prd_artifact_sha256"],
                "prototypeSha256": receipt["prototype_sha256"],
                "readerSha256": receipt["reader_snapshot_sha256"],
            }
            if (freeze.get("inputs") != expected_freeze
                    or freeze.get("manifestSha256") != receipt["manifest_sha256"]
                    or freeze.get("evidenceSha256") != receipt["evidence_sha256"]
                    or freeze.get("validationReceiptSha256") != sha256_file(validation_receipt_path)):
                raise IntegrityError("Product Freeze does not bind current Builder inputs")
    if (
        baseline_evidence != "pass"
        or high_fidelity_readiness != "pass"
        or receipt["status"] != "pass"
        or prototype_sync != "pass"
        or pending_item_ids
        or blocker_ids
    ):
        raise GateBlockedError("Builder gates are not closed")

    document = {
        "schema_version": 1,
        "prototype_run_id": entry["prototype_run_id"],
        "builder_handoff_id": builder_handoff_id,
        "builder_context_id": builder_context_id,
        "entry_path": str(entry_path),
        "entry_sha256": entry_sha256,
        "source_set_path": str(source_set_path),
        "source_set_sha256": sha256_file(source_set_path),
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": sha256_file(snapshot_path),
        "prd_artifact_path": entry["prd_artifact_path"],
        "prd_artifact_sha256": entry["prd_artifact_sha256"],
        "review_artifact_path": entry["review_artifact_path"],
        "review_artifact_sha256": entry["review_artifact_sha256"],
        "prototype_path": receipt["prototype_path"],
        "prototype_sha256": receipt["prototype_sha256"],
        "manifest_path": receipt["manifest_path"],
        "manifest_sha256": receipt["manifest_sha256"],
        "evidence_path": receipt["evidence_path"],
        "evidence_sha256": receipt["evidence_sha256"],
        "independent_review_path": receipt["independent_review_path"],
        "independent_review_sha256": receipt["independent_review_sha256"],
        "validation_receipt_path": str(validation_receipt_path),
        "validation_receipt_sha256": sha256_file(validation_receipt_path),
        "delivery": entry["requested_delivery"],
        "coverage": entry["requested_coverage"],
        "baseline_evidence": baseline_evidence,
        "high_fidelity_readiness": high_fidelity_readiness,
        "automated_validation": receipt["status"],
        "prototype_sync": prototype_sync,
        "pending_item_ids": list(pending_item_ids),
        "blocker_ids": list(blocker_ids),
        "created_at": created_at,
    }
    if receipt.get("manifest_contract_version") == "4.0.0":
        document.update({key: receipt[key] for key in (
            "manifest_contract_version", "reader_snapshot_path", "reader_snapshot_sha256",
            "reader_metadata_path", "reader_metadata_sha256", "previous_manifest_path",
            "previous_manifest_sha256", "previous_snapshot_path", "previous_snapshot_sha256",
            "lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256",
            "product_freeze_manifest_path", "product_freeze_manifest_sha256",
        )})
        if "validation_context" in receipt:
            document["validation_context"] = receipt["validation_context"]
        if product_freeze_manifest_path is not None:
            document["product_freeze_manifest_path"] = str(product_freeze_manifest_path.resolve())
            document["product_freeze_manifest_sha256"] = freeze_sha256
    validate_builder_handoff(document)
    output = safe_run_path(run_root, entry["prototype_run_id"], "builder-handoff.json")
    def check_builder_sources():
        load_locked_entry(entry_path, entry_sha256)
        assert_locked_sources(snapshot, prototype_path=Path(receipt["prototype_path"]))
        if product_freeze_manifest_path is not None:
            _locked(str(product_freeze_manifest_path), freeze_sha256, "Product Freeze")
    write_once_json(output, document, binding_check=check_builder_sources)
    return output
