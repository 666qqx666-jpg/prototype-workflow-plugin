from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import COVERAGE_LEVELS, DELIVERY_LEVELS
from .errors import ContractError, IntegrityError, RestartRequiredError
from .handoff import create_builder_handoff
from .review import create_review, gate
from .start import create_entry, create_source_set, start_run
from .store import load_json, sha256_file
from .validation import validate_output
from .local_sources import capture_local_code, capture_first_baseline, capture_code_collection


def _source(value: str) -> tuple[str, Path]:
    role, separator, raw_path = value.partition("=")
    if separator != "=" or not role or not raw_path:
        raise argparse.ArgumentTypeError("--source must be ROLE=PATH")
    return role, Path(raw_path)


def _emit(field: str, path: Path) -> int:
    print(json.dumps(
        {"status": "pass", field: str(path), "sha256": sha256_file(path)},
        ensure_ascii=False,
    ))
    return 0


def _emit_restart_required(prototype_run_id: str) -> int:
    print(json.dumps({
        "status": "restart_required",
        "prototype_run_id": prototype_run_id,
    }))
    return 6


def register(subparsers: argparse._SubParsersAction) -> None:
    prototype = subparsers.add_parser("prototype")
    commands = prototype.add_subparsers(dest="prototype_command", required=True)

    entry = commands.add_parser("entry-create")
    entry.add_argument("--run-root", type=Path, required=True)
    entry.add_argument("--run-id", required=True)
    entry.add_argument("--prd-run-id", required=True)
    entry.add_argument("--review-context-id", required=True)
    entry.add_argument("--prd", type=Path, required=True)
    entry.add_argument("--review", type=Path, required=True)
    entry.add_argument("--delivery", choices=sorted(DELIVERY_LEVELS), required=True)
    entry.add_argument("--coverage", choices=sorted(COVERAGE_LEVELS), required=True)
    entry.add_argument("--readiness-assessment", type=Path, required=True,
        help="Bound prototype-stage assessment; Spec readiness is recorded, not an entry gate")
    entry.add_argument("--authorized-by", required=True)
    entry.add_argument("--authorized-at", required=True)

    local_code = commands.add_parser("local-code-capture",
        help="Capture only an explicitly authorized local project/file selection; never an SSH fallback")
    local_code.add_argument("--run-root", type=Path, required=True)
    local_code.add_argument("--entry", type=Path, required=True)
    local_code.add_argument("--authorization", type=Path, required=True)
    first = commands.add_parser("first-baseline-create",
        help="Lock a no-existing-prototype confirmation before creating the first source")
    first.add_argument("--run-root", type=Path, required=True)
    first.add_argument("--entry", type=Path, required=True)
    first.add_argument("--confirmation", type=Path, required=True)
    collection = commands.add_parser("code-source-collect")
    collection.add_argument("--run-root", type=Path, required=True)
    collection.add_argument("--entry", type=Path, required=True)
    collection.add_argument("--captured-source", type=Path, action="append", required=True)

    source_set = commands.add_parser("source-set-create")
    source_set.add_argument("--run-root", type=Path, required=True)
    source_set.add_argument("--source-set-id", required=True)
    source_set.add_argument("--entry", type=Path, required=True)
    source_set.add_argument("--source", type=_source, action="append", required=True)
    source_set.add_argument("--selected-at", required=True)

    start = commands.add_parser("start")
    start.add_argument("--run-root", type=Path, required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--entry", type=Path, required=True)
    start.add_argument("--source-set", type=Path, required=True)
    start.add_argument("--manifest", type=Path, required=True)
    start.add_argument("--registry", type=Path, required=True)
    start.add_argument("--skill-root", type=Path, required=True)
    start.add_argument("--pack-root", type=Path, required=True)
    start.add_argument("--source-registry", type=Path, required=True)
    start.add_argument("--source-registry-version", required=True)
    start.add_argument("--workspace-root-id", required=True)
    start.add_argument("--created-at", required=True)

    validate = commands.add_parser("validate-output")
    validate.add_argument("--run-root", type=Path, required=True)
    validate.add_argument("--snapshot", type=Path, required=True)
    validate.add_argument("--prototype", type=Path, required=True)
    validate.add_argument("--validator-mode", choices=sorted(DELIVERY_LEVELS), required=True)
    validate.add_argument("--manifest", type=Path)
    validate.add_argument("--evidence", type=Path)
    validate.add_argument("--independent-review", type=Path)
    validate.add_argument("--reader-snapshot", type=Path)
    validate.add_argument("--reader-metadata", type=Path)
    validate.add_argument("--previous-manifest", type=Path)
    validate.add_argument("--previous-snapshot", type=Path)
    validate.add_argument("--lineage-baseline-receipt", type=Path)
    validate.add_argument("--product-freeze-manifest", type=Path)
    validate.add_argument("--skill-root", type=Path, required=True)
    validate.add_argument("--workspace-root", type=Path)
    validate.add_argument("--evidence-root", type=Path, action="append", default=[])
    validate.add_argument("--checked-at", required=True)

    builder = commands.add_parser("builder-handoff")
    builder.add_argument("--run-root", type=Path, required=True)
    builder.add_argument("--builder-handoff-id", required=True)
    builder.add_argument("--builder-context-id", required=True)
    builder.add_argument("--entry", type=Path, required=True)
    builder.add_argument("--source-set", type=Path, required=True)
    builder.add_argument("--snapshot", type=Path, required=True)
    builder.add_argument("--receipt", type=Path, required=True)
    builder.add_argument("--product-freeze-manifest", type=Path,
        help="Optional product convergence Freeze generated after local validation")
    builder.add_argument("--baseline-evidence", choices=("pass", "block"), required=True)
    builder.add_argument("--high-fidelity-readiness", choices=("pass", "block"), required=True)
    builder.add_argument("--prototype-sync", choices=("pass", "block", "not_required"), required=True)
    builder.add_argument("--pending-item-id", action="append", default=[])
    builder.add_argument("--blocker-id", action="append", default=[])
    builder.add_argument("--created-at", required=True)

    review = commands.add_parser("review-check")
    review.add_argument("--run-root", type=Path, required=True)
    review.add_argument("--builder-handoff", type=Path, required=True)
    review.add_argument("--builder-context-id", required=True)
    review.add_argument("--review-context-id", required=True)
    review.add_argument("--verdict", choices=("pass", "block"))
    review.add_argument("--findings-json", type=Path)
    review.add_argument("--independent-review", type=Path)
    review.add_argument("--reviewed-at", required=True)
    review.add_argument("--t01-compatibility", action="store_true", help="Explicit 0.1.19 execution against frozen 0.1.18 Standard records; new output directory required")

    final_gate = commands.add_parser("gate")
    final_gate.add_argument("--run-root", type=Path, required=True)
    final_gate.add_argument("--builder-handoff", type=Path, required=True)
    final_gate.add_argument("--review", type=Path, required=True)
    final_gate.add_argument("--created-at", required=True)
    final_gate.add_argument("--t01-compatibility", action="store_true")


def run(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except RestartRequiredError:
        run_id = getattr(args, "run_id", None)
        if run_id is None:
            path = getattr(args, "entry", None) or getattr(args, "builder_handoff", None)
            run_id = load_json(path)["prototype_run_id"]
        return _emit_restart_required(run_id)
    except IntegrityError:
        source = getattr(args, "snapshot", None) or getattr(args, "builder_handoff", None)
        if source is None:
            raise
        return _emit_restart_required(load_json(source)["prototype_run_id"])
    except ContractError as error:
        if args.prototype_command != "entry-create":
            raise
        print(json.dumps({"status": "blocked", "error": error.code, "message": str(error)}))
        return 6


def _run(args: argparse.Namespace) -> int:
    command = args.prototype_command
    if command == "local-code-capture":
        return _emit("code_source", capture_local_code(run_root=args.run_root,
            entry_path=args.entry, authorization_path=args.authorization))
    if command == "first-baseline-create":
        return _emit("prototype_baseline", capture_first_baseline(run_root=args.run_root,
            entry_path=args.entry, confirmation_path=args.confirmation))
    if command == "code-source-collect":
        return _emit("code_source", capture_code_collection(run_root=args.run_root,
            entry_path=args.entry, source_paths=args.captured_source))
    if command == "entry-create":
        output = create_entry(
            run_root=args.run_root,
            prototype_run_id=args.run_id,
            prd_run_id=args.prd_run_id,
            review_context_id=args.review_context_id,
            prd_path=args.prd,
            review_path=args.review,
            delivery=args.delivery,
            coverage=args.coverage,
            readiness_assessment_path=args.readiness_assessment,
            stage_authorized_by=args.authorized_by,
            stage_authorized_at=args.authorized_at,
        )
        return _emit("entry", output)
    if command == "source-set-create":
        pairs = args.source
        sources = dict(pairs)
        if len(sources) != len(pairs):
            raise ContractError("--source roles must be unique")
        output = create_source_set(
            run_root=args.run_root,
            source_set_id=args.source_set_id,
            entry_path=args.entry,
            sources=sources,
            selected_at=args.selected_at,
        )
        return _emit("source_set", output)
    if command == "start":
        output = start_run(
            run_root=args.run_root,
            prototype_run_id=args.run_id,
            entry_path=args.entry,
            source_set_path=args.source_set,
            manifest_path=args.manifest,
            registry_root=args.registry,
            skill_root=args.skill_root,
            pack_root=args.pack_root,
            source_registry_path=args.source_registry,
            source_registry_version=args.source_registry_version,
            workspace_root_id=args.workspace_root_id,
            created_at=args.created_at,
        )
        return _emit("snapshot", output)
    if command == "validate-output":
        try:
            output = validate_output(
                run_root=args.run_root,
                snapshot_path=args.snapshot,
                prototype_path=args.prototype,
                validator_mode=args.validator_mode,
                manifest_path=args.manifest,
                evidence_path=args.evidence,
                independent_review_path=args.independent_review,
                reader_snapshot_path=args.reader_snapshot,
                reader_metadata_path=args.reader_metadata,
                previous_manifest_path=args.previous_manifest,
                previous_snapshot_path=args.previous_snapshot,
                lineage_baseline_receipt_path=args.lineage_baseline_receipt,
                product_freeze_manifest_path=args.product_freeze_manifest,
                skill_root=args.skill_root,
                workspace_root=args.workspace_root,
                evidence_roots=tuple(args.evidence_root),
                checked_at=args.checked_at,
            )
        except RestartRequiredError:
            snapshot = load_json(args.snapshot)
            return _emit_restart_required(snapshot["prototype_run_id"])
        return _emit("receipt", output)
    if command == "builder-handoff":
        try:
            output = create_builder_handoff(
                run_root=args.run_root,
                builder_handoff_id=args.builder_handoff_id,
                builder_context_id=args.builder_context_id,
                entry_path=args.entry,
                source_set_path=args.source_set,
                snapshot_path=args.snapshot,
                validation_receipt_path=args.receipt,
                product_freeze_manifest_path=args.product_freeze_manifest,
                baseline_evidence=args.baseline_evidence,
                high_fidelity_readiness=args.high_fidelity_readiness,
                prototype_sync=args.prototype_sync,
                pending_item_ids=args.pending_item_id,
                blocker_ids=args.blocker_id,
                created_at=args.created_at,
            )
        except RestartRequiredError:
            snapshot = load_json(args.snapshot)
            return _emit_restart_required(snapshot["prototype_run_id"])
        return _emit("builder_handoff", output)
    if command == "review-check":
        findings = json.loads(args.findings_json.read_text(encoding="utf-8")) if args.findings_json else []
        if type(findings) is not list:
            raise ContractError("findings JSON must contain one array")
        output = create_review(
            run_root=args.run_root,
            builder_handoff_path=args.builder_handoff,
            builder_context_id=args.builder_context_id,
            review_context_id=args.review_context_id,
            verdict=args.verdict,
            findings=findings,
            independent_review_path=args.independent_review,
            reviewed_at=args.reviewed_at,
            t01_compatibility=args.t01_compatibility,
        )
        return _emit("prototype_review", output)
    if command == "gate":
        try:
            output = gate(
                run_root=args.run_root,
                builder_handoff_path=args.builder_handoff,
                review_path=args.review,
                created_at=args.created_at,
                t01_compatibility=args.t01_compatibility,
            )
        except RestartRequiredError:
            return _emit_restart_required(
                load_json(args.builder_handoff)["prototype_run_id"]
            )
        return _emit("prototype_handoff", output)
    raise AssertionError("unreachable Prototype command")
