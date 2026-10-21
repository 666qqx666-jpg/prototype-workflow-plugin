from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar

from enterprise_prd.baseline_registry.contracts import (
    ChangeMode,
    ConflictDecision,
    ConflictScope,
    LifecycleStatus,
    SURFACE_ID_RE,
)
from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, IDENTITY_RE, StrEnum, aware_timestamp, need, runtime_id
from enterprise_prd.errors import ContractError

QUARANTINED_SOURCE_ID = "slm-followup-records"


class SessionStatus(StrEnum):
    DRAFT = "draft"
    SOURCES_CONFIRMED = "sources_confirmed"
    ANALYSIS_READY = "analysis_ready"
    GOVERNANCE_CONFIRMED = "governance_confirmed"
    DRY_RUN_PASSED = "dry_run_passed"
    REGISTRY_COMMITTED = "registry_committed"
    COMPLETED = "completed"


class CandidateStatus(StrEnum):
    CANDIDATE_CREATED = "candidate_created"
    SOURCE_VERIFIED = "source_verified"
    REVISION_LOCKED = "revision_locked"
    ANALYZED = "analyzed"
    GOVERNANCE_CONFIRMED = "governance_confirmed"
    READY_TO_COMMIT = "ready_to_commit"
    COMMITTED = "committed"
    SOURCE_PENDING = "source_pending"
    SOURCE_UNAVAILABLE = "source_unavailable"
    REVISION_CHANGED = "revision_changed"
    IDENTITY_CONFLICT = "identity_conflict"
    ANALYSIS_FAILED = "analysis_failed"
    GOVERNANCE_INCOMPLETE = "governance_incomplete"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


class SourceOperation(StrEnum):
    SOURCE_VERIFICATION = "source_verification"
    CONTENT_ANALYSIS = "content_analysis"

    @classmethod
    def _missing_(cls, value: object):
        raise ContractError("source operation is invalid")


class BindingDecision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"
    MANUAL = "manual"


class ExclusionReason(StrEnum):
    SOURCE_PENDING = "source_pending"
    SOURCE_UNAVAILABLE = "source_unavailable"
    IDENTITY_CONFLICT = "identity_conflict"
    NOT_RELEVANT = "not_relevant"
    NOT_PRODUCTION_BASELINE = "not_production_baseline"
    CANCELLED = "cancelled"


E = TypeVar("E")


def _enum(kind: type[E], value: object, message: str) -> E:
    try:
        return kind(value)  # type: ignore[call-arg, return-value]
    except (TypeError, ValueError) as exc:
        raise ContractError(message) from exc


def _exact(raw: object, names: set[str], message: str) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != names:
        raise ContractError(message)
    return raw


def _hash(value: object, name: str) -> str:
    need(type(value) is str and HASH_RE.fullmatch(value) is not None, f"{name} is invalid")
    return value


def _identity(value: object, name: str) -> str:
    need(type(value) is str and IDENTITY_RE.fullmatch(value) is not None, f"{name} is invalid")
    need(value != QUARANTINED_SOURCE_ID, "source identity is quarantined")
    return value


def _text(value: object, name: str) -> str:
    need(type(value) is str and bool(value.strip()), f"{name} is invalid")
    return value


def _timestamp(value: object, name: str) -> str:
    need(type(value) is str, f"{name} is invalid")
    aware_timestamp(value, name)
    return value


def _tuple(value: object, name: str, *, nonempty: bool = False) -> tuple:
    need(type(value) in (list, tuple), f"{name} is invalid")
    result = tuple(value)
    need(not nonempty or bool(result), f"{name} is invalid")
    return result


def _mapping(value: object) -> dict:
    def convert(item: object) -> object:
        if hasattr(item, "value"):
            return item.value
        if hasattr(item, "__dataclass_fields__"):
            return {field.name: convert(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, (tuple, list)):
            return [convert(child) for child in item]
        if isinstance(item, dict):
            return {key: convert(child) for key, child in item.items()}
        return item

    result = convert(value)
    assert isinstance(result, dict)
    return result


@dataclass(frozen=True)
class CandidateApproval:
    candidate_id: str
    snapshot_source_id: str
    expected_locator_ref: str
    display_name: str
    approved_operations: tuple[SourceOperation, ...]

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        _identity(self.snapshot_source_id, "snapshot_source_id")
        need(type(self.expected_locator_ref) is str and self.expected_locator_ref.startswith("snapshot:") and HASH_RE.fullmatch(self.expected_locator_ref[9:]) is not None, "expected_locator_ref is invalid")
        _text(self.display_name, "display_name")
        need(type(self.approved_operations) is tuple and bool(self.approved_operations), "approved_operations are invalid")
        need(all(type(item) is SourceOperation for item in self.approved_operations) and len(set(self.approved_operations)) == len(self.approved_operations), "approved_operations are invalid")

    @classmethod
    def from_mapping(cls, raw: object) -> "CandidateApproval":
        value = _exact(raw, {"candidate_id", "snapshot_source_id", "expected_locator_ref", "display_name", "approved_operations"}, "candidate approval fields are invalid")
        result = cls(value["candidate_id"], value["snapshot_source_id"], value["expected_locator_ref"], value["display_name"], tuple(_enum(SourceOperation, item, "source operation is invalid") for item in _tuple(value["approved_operations"], "approved_operations", nonempty=True)))
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    status: CandidateStatus
    approval_hash: str
    observation_revision: str | None
    proposal_ids: tuple[str, ...]
    formal_source_id: str | None
    exclusion_reason: ExclusionReason | None

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        need(type(self.status) is CandidateStatus, "candidate status is invalid")
        _hash(self.approval_hash, "approval_hash")
        if self.observation_revision is not None:
            _hash(self.observation_revision, "observation_revision")
        need(type(self.proposal_ids) is tuple and tuple(sorted(self.proposal_ids)) == self.proposal_ids and len(set(self.proposal_ids)) == len(self.proposal_ids), "proposal_ids are invalid")
        for item in self.proposal_ids:
            runtime_id(item, "proposal_id")
        if self.formal_source_id is not None:
            _identity(self.formal_source_id, "formal_source_id")
        need(self.exclusion_reason is None or type(self.exclusion_reason) is ExclusionReason, "exclusion_reason is invalid")

    @classmethod
    def from_mapping(cls, raw: object) -> "CandidateRecord":
        names = {item.name for item in fields(cls)}
        value = _exact(raw, names, "candidate record fields are invalid")
        result = cls(value["candidate_id"], _enum(CandidateStatus, value["status"], "candidate status is invalid"), value["approval_hash"], value["observation_revision"], tuple(sorted(_tuple(value["proposal_ids"], "proposal_ids"))), value["formal_source_id"], None if value["exclusion_reason"] is None else _enum(ExclusionReason, value["exclusion_reason"], "exclusion_reason is invalid"))
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class HeadingObservation:
    heading_path: str
    normalized_path: str
    block_id: str
    evidence_hash: str

    def validate(self) -> None:
        _text(self.heading_path, "heading_path")
        _text(self.normalized_path, "normalized_path")
        runtime_id(self.block_id, "block_id")
        _hash(self.evidence_hash, "evidence_hash")

    @classmethod
    def from_mapping(cls, raw: object) -> "HeadingObservation":
        value = _exact(raw, {item.name for item in fields(cls)}, "heading observation fields are invalid")
        result = cls(**value)
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class SourceObservation:
    candidate_id: str
    snapshot_source_id: str
    document_identity_sha256: str
    title: str
    revision: str
    content_bytes: int
    headings: tuple[HeadingObservation, ...]
    observed_at: str

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        _identity(self.snapshot_source_id, "snapshot_source_id")
        _hash(self.document_identity_sha256, "document_identity_sha256")
        _text(self.title, "title")
        _hash(self.revision, "revision")
        need(type(self.content_bytes) is int and self.content_bytes >= 0, "content_bytes is invalid")
        need(type(self.headings) is tuple and bool(self.headings), "headings are invalid")
        for item in self.headings:
            item.validate()
        need(len({item.normalized_path for item in self.headings}) == len(self.headings), "headings are invalid")
        _timestamp(self.observed_at, "observed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "SourceObservation":
        value = _exact(raw, {item.name for item in fields(cls)}, "source observation fields are invalid")
        result = cls(value["candidate_id"], value["snapshot_source_id"], value["document_identity_sha256"], value["title"], value["revision"], value["content_bytes"], tuple(HeadingObservation.from_mapping(item) for item in _tuple(value["headings"], "headings", nonempty=True)), value["observed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class EvidenceRef:
    normalized_path: str
    block_id: str
    evidence_hash: str

    def validate(self) -> None:
        _text(self.normalized_path, "normalized_path")
        runtime_id(self.block_id, "block_id")
        _hash(self.evidence_hash, "evidence_hash")

    @classmethod
    def from_mapping(cls, raw: object) -> "EvidenceRef":
        value = _exact(raw, {item.name for item in fields(cls)}, "evidence fields are invalid")
        result = cls(**value)
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class BindingProposal:
    proposal_id: str
    candidate_id: str
    suggested_surface_id: str
    suggested_display_path: tuple[str, ...]
    suggested_change_mode: ChangeMode
    impact_summary: str
    based_on_revision: str
    evidence_refs: tuple[EvidenceRef, ...]

    def validate(self) -> None:
        runtime_id(self.proposal_id, "proposal_id")
        runtime_id(self.candidate_id, "candidate_id")
        need(type(self.suggested_surface_id) is str and SURFACE_ID_RE.fullmatch(self.suggested_surface_id) is not None, "surface_id is invalid")
        need(type(self.suggested_display_path) is tuple and bool(self.suggested_display_path) and all(type(item) is str and item.strip() for item in self.suggested_display_path), "display_path is invalid")
        need(type(self.suggested_change_mode) is ChangeMode, "change mode is invalid")
        _text(self.impact_summary, "impact_summary")
        _hash(self.based_on_revision, "based_on_revision")
        need(type(self.evidence_refs) is tuple and bool(self.evidence_refs), "evidence_refs are invalid")
        for item in self.evidence_refs:
            item.validate()

    @classmethod
    def from_mapping(cls, raw: object) -> "BindingProposal":
        value = _exact(raw, {item.name for item in fields(cls)}, "binding proposal fields are invalid")
        result = cls(value["proposal_id"], value["candidate_id"], value["suggested_surface_id"], tuple(value["suggested_display_path"]), _enum(ChangeMode, value["suggested_change_mode"], "change mode is invalid"), value["impact_summary"], value["based_on_revision"], tuple(EvidenceRef.from_mapping(item) for item in value["evidence_refs"]))
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class AnalysisProposal:
    schema_version: int
    onboarding_id: str
    proposals: tuple[BindingProposal, ...]

    def validate(self) -> None:
        need(self.schema_version == 1, "proposal schema_version is invalid")
        runtime_id(self.onboarding_id, "onboarding_id")
        need(type(self.proposals) is tuple and bool(self.proposals), "proposals are invalid")
        for item in self.proposals:
            item.validate()

    @classmethod
    def from_mapping(cls, raw: object) -> "AnalysisProposal":
        value = _exact(raw, {"schema_version", "onboarding_id", "proposals"}, "analysis proposal fields are invalid")
        result = cls(value["schema_version"], value["onboarding_id"], tuple(sorted((BindingProposal.from_mapping(item) for item in value["proposals"]), key=lambda item: item.proposal_id)))
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class LifecycleDecision:
    source_id: str
    display_name: str
    lifecycle_status: LifecycleStatus
    observed_revision: str
    active_revision: str | None
    released_revision: str | None

    def validate(self) -> None:
        _identity(self.source_id, "source_id")
        _text(self.display_name, "display_name")
        need(type(self.lifecycle_status) is LifecycleStatus, "lifecycle_status is invalid")
        _hash(self.observed_revision, "observed_revision")
        if self.active_revision is not None:
            _hash(self.active_revision, "active_revision")
        if self.released_revision is not None:
            _hash(self.released_revision, "released_revision")
        if self.lifecycle_status in (LifecycleStatus.UNCONFIRMED, LifecycleStatus.WITHDRAWN):
            need(self.active_revision is None and self.released_revision is None, "unapproved lifecycle has approved revisions")
        elif self.lifecycle_status is LifecycleStatus.RELEASED:
            need(self.active_revision is not None and self.released_revision is not None, "released revision is missing")
        else:
            need(self.active_revision is not None and self.released_revision is None, "non-released lifecycle revisions are invalid")

    @classmethod
    def from_mapping(cls, raw: object) -> "LifecycleDecision":
        value = _exact(raw, {item.name for item in fields(cls)}, "lifecycle decision fields are invalid")
        result = cls(value["source_id"], value["display_name"], _enum(LifecycleStatus, value["lifecycle_status"], "lifecycle_status is invalid"), value["observed_revision"], value["active_revision"], value["released_revision"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class ConfirmedBinding:
    candidate_id: str
    surface_id: str
    display_path: tuple[str, ...]
    change_mode: ChangeMode
    impact_summary: str
    based_on_revision: str
    evidence_refs: tuple[EvidenceRef, ...]
    decision: BindingDecision
    proposal_id: str | None
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        need(type(self.surface_id) is str and SURFACE_ID_RE.fullmatch(self.surface_id) is not None, "surface_id is invalid")
        need(type(self.display_path) is tuple and bool(self.display_path) and all(type(item) is str and item.strip() for item in self.display_path), "display_path is invalid")
        need(type(self.change_mode) is ChangeMode, "change mode is invalid")
        _text(self.impact_summary, "impact_summary")
        _hash(self.based_on_revision, "based_on_revision")
        need(type(self.decision) is BindingDecision, "binding decision is invalid")
        if self.decision in (BindingDecision.ACCEPT, BindingDecision.CORRECT):
            need(type(self.proposal_id) is str, "proposal_id is required")
            runtime_id(self.proposal_id, "proposal_id")
        elif self.decision is BindingDecision.MANUAL:
            need(self.proposal_id is None, "manual binding cannot reference a proposal")
        else:
            need(False, "rejected binding cannot be confirmed")
        need(type(self.evidence_refs) is tuple and bool(self.evidence_refs), "evidence_refs are invalid")
        for item in self.evidence_refs:
            item.validate()
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "ConfirmedBinding":
        value = _exact(raw, {item.name for item in fields(cls)}, "confirmed binding fields are invalid")
        result = cls(value["candidate_id"], value["surface_id"], tuple(value["display_path"]), _enum(ChangeMode, value["change_mode"], "change mode is invalid"), value["impact_summary"], value["based_on_revision"], tuple(EvidenceRef.from_mapping(item) for item in value["evidence_refs"]), _enum(BindingDecision, value["decision"], "binding decision is invalid"), value["proposal_id"], value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class ConfirmedDependency:
    source_id: str
    depends_on: str
    rationale: str
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        _identity(self.source_id, "source_id")
        _identity(self.depends_on, "depends_on")
        need(self.source_id != self.depends_on, "dependency self-cycle is invalid")
        _text(self.rationale, "rationale")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "ConfirmedDependency":
        value = _exact(raw, {item.name for item in fields(cls)}, "confirmed dependency fields are invalid")
        result = cls(**value)
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class RejectedProposalDecision:
    proposal_id: str
    candidate_id: str
    reason: str
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        runtime_id(self.proposal_id, "proposal_id")
        runtime_id(self.candidate_id, "candidate_id")
        _text(self.reason, "rejection reason")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "RejectedProposalDecision":
        value = _exact(raw, {item.name for item in fields(cls)}, "rejected proposal fields are invalid")
        result = cls(**value)
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class ConfirmedConflict:
    resolution_id: str
    surface_id: str
    source_ids: tuple[str, ...]
    scope: ConflictScope
    decision: ConflictDecision
    winner_source_id: str | None
    excluded_source_ids: tuple[str, ...]
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        runtime_id(self.resolution_id, "resolution_id")
        need(type(self.surface_id) is str and SURFACE_ID_RE.fullmatch(self.surface_id) is not None, "surface_id is invalid")
        need(type(self.source_ids) is tuple and len(self.source_ids) >= 2 and tuple(sorted(self.source_ids)) == self.source_ids and len(set(self.source_ids)) == len(self.source_ids), "conflict source_ids are invalid")
        for item in self.source_ids:
            _identity(item, "source_id")
        need(type(self.scope) is ConflictScope and type(self.decision) is ConflictDecision, "conflict enums are invalid")
        if self.winner_source_id is not None:
            _identity(self.winner_source_id, "winner_source_id")
        need(type(self.excluded_source_ids) is tuple and tuple(sorted(self.excluded_source_ids)) == self.excluded_source_ids, "excluded_source_ids are invalid")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "ConfirmedConflict":
        value = _exact(raw, {item.name for item in fields(cls)}, "confirmed conflict fields are invalid")
        result = cls(value["resolution_id"], value["surface_id"], tuple(sorted(value["source_ids"])), _enum(ConflictScope, value["scope"], "conflict scope is invalid"), _enum(ConflictDecision, value["decision"], "conflict decision is invalid"), value["winner_source_id"], tuple(sorted(value["excluded_source_ids"])), value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class CandidateGovernance:
    candidate_id: str
    document: LifecycleDecision
    bindings: tuple[ConfirmedBinding, ...]
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        self.document.validate()
        need(type(self.bindings) is tuple, "bindings are invalid")
        for item in self.bindings:
            item.validate()
            need(item.candidate_id == self.candidate_id, "binding candidate mismatch")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "CandidateGovernance":
        value = _exact(raw, {item.name for item in fields(cls)}, "candidate governance fields are invalid")
        result = cls(value["candidate_id"], LifecycleDecision.from_mapping(value["document"]), tuple(sorted((ConfirmedBinding.from_mapping(item) for item in value["bindings"]), key=lambda item: item.surface_id)), value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class GovernanceSnapshot:
    schema_version: int
    onboarding_id: str
    expected_registry_view_hash: str
    candidates: tuple[CandidateGovernance, ...]
    dependencies: tuple[ConfirmedDependency, ...]
    conflicts: tuple[ConfirmedConflict, ...]
    rejected_proposals: tuple[RejectedProposalDecision, ...]
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        need(self.schema_version == 2, "governance schema_version is invalid")
        runtime_id(self.onboarding_id, "onboarding_id")
        _hash(self.expected_registry_view_hash, "expected_registry_view_hash")
        need(type(self.candidates) is tuple and bool(self.candidates), "governance candidates are invalid")
        for item in self.candidates:
            item.validate()
        for item in self.dependencies:
            item.validate()
        for item in self.conflicts:
            item.validate()
        need(type(self.rejected_proposals) is tuple, "rejected proposals are invalid")
        for item in self.rejected_proposals:
            item.validate()
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "GovernanceSnapshot":
        value = _exact(raw, {item.name for item in fields(cls)}, "governance snapshot fields are invalid")
        result = cls(value["schema_version"], value["onboarding_id"], value["expected_registry_view_hash"], tuple(sorted((CandidateGovernance.from_mapping(item) for item in value["candidates"]), key=lambda item: item.candidate_id)), tuple(sorted((ConfirmedDependency.from_mapping(item) for item in value["dependencies"]), key=lambda item: (item.source_id, item.depends_on))), tuple(sorted((ConfirmedConflict.from_mapping(item) for item in value["conflicts"]), key=lambda item: item.resolution_id)), tuple(sorted((RejectedProposalDecision.from_mapping(item) for item in value["rejected_proposals"]), key=lambda item: item.proposal_id)), value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class ExcludedCandidate:
    candidate_id: str
    reason: ExclusionReason
    affects_target_production: bool
    hard_prerequisite_of_included: bool
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        runtime_id(self.candidate_id, "candidate_id")
        need(type(self.reason) is ExclusionReason, "exclusion reason is invalid")
        need(type(self.affects_target_production) is bool and type(self.hard_prerequisite_of_included) is bool, "exclusion sufficiency is invalid")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "ExcludedCandidate":
        value = _exact(raw, {item.name for item in fields(cls)}, "excluded candidate fields are invalid")
        result = cls(value["candidate_id"], _enum(ExclusionReason, value["reason"], "exclusion reason is invalid"), value["affects_target_production"], value["hard_prerequisite_of_included"], value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class CommitSelection:
    schema_version: int
    onboarding_id: str
    included_candidate_ids: tuple[str, ...]
    excluded_candidates: tuple[ExcludedCandidate, ...]
    confirmed_by: str
    confirmed_at: str

    def validate(self) -> None:
        need(self.schema_version == 1, "selection schema_version is invalid")
        runtime_id(self.onboarding_id, "onboarding_id")
        need(type(self.included_candidate_ids) is tuple and tuple(sorted(self.included_candidate_ids)) == self.included_candidate_ids and len(set(self.included_candidate_ids)) == len(self.included_candidate_ids), "included candidates are invalid")
        for item in self.included_candidate_ids:
            runtime_id(item, "candidate_id")
        for item in self.excluded_candidates:
            item.validate()
        need(not set(self.included_candidate_ids) & {item.candidate_id for item in self.excluded_candidates}, "selection partition overlaps")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")

    @classmethod
    def from_mapping(cls, raw: object) -> "CommitSelection":
        value = _exact(raw, {item.name for item in fields(cls)}, "commit selection fields are invalid")
        result = cls(value["schema_version"], value["onboarding_id"], tuple(sorted(value["included_candidate_ids"])), tuple(sorted((ExcludedCandidate.from_mapping(item) for item in value["excluded_candidates"]), key=lambda item: item.candidate_id)), value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


@dataclass(frozen=True)
class OnboardingSession:
    schema_version: int
    onboarding_id: str
    target_surface_id: str
    expected_registry_view_hash: str
    status: SessionStatus
    candidates: tuple[CandidateRecord, ...]
    observations: tuple[SourceObservation, ...]
    approvals_hash: str | None
    proposal_hash: str | None
    governance_hash: str | None
    full_dry_run_hash: str | None
    selection_hash: str | None
    selected_dry_run_hash: str | None
    proposed_registry_view_hash: str | None
    receipt_id: str | None
    writer_permissions: tuple[str, ...] = ()

    @classmethod
    def empty(cls, onboarding_id: str, target_surface_id: str, expected_registry_view_hash: str) -> "OnboardingSession":
        result = cls(2, onboarding_id, target_surface_id, expected_registry_view_hash, SessionStatus.DRAFT, (), (), None, None, None, None, None, None, None, None, ())
        result.validate()
        return result

    def validate(self) -> None:
        need(self.schema_version == 2, "session schema_version is invalid")
        runtime_id(self.onboarding_id, "onboarding_id")
        need(type(self.target_surface_id) is str and SURFACE_ID_RE.fullmatch(self.target_surface_id) is not None, "target_surface_id is invalid")
        _hash(self.expected_registry_view_hash, "expected_registry_view_hash")
        need(type(self.status) is SessionStatus, "session status is invalid")
        for item in self.candidates:
            item.validate()
        for item in self.observations:
            item.validate()
        for name in ("approvals_hash", "proposal_hash", "governance_hash", "full_dry_run_hash", "selection_hash", "selected_dry_run_hash", "proposed_registry_view_hash"):
            value = getattr(self, name)
            if value is not None:
                _hash(value, name)
        if self.receipt_id is not None:
            need(self.receipt_id.startswith("onboarding-") and HASH_RE.fullmatch(self.receipt_id[11:]) is not None, "receipt_id is invalid")
        need(self.writer_permissions == (), "writer permissions are forbidden")

    @classmethod
    def from_mapping(cls, raw: object) -> "OnboardingSession":
        names = {item.name for item in fields(cls)} - {"writer_permissions"}
        value = _exact(raw, names, "onboarding session fields are invalid")
        result = cls(value["schema_version"], value["onboarding_id"], value["target_surface_id"], value["expected_registry_view_hash"], _enum(SessionStatus, value["status"], "session status is invalid"), tuple(sorted((CandidateRecord.from_mapping(item) for item in value["candidates"]), key=lambda item: item.candidate_id)), tuple(sorted((SourceObservation.from_mapping(item) for item in value["observations"]), key=lambda item: item.candidate_id)), value["approvals_hash"], value["proposal_hash"], value["governance_hash"], value["full_dry_run_hash"], value["selection_hash"], value["selected_dry_run_hash"], value["proposed_registry_view_hash"], value["receipt_id"], ())
        result.validate()
        return result

    def to_mapping(self) -> dict:
        result = _mapping(self)
        result.pop("writer_permissions")
        return result


@dataclass(frozen=True)
class OnboardingReceipt:
    schema_version: int
    receipt_id: str
    onboarding_id: str
    registry_view_hash_before: str
    registry_view_hash_after: str
    committed_source_ids: tuple[str, ...]
    confirmed_binding_evidence: tuple[dict, ...]
    confirmed_dependencies: tuple[dict, ...]
    confirmed_conflicts: tuple[dict, ...]
    excluded_candidates: tuple[dict, ...]
    production_baseline_dry_run_hash: str
    confirmed_by: str
    confirmed_at: str

    def payload_without_id(self) -> dict:
        result = self.to_mapping()
        result.pop("receipt_id")
        return result

    def validate(self) -> None:
        need(self.schema_version == 2, "receipt schema_version is invalid")
        runtime_id(self.onboarding_id, "onboarding_id")
        _hash(self.registry_view_hash_before, "registry_view_hash_before")
        _hash(self.registry_view_hash_after, "registry_view_hash_after")
        need(
            tuple(sorted(self.committed_source_ids)) == self.committed_source_ids
            and len(set(self.committed_source_ids)) == len(self.committed_source_ids),
            "committed source_ids are invalid",
        )
        for source_id in self.committed_source_ids:
            _identity(source_id, "committed_source_id")
        dependency_keys = tuple(
            (item["source_id"], item["depends_on"])
            for item in self.confirmed_dependencies
            if type(item) is dict and set(item) == {"source_id", "depends_on"}
        )
        need(
            len(dependency_keys) == len(self.confirmed_dependencies)
            and tuple(sorted(dependency_keys)) == dependency_keys
            and len(set(dependency_keys)) == len(dependency_keys),
            "receipt dependencies are invalid",
        )
        for source_id, depends_on in dependency_keys:
            _identity(source_id, "dependency source_id")
            _identity(depends_on, "dependency depends_on")
        conflict_keys = tuple(
            item["resolution_id"]
            for item in self.confirmed_conflicts
            if type(item) is dict and set(item) == {"resolution_id", "source_ids", "decision"}
        )
        need(
            len(conflict_keys) == len(self.confirmed_conflicts)
            and tuple(sorted(conflict_keys)) == conflict_keys
            and len(set(conflict_keys)) == len(conflict_keys),
            "receipt conflicts are invalid",
        )
        for item in self.confirmed_conflicts:
            runtime_id(item["resolution_id"], "resolution_id")
            source_ids = item["source_ids"]
            need(
                type(source_ids) in (list, tuple)
                and len(source_ids) >= 2
                and tuple(sorted(source_ids)) == tuple(source_ids)
                and len(set(source_ids)) == len(source_ids),
                "receipt conflicts are invalid",
            )
            for source_id in source_ids:
                _identity(source_id, "conflict source_id")
            _enum(ConflictDecision, item["decision"], "receipt conflicts are invalid")
        _hash(self.production_baseline_dry_run_hash, "production_baseline_dry_run_hash")
        _text(self.confirmed_by, "confirmed_by")
        _timestamp(self.confirmed_at, "confirmed_at")
        expected = "onboarding-" + sha256_bytes(canonical_json(self.payload_without_id()))
        need(self.receipt_id == expected, "receipt_id is invalid")
        validate_public_payload(self.to_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "OnboardingReceipt":
        value = _exact(raw, {item.name for item in fields(cls)}, "onboarding receipt fields are invalid")
        result = cls(value["schema_version"], value["receipt_id"], value["onboarding_id"], value["registry_view_hash_before"], value["registry_view_hash_after"], tuple(value["committed_source_ids"]), tuple(value["confirmed_binding_evidence"]), tuple(value["confirmed_dependencies"]), tuple(value["confirmed_conflicts"]), tuple(value["excluded_candidates"]), value["production_baseline_dry_run_hash"], value["confirmed_by"], value["confirmed_at"])
        result.validate()
        return result

    def to_mapping(self) -> dict:
        return _mapping(self)


_FORBIDDEN_FIELDS = {"document", "body", "content", "excerpt", "locator", "url", "absolute_path", "token", "access_token"}
_FORBIDDEN_STRINGS = ("document.md", "http://", "https://", "/Users/", "tenant_access_token", QUARANTINED_SOURCE_ID)


def validate_public_payload(value: object, *, allow_opaque_locator_ref: bool = False) -> None:
    def visit(item: object) -> None:
        if type(item) is dict:
            for key, child in item.items():
                need(type(key) is str, "forbidden material")
                lowered = key.lower()
                need(lowered not in _FORBIDDEN_FIELDS, "forbidden material")
                if lowered == "locator_ref":
                    allowed = allow_opaque_locator_ref and type(child) is str and (
                        (child.startswith("fixture:") and IDENTITY_RE.fullmatch(child[8:]) is not None)
                        or (child.startswith("onboarding:") and child[11:] and runtime_id(child[11:], "candidate_id"))
                    )
                    need(bool(allowed), "forbidden material")
                visit(child)
        elif type(item) in (list, tuple):
            for child in item:
                visit(child)
        elif type(item) is str:
            need(not any(marker in item for marker in _FORBIDDEN_STRINGS), "forbidden material")

    visit(value)
