from __future__ import annotations

from pathlib import Path
import subprocess

from .contracts import (
    validate_builder_handoff,
    validate_prototype_handoff,
    validate_receipt,
    validate_review,
    validate_snapshot,
    validate_source_set,
)
from .errors import GateBlockedError, IntegrityError
from .start import revalidate_snapshot_release
from .readiness import load_locked_entry
from .store import load_json, safe_run_path, sha256_file, write_once_json
from .validation import assert_receipt_release_binding
from .local_sources import assert_locked_sources
from .handoff import _assert_freeze_validation_binding
from .validation_context import bound_context
from .version import COMPONENT_VERSION

NEGATIVE_CHECK_IDS = frozenset({
    "unrelated-field-preservation", "empty-and-missing-values",
    "historical-out-of-candidate-data", "unauthorized-actions",
    "concurrent-conflict", "post-save-membership",
})


def _validate_independent_review_v2(review: dict, builder: dict) -> None:
    if type(review) is not dict or review.get("schemaVersion") != "2.0.0":
        raise GateBlockedError("Independent Review v2 is required")
    expected = {
        "manifestHash": builder["manifest_sha256"],
        "prototypeHash": builder["prototype_sha256"],
        "evidenceBundleHash": builder["evidence_sha256"],
        "currentSnapshotHash": builder["reader_snapshot_sha256"],
        "previousManifestHash": builder["previous_manifest_sha256"],
        "previousSnapshotHash": builder["previous_snapshot_sha256"],
    }
    if review.get("reviewerRole") != "independent" or any(review.get("subject", {}).get(key) != value for key, value in expected.items()):
        raise IntegrityError("Independent Review v2 subject bindings changed")
    for key in ("scopeReview", "pendingReview"):
        section = review.get(key)
        if type(section) is not dict or section.get("result") != "pass" or not section.get("evidenceRefs") or not section.get("note", "").strip():
            raise GateBlockedError(f"Independent Review v2 {key} did not pass")
    checks = review.get("negativeChecks")
    if type(checks) is not list or {item.get("checkId") for item in checks if type(item) is dict} != NEGATIVE_CHECK_IDS or len(checks) != len(NEGATIVE_CHECK_IDS):
        raise GateBlockedError("Independent Review v2 negative checks are incomplete")
    for item in checks:
        if item.get("applicability") == "applicable":
            if item.get("result") != "pass" or not item.get("evidenceRefs"):
                raise GateBlockedError(f"Independent Review v2 check blocked: {item.get('checkId')}")
        elif item.get("applicability") != "not-applicable" or item.get("result") != "not-applicable" or not item.get("note", "").strip():
            raise GateBlockedError(f"Independent Review v2 check invalid: {item.get('checkId')}")


def _revalidate_v4_gate(builder: dict, review: dict, context: dict) -> None:
    manifest_path = Path(builder["manifest_path"])
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != "4.0.0":
        raise GateBlockedError("Final Gate requires Manifest v4.0.0")
    cards = manifest.get("cards")
    if type(cards) is not list or any(card.get("displayNumber") != str(index + 1) for index, card in enumerate(cards)):
        raise GateBlockedError("Final Gate detected discontinuous display numbers")
    current_pending = [item for card in cards for item in card.get("pendingItems", [])]
    current_pending += [item for rule in manifest.get("globalRules", []) for item in rule.get("pendingItems", [])]
    if any(item.get("status") != "unresolved" or item.get("blocksDelivery") for item in current_pending):
        raise GateBlockedError("Final Gate detected unresolved blocking pending state")
    metadata = load_json(Path(builder["reader_metadata_path"]))
    if (metadata.get("manifestSha256") != builder["manifest_sha256"]
            or metadata.get("prototypeSha256") != builder["prototype_sha256"]
            or metadata.get("outputSha256") != builder["reader_snapshot_sha256"]):
        raise IntegrityError("Final Gate Reader metadata binding changed")
    if builder["product_freeze_manifest_path"] is not None:
        freeze = load_json(Path(builder["product_freeze_manifest_path"]))
        _assert_freeze_validation_binding(freeze, Path(builder["validation_receipt_path"]))
        expected = {
            "prdSha256": builder["prd_artifact_sha256"],
            "prototypeSha256": builder["prototype_sha256"],
            "readerSha256": builder["reader_snapshot_sha256"],
        }
        if (freeze.get("inputs") != expected
                or freeze.get("manifestSha256") != builder["manifest_sha256"]
                or freeze.get("evidenceSha256") != builder["evidence_sha256"]
                or freeze.get("validationReceiptSha256") != builder["validation_receipt_sha256"]):
            raise IntegrityError("Product Freeze does not bind the current success artifacts")
    skill = Path(builder["skill_root"] if "skill_root" in builder else load_json(Path(builder["snapshot_path"]))["skill_root"])
    script = skill / "prototype-design-workflow/scripts/validate-prototype-handoff.mjs"
    evidence_roots = context["evidence_roots"]
    workspace = context["workspace_root"]
    command = [
        "node", str(script), "--manifest", str(manifest_path),
        "--prototype", builder["prototype_path"], "--evidence", builder["evidence_path"],
        "--workspace-root", str(workspace), "--review", review["independent_review_path"],
        "--current-snapshot", builder["reader_snapshot_path"],
    ]
    for root in evidence_roots:
        command.extend(("--evidence-root", root))
    if builder["previous_manifest_path"] is not None:
        command.extend(("--previous-manifest", builder["previous_manifest_path"],
                        "--previous-snapshot", builder["previous_snapshot_path"],
                        "--lineage-baseline-receipt", builder["lineage_baseline_receipt_path"]))
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    if result.returncode:
        detail = (result.stderr or result.stdout or "Final Gate validation failed").splitlines()[0]
        raise GateBlockedError(f"Final Gate v4 revalidation failed: {detail[:300]}")


def _locked(path: str, expected: str, field: str) -> Path:
    artifact = Path(path).resolve()
    if sha256_file(artifact) != expected:
        raise IntegrityError(f"{field} hash changed")
    return artifact


def _execution(snapshot: dict, builder: dict, run_root: Path, compatibility: bool):
    versions = (snapshot["skill_version"], snapshot["pack_version"])
    if versions == (COMPONENT_VERSION, COMPONENT_VERSION):
        if compatibility:
            raise GateBlockedError("T01 compatibility is only for frozen 0.1.18 inputs")
        return None
    if not compatibility or COMPONENT_VERSION != "0.1.19" or versions != ("0.1.18", "0.1.18") or builder.get("manifest_contract_version") != "4.0.0":
        raise GateBlockedError("Cross-version Review/Gate requires explicit T01 0.1.18 compatibility")
    destination = safe_run_path(run_root, builder["prototype_run_id"], "prototype-review.json").parent
    # Never append a recovered success to any of the old Run's directories.
    for field in ("snapshot_path", "entry_path", "validation_receipt_path"):
        old_run = Path(builder[field]).resolve().parent
        if destination.is_relative_to(old_run):
            raise GateBlockedError("T01 compatibility requires a new output directory outside the frozen Run")
    return {"mode": "t01-0.1.18-compatibility", "runtime_version": COMPONENT_VERSION,
            "source_skill_version": versions[0], "source_pack_version": versions[1]}


def create_review(
    *,
    run_root: Path,
    builder_handoff_path: Path,
    builder_context_id: str,
    review_context_id: str,
    verdict: str | None,
    findings: list[dict] | None,
    independent_review_path: Path | None = None,
    reviewed_at: str,
    t01_compatibility: bool = False,
) -> Path:
    builder_handoff_path = builder_handoff_path.resolve()
    builder = validate_builder_handoff(load_json(builder_handoff_path))
    entry_path = Path(builder["entry_path"])
    entry, entry_sha256 = load_locked_entry(entry_path, builder["entry_sha256"])
    if builder["builder_context_id"] != builder_context_id:
        raise IntegrityError("builder context differs from Builder Handoff")
    if review_context_id == builder_context_id:
        raise GateBlockedError("Prototype Review requires a new context")
    _locked(builder["prototype_path"], builder["prototype_sha256"], "Prototype")
    snapshot = validate_snapshot(load_json(_locked(builder["snapshot_path"], builder["snapshot_sha256"], "Snapshot")))
    revalidate_snapshot_release(snapshot)
    receipt = validate_receipt(load_json(_locked(builder["validation_receipt_path"], builder["validation_receipt_sha256"], "Validation Receipt")))
    assert_receipt_release_binding(snapshot, receipt)
    execution = _execution(snapshot, builder, run_root, t01_compatibility)
    assert_locked_sources(snapshot, prototype_path=Path(builder["prototype_path"]))
    independent_review = None
    if builder.get("manifest_contract_version") == "4.0.0":
        context = bound_context(builder, receipt)
        if independent_review_path is None:
            raise GateBlockedError("Independent Review v2 is required after Builder Handoff")
        independent_review_path = _locked(str(independent_review_path), sha256_file(independent_review_path), "Independent Review v2")
        independent_review = load_json(independent_review_path)
        _validate_independent_review_v2(independent_review, builder)
        snapshot_release = load_json(Path(builder["snapshot_path"]))
        validator = Path(snapshot_release["skill_root"]) / "prototype-design-workflow/scripts/validate-independent-review.mjs"
        if not validator.is_file():
            raise IntegrityError("released Independent Review validator is missing")
        evidence_bundle = load_json(Path(builder["evidence_path"]))
        command = [
            "node", str(validator), "--review", str(independent_review_path),
            "--manifest", builder["manifest_path"], "--prototype", builder["prototype_path"],
            "--evidence", builder["evidence_path"], "--current-snapshot", builder["reader_snapshot_path"],
        ]
        if builder["previous_manifest_path"] is not None:
            command.extend(("--previous-manifest", builder["previous_manifest_path"],
                            "--previous-snapshot", builder["previous_snapshot_path"]))
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise GateBlockedError("E_REVIEW_NEGATIVE_CHECKS")
        evidence_roots = context["evidence_roots"]
        core_validator = Path(snapshot_release["skill_root"]) / "prototype-design-workflow/scripts/validate-prototype-handoff.mjs"
        command = [
            "node", str(core_validator), "--manifest", builder["manifest_path"],
            "--prototype", builder["prototype_path"], "--evidence", builder["evidence_path"],
            "--workspace-root", context["workspace_root"],
            "--review", str(independent_review_path), "--current-snapshot", builder["reader_snapshot_path"],
        ]
        for root in evidence_roots:
            command.extend(("--evidence-root", root))
        if builder["previous_manifest_path"] is not None:
            command.extend(("--previous-manifest", builder["previous_manifest_path"],
                            "--previous-snapshot", builder["previous_snapshot_path"],
                            "--lineage-baseline-receipt", builder["lineage_baseline_receipt_path"]))
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "core validation failed").splitlines()[0]
            raise GateBlockedError(f"E_INDEPENDENT_REVIEW_REQUIRED: {detail[:300]}")
        verdict = independent_review["verdict"]
        findings = [{
            "severity": "major" if verdict == "block" else "note",
            "code": f"independent-review-{index + 1}",
            "message": finding,
            "artifact_ref": "independent-review.json",
        } for index, finding in enumerate(independent_review.get("findings", []))]
    else:
        if verdict is None:
            raise GateBlockedError("legacy review requires an explicit verdict")
        findings = findings or []
    document = {
        "schema_version": 2 if independent_review is not None else 1,
        "prototype_run_id": builder["prototype_run_id"],
        "review_context_id": review_context_id,
        "reviewed_builder_handoff_sha256": sha256_file(builder_handoff_path),
        "reviewed_prototype_sha256": builder["prototype_sha256"],
        "verdict": verdict,
        "findings": list(findings),
        "reviewed_at": reviewed_at,
    }
    if independent_review is not None:
        document.update({key: builder[key] for key in (
            "manifest_contract_version", "reader_snapshot_path", "reader_snapshot_sha256",
            "reader_metadata_path", "reader_metadata_sha256", "previous_manifest_path",
            "previous_manifest_sha256", "previous_snapshot_path", "previous_snapshot_sha256",
            "lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256",
            "product_freeze_manifest_path", "product_freeze_manifest_sha256",
        )})
        document["independent_review_path"] = str(independent_review_path)
        document["independent_review_sha256"] = sha256_file(independent_review_path)
        document["reviewed_manifest_sha256"] = builder["manifest_sha256"]
        document["reviewed_reader_snapshot_sha256"] = builder["reader_snapshot_sha256"]
        document["reviewed_previous_manifest_sha256"] = builder["previous_manifest_sha256"]
        document["reviewed_previous_snapshot_sha256"] = builder["previous_snapshot_sha256"]
        document["negative_check_ids"] = [
            "unrelated-field-preservation", "empty-and-missing-values",
            "historical-out-of-candidate-data", "unauthorized-actions",
            "concurrent-conflict", "post-save-membership",
        ]
    if execution is not None:
        document["execution"] = execution
    validate_review(document)
    output = safe_run_path(run_root, builder["prototype_run_id"], "prototype-review.json")
    def check_review_sources():
        load_locked_entry(entry_path, entry_sha256)
        assert_locked_sources(snapshot, prototype_path=Path(builder["prototype_path"]))
        _locked(str(builder_handoff_path), document["reviewed_builder_handoff_sha256"], "Builder Handoff")
        _locked(builder["validation_receipt_path"], builder["validation_receipt_sha256"], "Receipt")
        if independent_review is not None:
            bound_context(builder, receipt)
            _locked(document["independent_review_path"], document["independent_review_sha256"], "Independent Review")
    write_once_json(output, document, binding_check=check_review_sources)
    return output


def gate(
    *,
    run_root: Path,
    builder_handoff_path: Path,
    review_path: Path,
    created_at: str,
    t01_compatibility: bool = False,
) -> Path:
    builder_handoff_path = builder_handoff_path.resolve()
    review_path = review_path.resolve()
    builder = validate_builder_handoff(load_json(builder_handoff_path))
    entry_path = Path(builder["entry_path"])
    entry, entry_sha256 = load_locked_entry(entry_path, builder["entry_sha256"])
    review = validate_review(load_json(review_path))
    if review["prototype_run_id"] != builder["prototype_run_id"]:
        raise IntegrityError("Review and Builder Handoff runs differ")
    if review["review_context_id"] == builder["builder_context_id"]:
        raise GateBlockedError("Prototype Review requires an independent context")
    if review["reviewed_builder_handoff_sha256"] != sha256_file(builder_handoff_path):
        raise IntegrityError("Review does not bind the current Builder Handoff")
    if review["reviewed_prototype_sha256"] != builder["prototype_sha256"]:
        raise IntegrityError("Review does not bind the current Prototype")
    if builder.get("manifest_contract_version") == "4.0.0":
        if review.get("manifest_contract_version") != "4.0.0":
            raise IntegrityError("Review does not bind Manifest v4")
        for field in (
            "reader_snapshot_path", "reader_snapshot_sha256", "reader_metadata_path",
            "reader_metadata_sha256", "previous_manifest_path", "previous_manifest_sha256",
            "previous_snapshot_path", "previous_snapshot_sha256",
            "lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256",
            "product_freeze_manifest_path", "product_freeze_manifest_sha256",
        ):
            if review[field] != builder[field]:
                raise IntegrityError(f"Review changed Builder binding: {field}")
        for path_field, hash_field, label in (
            ("reader_snapshot_path", "reader_snapshot_sha256", "Reader Snapshot"),
            ("reader_metadata_path", "reader_metadata_sha256", "Reader metadata"),
            ("independent_review_path", "independent_review_sha256", "Independent Review v2"),
        ):
            _locked(review[path_field], review[hash_field], label)
        for path_field, hash_field, label in (
            ("previous_manifest_path", "previous_manifest_sha256", "Previous Manifest"),
            ("previous_snapshot_path", "previous_snapshot_sha256", "Previous Snapshot"),
            ("lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256", "Lineage baseline receipt"),
            ("product_freeze_manifest_path", "product_freeze_manifest_sha256", "Product Freeze"),
        ):
            if review[path_field] is not None:
                _locked(review[path_field], review[hash_field], label)
        _validate_independent_review_v2(load_json(Path(review["independent_review_path"])), builder)
    if review["verdict"] != "pass":
        raise GateBlockedError("independent Prototype Review did not pass")
    for field in (
        "baseline_evidence",
        "high_fidelity_readiness",
        "automated_validation",
        "prototype_sync",
    ):
        if builder[field] != "pass":
            raise GateBlockedError(f"{field} did not pass")
    if builder["pending_item_ids"] or builder["blocker_ids"]:
        raise GateBlockedError("Builder Handoff has unresolved items")

    locked_pairs = (
        ("entry_path", "entry_sha256", "Prototype Entry"),
        ("source_set_path", "source_set_sha256", "Prototype Source Set"),
        ("snapshot_path", "snapshot_sha256", "Run Snapshot"),
        ("prd_artifact_path", "prd_artifact_sha256", "PRD artifact"),
        ("review_artifact_path", "review_artifact_sha256", "PRD Review artifact"),
        ("prototype_path", "prototype_sha256", "Prototype"),
        ("validation_receipt_path", "validation_receipt_sha256", "Validation Receipt"),
    )
    for path_field, hash_field, label in locked_pairs:
        _locked(builder[path_field], builder[hash_field], label)
    for path_field, hash_field, label in (
        ("manifest_path", "manifest_sha256", "v3 Manifest"),
        ("evidence_path", "evidence_sha256", "Evidence Bundle"),
        ("independent_review_path", "independent_review_sha256", "v3 Independent Review"),
    ):
        if builder[path_field] is not None:
            _locked(builder[path_field], builder[hash_field], label)

    source_set = validate_source_set(load_json(Path(builder["source_set_path"])))
    snapshot = validate_snapshot(load_json(Path(builder["snapshot_path"])))
    receipt = validate_receipt(load_json(Path(builder["validation_receipt_path"])))
    if len({
        entry["prototype_run_id"],
        source_set["prototype_run_id"],
        snapshot["prototype_run_id"],
        receipt["prototype_run_id"],
        builder["prototype_run_id"],
    }) != 1:
        raise IntegrityError("Gate artifacts belong to different runs")
    revalidate_snapshot_release(snapshot)
    assert_receipt_release_binding(snapshot, receipt)
    execution = _execution(snapshot, builder, run_root, t01_compatibility)
    if review.get("execution") != execution:
        raise IntegrityError("Review execution provenance differs from Gate")
    for source in source_set["sources"]:
        _locked(source["path"], source["sha256"], source["role"])
    _locked(snapshot["source_registry_path"], snapshot["source_registry_sha256"], "Source Registry")
    if builder.get("manifest_contract_version") == "4.0.0":
        _revalidate_v4_gate(builder, review, bound_context(builder, receipt))

    document = {
        "schema_version": 1,
        "prototype_run_id": builder["prototype_run_id"],
        "review_context_id": review["review_context_id"],
        "skill_name": snapshot["skill_name"],
        "skill_version": snapshot["skill_version"],
        "skill_sha256": snapshot["skill_sha256"],
        "skill_content_sha256": snapshot["skill_content_sha256"],
        "pack_name": snapshot["pack_name"],
        "pack_version": snapshot["pack_version"],
        "pack_sha256": snapshot["pack_sha256"],
        "pack_content_sha256": snapshot["pack_content_sha256"],
        "source_registry_version": snapshot["source_registry_version"],
        "source_registry_sha256": snapshot["source_registry_sha256"],
        "entry_path": builder["entry_path"],
        "entry_sha256": builder["entry_sha256"],
        "prd_artifact_path": builder["prd_artifact_path"],
        "prd_artifact_sha256": builder["prd_artifact_sha256"],
        "review_artifact_path": builder["review_artifact_path"],
        "review_artifact_sha256": builder["review_artifact_sha256"],
        "prototype_path": builder["prototype_path"],
        "prototype_sha256": builder["prototype_sha256"],
        "manifest_path": builder["manifest_path"],
        "manifest_sha256": builder["manifest_sha256"],
        "evidence_path": builder["evidence_path"],
        "evidence_sha256": builder["evidence_sha256"],
        "independent_review_path": builder["independent_review_path"],
        "independent_review_sha256": builder["independent_review_sha256"],
        "builder_handoff_path": str(builder_handoff_path),
        "builder_handoff_sha256": sha256_file(builder_handoff_path),
        "prototype_review_path": str(review_path),
        "prototype_review_sha256": sha256_file(review_path),
        "baseline_evidence": builder["baseline_evidence"],
        "high_fidelity_readiness": builder["high_fidelity_readiness"],
        "automated_validation": builder["automated_validation"],
        "independent_review": "pass",
        "prototype_sync": builder["prototype_sync"],
        "coverage": builder["coverage"],
        "blocker_ids": [],
        "gate_status": "pass",
        "created_at": created_at,
    }
    if builder.get("manifest_contract_version") == "4.0.0":
        document.update({key: builder[key] for key in (
            "manifest_contract_version", "reader_snapshot_path", "reader_snapshot_sha256",
            "reader_metadata_path", "reader_metadata_sha256", "previous_manifest_path",
            "previous_manifest_sha256", "previous_snapshot_path", "previous_snapshot_sha256",
            "lineage_baseline_receipt_path", "lineage_baseline_receipt_sha256",
            "product_freeze_manifest_path", "product_freeze_manifest_sha256",
        )})
        document["independent_review_path"] = review["independent_review_path"]
        document["independent_review_sha256"] = review["independent_review_sha256"]
    if execution is not None:
        document["execution"] = execution
    validate_prototype_handoff(document)
    output = safe_run_path(run_root, builder["prototype_run_id"], "prototype-handoff.json")
    def check_gate_sources():
        load_locked_entry(entry_path, entry_sha256)
        assert_locked_sources(snapshot, prototype_path=Path(builder["prototype_path"]))
        _locked(str(builder_handoff_path), document["builder_handoff_sha256"], "Builder Handoff")
        _locked(str(review_path), document["prototype_review_sha256"], "Prototype Review")
        _locked(builder["validation_receipt_path"], builder["validation_receipt_sha256"], "Receipt")
        if builder.get("manifest_contract_version") == "4.0.0":
            bound_context(builder, receipt)
            _locked(review["independent_review_path"], review["independent_review_sha256"], "Independent Review")
    write_once_json(output, document, binding_check=check_gate_sources)
    return output
