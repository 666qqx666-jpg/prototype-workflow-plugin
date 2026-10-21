from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

import yaml

from enterprise_prd.baseline_registry.store import RegistryStore
from enterprise_prd.canonical import canonical_json

from .artifacts import ArtifactStore
from .contracts import AnalysisProposal, CandidateApproval, CommitSelection, GovernanceSnapshot
from .genesis import registry_presence
from .source import ApprovedSnapshotSource
from .workflow import OnboardingWorkflow, ReceiptRecoveryService
from .workspace import OnboardingWorkspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "knowledge-onboarding-0.2.0"


def _input(path_text: str) -> dict:
    path = Path(path_text)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("input file is invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        fixture = path.resolve().is_relative_to(FIXTURE_ROOT.resolve())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024 or (info.st_mode & 0o777 != 0o600 and not fixture):
            raise ValueError("input file is invalid")
        payload = b""
        while len(payload) <= 1024 * 1024:
            chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
    finally:
        os.close(descriptor)
    value = yaml.safe_load(payload.decode("utf-8"))
    if type(value) is not dict:
        raise ValueError("input file is invalid")
    return value


def _approvals(value: dict) -> tuple[CandidateApproval, ...]:
    if set(value) != {"schema_version", "approvals"} or value["schema_version"] != 1 or type(value["approvals"]) is not list:
        raise ValueError("approvals file is invalid")
    return tuple(CandidateApproval.from_mapping(item) for item in value["approvals"])


def _state_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--onboarding-id", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--registry-root", required=True)


def _source_common(parser: argparse.ArgumentParser) -> None:
    _state_common(parser)
    parser.add_argument("--snapshot-root", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m enterprise_prd.knowledge_onboarding.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    _source_common(create)
    create.add_argument("--target-surface-id", required=True)
    create.add_argument("--expected-registry-view-hash", required=True)
    create.add_argument("--approvals", required=True)
    genesis = subparsers.add_parser("create-genesis")
    _source_common(genesis)
    genesis.add_argument("--target-surface-id", required=True)
    genesis.add_argument("--approvals", required=True)
    observe = subparsers.add_parser("observe")
    _source_common(observe)
    observe.add_argument("--observed-at", required=True)
    proposals = subparsers.add_parser("import-proposals")
    _source_common(proposals)
    proposals.add_argument("--proposals", required=True)
    governance = subparsers.add_parser("confirm-governance")
    _source_common(governance)
    governance.add_argument("--governance", required=True)
    full = subparsers.add_parser("dry-run-full")
    _source_common(full)
    full.add_argument("--created-at", required=True)
    selection = subparsers.add_parser("select")
    _source_common(selection)
    selection.add_argument("--selection", required=True)
    selected = subparsers.add_parser("dry-run-selected")
    _source_common(selected)
    selected.add_argument("--created-at", required=True)
    _source_common(subparsers.add_parser("commit"))
    status = subparsers.add_parser("status")
    _state_common(status)
    status.add_argument("--snapshot-root")
    _state_common(subparsers.add_parser("recover-receipt"))
    return parser


def _existing_approvals(workspace: OnboardingWorkspace, onboarding_id: str) -> tuple[CandidateApproval, ...]:
    session, _ = workspace.load(onboarding_id)
    value = ArtifactStore(workspace).load(onboarding_id, "approvals", session.approvals_hash)
    return _approvals(value)


def _result(workspace: OnboardingWorkspace, registry: RegistryStore, onboarding_id: str) -> dict:
    session, state_hash = workspace.load(onboarding_id)
    registry_state, registry_view_hash = registry_presence(registry)
    return {
        "onboarding_id": session.onboarding_id,
        "status": session.status.value,
        "state_hash": state_hash,
        "registry_state": registry_state,
        "registry_view_hash": registry_view_hash,
        "receipt_id": session.receipt_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        workspace = OnboardingWorkspace(Path(args.workspace_root))
        registry = RegistryStore(Path(args.registry_root))
        if args.command == "recover-receipt":
            ReceiptRecoveryService(workspace, registry).recover(args.onboarding_id)
            sys.stdout.buffer.write(
                canonical_json(_result(workspace, registry, args.onboarding_id)) + b"\n"
            )
            return 0
        if args.command == "status":
            sys.stdout.buffer.write(canonical_json(_result(workspace, registry, args.onboarding_id)) + b"\n")
            return 0
        if args.command == "create":
            approvals = _approvals(_input(args.approvals))
        elif args.command == "create-genesis":
            state, _ = registry_presence(registry)
            if state != "absent":
                from .errors import OnboardingError

                raise OnboardingError("registry_already_exists")
            approvals = _approvals(_input(args.approvals))
        else:
            approvals = _existing_approvals(workspace, args.onboarding_id)
        source = ApprovedSnapshotSource(Path(args.snapshot_root), approvals)
        workflow = OnboardingWorkflow(workspace, registry, source)
        if args.command == "create":
            workflow.create(args.onboarding_id, args.target_surface_id, args.expected_registry_view_hash, approvals)
        elif args.command == "create-genesis":
            workflow.create_genesis(args.onboarding_id, args.target_surface_id, approvals)
        elif args.command == "observe":
            workflow.observe_sources(args.onboarding_id, args.observed_at)
        elif args.command == "import-proposals":
            workflow.import_proposals(args.onboarding_id, AnalysisProposal.from_mapping(_input(args.proposals)))
        elif args.command == "confirm-governance":
            workflow.confirm_governance(args.onboarding_id, GovernanceSnapshot.from_mapping(_input(args.governance)))
        elif args.command == "dry-run-full":
            workflow.dry_run_full(args.onboarding_id, args.created_at)
        elif args.command == "select":
            workflow.select_submission(args.onboarding_id, CommitSelection.from_mapping(_input(args.selection)))
        elif args.command == "dry-run-selected":
            workflow.dry_run_selected(args.onboarding_id, args.created_at)
        elif args.command == "commit":
            workflow.commit(args.onboarding_id)
        else:
            raise ValueError("command is invalid")
        sys.stdout.buffer.write(canonical_json(_result(workspace, registry, args.onboarding_id)) + b"\n")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
