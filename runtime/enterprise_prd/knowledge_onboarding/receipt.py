from __future__ import annotations

import json
import os
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import ContractError
from enterprise_prd.baseline_registry.contracts import BindingState, DependencyState
from enterprise_prd.baseline_registry.snapshot import RegistrySnapshot

from .contracts import (
    CommitSelection,
    GovernanceSnapshot,
    OnboardingReceipt,
    validate_public_payload,
)
from .errors import OnboardingError
from .workspace import OnboardingWorkspace


def build_receipt(
    governance: GovernanceSnapshot,
    selection: CommitSelection,
    registry_view_hash_before: str,
    registry_after: RegistrySnapshot,
    production_baseline_dry_run_hash: str,
) -> OnboardingReceipt:
    included = set(selection.included_candidate_ids)
    selected = tuple(item for item in governance.candidates if item.candidate_id in included)
    stable = {item.candidate_id: item.document.source_id for item in selected}
    evidence = tuple(
        sorted(
            (
                {
                    "source_id": candidate.document.source_id,
                    "surface_id": binding.surface_id,
                    "based_on_revision": binding.based_on_revision,
                    "normalized_paths": sorted(item.normalized_path for item in binding.evidence_refs),
                    "evidence_hashes": sorted(item.evidence_hash for item in binding.evidence_refs),
                }
                for candidate in selected
                for binding in candidate.bindings
            ),
            key=lambda item: (item["source_id"], item["surface_id"]),
        )
    )
    source_ids = set(stable.values())
    after_dependencies = {
        (item.source_id, item.depends_on): item
        for item in registry_after.dependencies
    }
    included_dependencies = tuple(
        item
        for item in governance.dependencies
        if item.source_id in source_ids
        and (item.source_id, item.depends_on) in after_dependencies
    )
    for item in included_dependencies:
        stored = after_dependencies[(item.source_id, item.depends_on)]
        if (
            stored.state is not DependencyState.CONFIRMED
            or stored.rationale != item.rationale
            or stored.confirmed_by != item.confirmed_by
            or stored.confirmed_at != item.confirmed_at
        ):
            raise OnboardingError("receipt_recovery_conflict")
    dependencies = tuple(
        sorted(
            (
                {"source_id": item.source_id, "depends_on": item.depends_on}
                for item in included_dependencies
            ),
            key=lambda item: (item["source_id"], item["depends_on"]),
        )
    )

    after_conflicts = {item.resolution_id: item for item in registry_after.conflicts}
    included_conflicts = tuple(
        item
        for item in governance.conflicts
        if set(item.source_ids) & source_ids and item.resolution_id in after_conflicts
    )
    for item in included_conflicts:
        stored = after_conflicts[item.resolution_id]
        if (
            stored.surface_id != item.surface_id
            or stored.source_ids != item.source_ids
            or stored.scope is not item.scope
            or stored.decision is not item.decision
            or stored.winner_source_id != item.winner_source_id
            or stored.excluded_source_ids != item.excluded_source_ids
            or stored.state is not BindingState.CONFIRMED
            or stored.confirmed_by != item.confirmed_by
            or stored.confirmed_at != item.confirmed_at
        ):
            raise OnboardingError("receipt_recovery_conflict")
    conflicts = tuple(
        sorted(
            (
                {
                    "resolution_id": item.resolution_id,
                    "source_ids": list(item.source_ids),
                    "decision": item.decision.value,
                }
                for item in included_conflicts
            ),
            key=lambda item: item["resolution_id"],
        )
    )
    excluded = tuple({"candidate_id": item.candidate_id, "reason": item.reason.value} for item in selection.excluded_candidates)
    payload = {
        "schema_version": 2,
        "onboarding_id": governance.onboarding_id,
        "registry_view_hash_before": registry_view_hash_before,
        "registry_view_hash_after": registry_after.view_hash,
        "committed_source_ids": sorted(source_ids),
        "confirmed_binding_evidence": list(evidence),
        "confirmed_dependencies": list(dependencies),
        "confirmed_conflicts": list(conflicts),
        "excluded_candidates": list(excluded),
        "production_baseline_dry_run_hash": production_baseline_dry_run_hash,
        "confirmed_by": selection.confirmed_by,
        "confirmed_at": selection.confirmed_at,
    }
    receipt_id = "onboarding-" + sha256_bytes(canonical_json(payload))
    receipt = OnboardingReceipt.from_mapping({"receipt_id": receipt_id, **payload})
    receipt.validate()
    return receipt


class ReceiptStore:
    def __init__(self, workspace: OnboardingWorkspace) -> None:
        self.workspace = workspace

    def save(self, onboarding_id: str, receipt: OnboardingReceipt) -> Path:
        receipt.validate()
        validate_public_payload(receipt.to_mapping())
        path = self.workspace.private_path(onboarding_id, "receipt.json")
        payload = canonical_json(receipt.to_mapping()) + b"\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            try:
                current = path.read_bytes()
            except OSError as exc:
                raise OnboardingError("receipt_recovery_conflict") from exc
            if current != payload:
                raise OnboardingError("receipt_recovery_conflict")
            return path
        except OSError as exc:
            raise OnboardingError("receipt_recovery_conflict") from exc
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return path

    def load(self, onboarding_id: str) -> OnboardingReceipt:
        path = self.workspace.private_path(onboarding_id, "receipt.json")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise OnboardingError("receipt_recovery_conflict") from exc
        try:
            payload = b""
            while len(payload) <= 1024 * 1024:
                chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(payload)))
                if not chunk:
                    break
                payload += chunk
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("receipt is invalid") from exc
        receipt = OnboardingReceipt.from_mapping(value)
        if canonical_json(receipt.to_mapping()) + b"\n" != payload:
            raise ContractError("receipt is not canonical")
        return receipt
