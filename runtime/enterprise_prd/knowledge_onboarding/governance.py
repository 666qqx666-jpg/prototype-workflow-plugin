from __future__ import annotations

from enterprise_prd.baseline_registry.contracts import (
    BindingState,
    DependencyState,
    ObservationState,
)
from enterprise_prd.baseline_registry.snapshot import RegistrySnapshot
from enterprise_prd.errors import ContractError

from .contracts import (
    AnalysisProposal,
    BindingDecision,
    GovernanceSnapshot,
    SourceObservation,
    validate_public_payload,
)
from .errors import OnboardingError


def _fail(code: str) -> None:
    raise OnboardingError(code)


def _validate_proposal_decisions(governance: GovernanceSnapshot, proposals: AnalysisProposal) -> dict[str, object]:
    proposal_by_id = {item.proposal_id: item for item in proposals.proposals}
    if len(proposal_by_id) != len(proposals.proposals):
        _fail("governance_incomplete")
    seen: set[str] = set()
    for candidate in governance.candidates:
        for binding in candidate.bindings:
            if binding.decision is BindingDecision.MANUAL:
                if binding.proposal_id is not None:
                    _fail("governance_incomplete")
                continue
            proposal = proposal_by_id.get(binding.proposal_id)
            if proposal is None or binding.proposal_id in seen or proposal.candidate_id != binding.candidate_id:
                _fail("governance_incomplete")
            if proposal.based_on_revision != binding.based_on_revision:
                _fail("governance_incomplete")
            if binding.decision is BindingDecision.ACCEPT and (
                proposal.suggested_surface_id != binding.surface_id
                or proposal.suggested_display_path != binding.display_path
                or proposal.suggested_change_mode != binding.change_mode
                or proposal.impact_summary != binding.impact_summary
                or proposal.evidence_refs != binding.evidence_refs
            ):
                _fail("governance_incomplete")
            seen.add(binding.proposal_id)
    for rejected in governance.rejected_proposals:
        proposal = proposal_by_id.get(rejected.proposal_id)
        if proposal is None or rejected.proposal_id in seen or proposal.candidate_id != rejected.candidate_id:
            _fail("governance_incomplete")
        seen.add(rejected.proposal_id)
    if seen != set(proposal_by_id):
        _fail("governance_incomplete")
    return proposal_by_id


def _project_registry_mapping(
    raw: dict,
    expected_registry_view_hash: str,
    governance: GovernanceSnapshot,
    observations: tuple[SourceObservation, ...],
    proposals: AnalysisProposal,
    included_candidate_ids: tuple[str, ...] | None = None,
) -> RegistrySnapshot:
    if governance.expected_registry_view_hash != expected_registry_view_hash:
        _fail("registry_view_changed")
    observation_by_id = {item.candidate_id: item for item in observations}
    proposal_by_id = _validate_proposal_decisions(governance, proposals)
    if len(observation_by_id) != len(observations):
        _fail("governance_incomplete")
    known_candidates = {item.candidate_id for item in governance.candidates}
    included = known_candidates if included_candidate_ids is None else set(included_candidate_ids)
    if not included <= known_candidates:
        _fail("governance_incomplete")
    base_ids = {item["source_id"] for item in raw["documents"]}
    base_revisions = {(item["source_id"], item["revision"]) for item in raw["revisions"]}
    stable_by_candidate = {item.candidate_id: item.document.source_id for item in governance.candidates}
    known_stable = set(stable_by_candidate.values())
    included_stable = {stable_by_candidate[item] for item in included}
    for candidate in governance.candidates:
        if candidate.candidate_id not in included:
            continue
        observation = observation_by_id.get(candidate.candidate_id)
        if observation is None:
            _fail("governance_incomplete")
        document = candidate.document
        if document.lifecycle_status.value == "unconfirmed" or document.observed_revision != observation.revision:
            _fail("governance_incomplete")
        if document.source_id in base_ids or (document.source_id, observation.revision) in base_revisions:
            _fail("identity_conflict")
        raw["documents"].append(
            {
                "source_id": document.source_id,
                "display_name": document.display_name,
                "adapter": "local-snapshot",
                "locator_ref": f"onboarding:{candidate.candidate_id}",
                "lifecycle_status": document.lifecycle_status.value,
                "observed_revision": document.observed_revision,
                "active_revision": document.active_revision,
                "released_revision": document.released_revision,
                "record_version": 1,
            }
        )
        raw["revisions"].append(
            {
                "source_id": document.source_id,
                "revision": observation.revision,
                "observed_at": observation.observed_at,
                "source_kind": "local-snapshot",
                "content_bytes": observation.content_bytes,
                "observation_state": ObservationState.AVAILABLE.value,
            }
        )
        for binding in candidate.bindings:
            if binding.decision is not BindingDecision.MANUAL:
                proposal = proposal_by_id.get(binding.proposal_id)
                if proposal is None or (
                    proposal.based_on_revision != binding.based_on_revision
                    or proposal.evidence_refs != binding.evidence_refs
                ):
                    _fail("governance_incomplete")
            exact = {(item.normalized_path, item.block_id, item.evidence_hash) for item in observation.headings}
            if any((item.normalized_path, item.block_id, item.evidence_hash) not in exact for item in binding.evidence_refs):
                _fail("evidence_unresolvable")
            existing_surface = next((item for item in raw["surfaces"] if item["surface_id"] == binding.surface_id), None)
            if existing_surface is not None:
                if tuple(existing_surface["display_path"]) != binding.display_path:
                    _fail("governance_incomplete")
            else:
                raw["surfaces"].append({"surface_id": binding.surface_id, "display_path": list(binding.display_path), "record_version": 1})
            raw["bindings"].append(
                {
                    "source_id": document.source_id,
                    "surface_id": binding.surface_id,
                    "state": BindingState.CONFIRMED.value,
                    "change_mode": binding.change_mode.value,
                    "impact_summary": binding.impact_summary,
                    "based_on_revision": binding.based_on_revision,
                    "confirmed_by": binding.confirmed_by,
                    "confirmed_at": binding.confirmed_at,
                    "record_version": 1,
                }
            )
    available_ids = base_ids | included_stable
    for dependency in governance.dependencies:
        if dependency.source_id in included_stable:
            if dependency.depends_on not in available_ids:
                _fail("governance_incomplete")
            raw["dependencies"].append(
                {
                    "source_id": dependency.source_id,
                    "depends_on": dependency.depends_on,
                    "state": DependencyState.CONFIRMED.value,
                    "rationale": dependency.rationale,
                    "confirmed_by": dependency.confirmed_by,
                    "confirmed_at": dependency.confirmed_at,
                    "record_version": 1,
                }
            )
    for conflict in governance.conflicts:
        conflict_ids = set(conflict.source_ids)
        if not conflict_ids & known_stable:
            _fail("governance_incomplete")
        if not conflict_ids & included_stable:
            continue
        if not conflict_ids <= available_ids:
            _fail("governance_incomplete")
        raw["conflicts"].append(
            {
                "resolution_id": conflict.resolution_id,
                "surface_id": conflict.surface_id,
                "source_ids": list(conflict.source_ids),
                "scope": conflict.scope.value,
                "decision": conflict.decision.value,
                "winner_source_id": conflict.winner_source_id,
                "excluded_source_ids": list(conflict.excluded_source_ids),
                "state": BindingState.CONFIRMED.value,
                "confirmed_by": conflict.confirmed_by,
                "confirmed_at": conflict.confirmed_at,
                "record_version": 1,
            }
        )
    validate_public_payload(raw, allow_opaque_locator_ref=True)
    try:
        return RegistrySnapshot.from_mapping(raw)
    except ContractError as exc:
        raise OnboardingError("governance_incomplete") from exc


def project_registry(
    base: RegistrySnapshot,
    governance: GovernanceSnapshot,
    observations: tuple[SourceObservation, ...],
    proposals: AnalysisProposal,
    included_candidate_ids: tuple[str, ...] | None = None,
) -> RegistrySnapshot:
    try:
        base.validate()
        governance.validate()
        proposals.validate()
    except ContractError as exc:
        raise OnboardingError("governance_incomplete") from exc
    return _project_registry_mapping(
        base.to_mapping(),
        base.view_hash,
        governance,
        observations,
        proposals,
        included_candidate_ids,
    )


def project_genesis_registry(
    governance: GovernanceSnapshot,
    observations: tuple[SourceObservation, ...],
    proposals: AnalysisProposal,
    included_candidate_ids: tuple[str, ...] | None = None,
) -> RegistrySnapshot:
    from .genesis import GENESIS_BASE_MAPPING, GENESIS_BASE_VIEW_HASH

    try:
        governance.validate()
        proposals.validate()
    except ContractError as exc:
        raise OnboardingError("governance_incomplete") from exc
    raw = {key: list(value) if type(value) is list else value for key, value in GENESIS_BASE_MAPPING.items()}
    return _project_registry_mapping(
        raw,
        GENESIS_BASE_VIEW_HASH,
        governance,
        observations,
        proposals,
        included_candidate_ids,
    )


def observe_new_revision_without_promotion(current: RegistrySnapshot, observation: SourceObservation) -> RegistrySnapshot:
    matches = tuple(item for item in current.documents if item.locator_ref == f"onboarding:{observation.candidate_id}")
    if len(matches) != 1:
        _fail("identity_conflict")
    document = matches[0]
    from enterprise_prd.baseline_registry.contracts import RevisionRecord

    record = RevisionRecord(document.source_id, observation.revision, observation.observed_at, "local-snapshot", observation.content_bytes, ObservationState.AVAILABLE)
    return current.observe(record, expected_record_version=document.record_version)
