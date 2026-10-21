from __future__ import annotations

from .contracts import (
    AnalysisProposal,
    CandidateApproval,
    OnboardingSession,
    SessionStatus,
    SourceObservation,
    SourceOperation,
    validate_public_payload,
)
from .errors import OnboardingError


class ProposalValidator:
    def validate(
        self,
        bundle: AnalysisProposal,
        session: OnboardingSession,
        approvals: tuple[CandidateApproval, ...],
        observations: tuple[SourceObservation, ...],
    ) -> AnalysisProposal:
        bundle.validate()
        if session.status is not SessionStatus.SOURCES_CONFIRMED or bundle.onboarding_id != session.onboarding_id:
            raise OnboardingError("governance_incomplete")
        approval_by_id = {item.candidate_id: item for item in approvals}
        observation_by_id = {item.candidate_id: item for item in observations}
        if len(approval_by_id) != len(approvals) or len(observation_by_id) != len(observations):
            raise OnboardingError("governance_incomplete")
        proposal_ids: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        for proposal in bundle.proposals:
            approval = approval_by_id.get(proposal.candidate_id)
            observation = observation_by_id.get(proposal.candidate_id)
            if approval is None or observation is None or SourceOperation.CONTENT_ANALYSIS not in approval.approved_operations:
                raise OnboardingError("source_not_approved")
            if proposal.based_on_revision != observation.revision:
                raise OnboardingError("revision_changed")
            exact = {(item.normalized_path, item.block_id, item.evidence_hash) for item in observation.headings}
            if any((item.normalized_path, item.block_id, item.evidence_hash) not in exact for item in proposal.evidence_refs):
                raise OnboardingError("evidence_unresolvable")
            pair = (proposal.candidate_id, proposal.suggested_surface_id)
            if proposal.proposal_id in proposal_ids or pair in pairs:
                raise OnboardingError("analysis_failed")
            proposal_ids.add(proposal.proposal_id)
            pairs.add(pair)
        validate_public_payload(bundle.to_mapping())
        return AnalysisProposal.from_mapping(bundle.to_mapping())
