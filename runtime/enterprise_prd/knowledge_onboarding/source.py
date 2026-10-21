from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.local_snapshot.directory import GeneratedSnapshotDirectory
from enterprise_prd.local_snapshot.snapshot import (
    SnapshotStore,
    document_identity_sha256,
    heading_block_id,
    validate_private_root,
)

from .contracts import (
    CandidateApproval,
    EvidenceRef,
    HeadingObservation,
    SourceObservation,
    SourceOperation,
)
from .errors import OnboardingError


@dataclass(frozen=True)
class AnalysisMaterial:
    candidate_id: str
    revision: str
    title: str
    document: str
    headings: tuple[HeadingObservation, ...]


def _unique_approvals(values: tuple[CandidateApproval, ...]) -> dict[str, CandidateApproval]:
    if type(values) is not tuple or not values:
        raise OnboardingError("source_not_approved")
    for item in values:
        item.validate()
    if len({item.candidate_id for item in values}) != len(values) or len({item.snapshot_source_id for item in values}) != len(values) or len({item.expected_locator_ref for item in values}) != len(values):
        raise OnboardingError("duplicate_source")
    return {item.candidate_id: item for item in values}


class ApprovedSnapshotSource:
    def __init__(self, private_snapshot_root: Path, approvals: tuple[CandidateApproval, ...]) -> None:
        self.private_snapshot_root = validate_private_root(private_snapshot_root)
        self.approvals = _unique_approvals(approvals)

    def _approved(self, approval: CandidateApproval) -> CandidateApproval:
        known = self.approvals.get(approval.candidate_id)
        if known != approval:
            raise OnboardingError("source_not_approved")
        return known

    def _entry(self, approval: CandidateApproval):
        matches = tuple(
            item
            for item in GeneratedSnapshotDirectory(self.private_snapshot_root).capture().entries
            if item.source_id == approval.snapshot_source_id and item.locator_ref == approval.expected_locator_ref
        )
        if len(matches) != 1:
            raise OnboardingError("source_not_approved")
        return matches[0]

    def _load_entry(self, entry):
        return SnapshotStore(self.private_snapshot_root / "snapshots").load(entry)

    @staticmethod
    def _headings(approval: CandidateApproval, revision: str, record) -> tuple[HeadingObservation, ...]:
        values = []
        for item in record.parsed.headings:
            block_id = heading_block_id(approval.snapshot_source_id, revision, item.normalized_path)
            evidence_hash = sha256_bytes(canonical_json({"candidate_id": approval.candidate_id, "revision": revision, "normalized_path": item.normalized_path, "block_id": block_id}))
            values.append(HeadingObservation(item.raw_path, item.normalized_path, block_id, evidence_hash))
        return tuple(values)

    def _observe_exact(self, approval: CandidateApproval, observed_at: str) -> SourceObservation:
        if SourceOperation.SOURCE_VERIFICATION not in approval.approved_operations:
            raise OnboardingError("source_not_approved")
        record = self._load_entry(self._entry(approval))
        revision = record.manifest["content_sha256"]
        result = SourceObservation(
            approval.candidate_id,
            approval.snapshot_source_id,
            document_identity_sha256(approval.snapshot_source_id),
            record.parsed.title,
            revision,
            record.manifest["content_bytes"],
            self._headings(approval, revision, record),
            observed_at,
        )
        result.validate()
        return result

    def _load_exact_analysis(self, approval: CandidateApproval, observation: SourceObservation) -> AnalysisMaterial:
        if SourceOperation.CONTENT_ANALYSIS not in approval.approved_operations:
            raise OnboardingError("source_not_approved")
        if observation.candidate_id != approval.candidate_id or observation.snapshot_source_id != approval.snapshot_source_id:
            raise OnboardingError("revision_changed")
        record = self._load_entry(self._entry(approval))
        revision = record.manifest["content_sha256"]
        if revision != observation.revision:
            raise OnboardingError("revision_changed")
        return AnalysisMaterial(approval.candidate_id, revision, record.parsed.title, record.document, self._headings(approval, revision, record))

    def _resolve_exact_heading(self, approval: CandidateApproval, observation: SourceObservation, normalized_path: str) -> EvidenceRef:
        if observation.candidate_id != approval.candidate_id or observation.snapshot_source_id != approval.snapshot_source_id:
            raise OnboardingError("evidence_unresolvable")
        matches = tuple(item for item in observation.headings if item.normalized_path == normalized_path)
        if len(matches) != 1:
            raise OnboardingError("evidence_unresolvable")
        item = matches[0]
        return EvidenceRef(item.normalized_path, item.block_id, item.evidence_hash)

    def observe(self, approval: CandidateApproval, observed_at: str) -> SourceObservation:
        return self._observe_exact(self._approved(approval), observed_at)

    def load_analysis_material(self, approval: CandidateApproval, observation: SourceObservation) -> AnalysisMaterial:
        return self._load_exact_analysis(self._approved(approval), observation)

    def resolve_evidence(self, approval: CandidateApproval, observation: SourceObservation, normalized_path: str) -> EvidenceRef:
        return self._resolve_exact_heading(self._approved(approval), observation, normalized_path)
