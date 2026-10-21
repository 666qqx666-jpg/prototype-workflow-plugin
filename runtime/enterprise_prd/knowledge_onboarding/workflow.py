from __future__ import annotations

from dataclasses import replace

from enterprise_prd.baseline_registry.snapshot import RegistrySnapshot
from enterprise_prd.baseline_registry.store import RegistryStore
from enterprise_prd.baseline_registry.contracts import (
    BaselineMode,
    BindingState,
    DependencyState,
    LifecycleStatus,
    ObservationState,
)
from enterprise_prd.baseline_registry.resolver import BaselineRequest, BaselineResolver
from enterprise_prd.baseline_registry.errors import ConcurrentRegistryConflict
from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import ContractError

from .contracts import (
    AnalysisProposal,
    CandidateApproval,
    CandidateRecord,
    CandidateStatus,
    GovernanceSnapshot,
    CommitSelection,
    OnboardingSession,
    SessionStatus,
    SourceObservation,
)
from .artifacts import ArtifactStore
from .errors import ConcurrentOnboardingConflict, OnboardingError
from .genesis import GENESIS_BASE_VIEW_HASH, GenesisClaim, GenesisClaimStore, is_genesis_session, registry_presence
from .governance import project_genesis_registry, project_registry
from .proposal import ProposalValidator
from .source import ApprovedSnapshotSource
from .workspace import OnboardingWorkspace
from .receipt import ReceiptStore, build_receipt


def _production_dry_run(snapshot: RegistrySnapshot, surface_id: str, created_at: str) -> dict:
    selected = {
        binding.source_id
        for binding in snapshot.bindings
        if binding.surface_id == surface_id
        and binding.state is BindingState.CONFIRMED
        and snapshot.document(binding.source_id).lifecycle_status is LifecycleStatus.RELEASED
    }
    changed = True
    while changed:
        changed = False
        for edge in snapshot.dependencies:
            if edge.state is DependencyState.CONFIRMED and edge.source_id in selected and edge.depends_on not in selected:
                selected.add(edge.depends_on)
                changed = True
    if any(snapshot.document(source_id).released_revision is None for source_id in selected):
        raise OnboardingError("baseline_dry_run_failed")
    request = BaselineRequest(
        task_label="商管后台新增跟进记录：onboarding production baseline",
        baseline_mode=BaselineMode.PRODUCTION,
        surface_ids=(surface_id,),
        explicit_target_source_ids=(),
        confirmed_auto_dependency_source_ids=(),
        conflict_resolution_ids=(),
        expected_registry_view_hash=snapshot.view_hash,
        expected_source_revisions=tuple(sorted((source_id, snapshot.document(source_id).released_revision) for source_id in selected)),
        created_by="product-owner",
        created_at=created_at,
    )
    try:
        resolution = BaselineResolver(snapshot).resolve(request)
    except Exception as exc:
        raise OnboardingError("baseline_dry_run_failed") from exc
    return {
        "schema_version": 1,
        "registry_view_hash": snapshot.view_hash,
        "surface_id": surface_id,
        "document_revisions": [[item.source_id, item.revision] for item in resolution.documents],
        "resolution_hash": sha256_bytes(resolution.rendered.encode("utf-8")),
    }


def _require_genesis_released_target(
    snapshot: RegistrySnapshot,
    surface_id: str,
) -> None:
    for binding in snapshot.bindings:
        if binding.surface_id != surface_id or binding.state is not BindingState.CONFIRMED:
            continue
        document = snapshot.document(binding.source_id)
        if (
            document.lifecycle_status is LifecycleStatus.RELEASED
            and document.released_revision is not None
            and snapshot.revision(
                document.source_id,
                document.released_revision,
            ).observation_state
            is ObservationState.AVAILABLE
        ):
            return
    raise OnboardingError("baseline_dry_run_failed")


def _dry_run_for_session(
    session: OnboardingSession,
    snapshot: RegistrySnapshot,
    created_at: str,
) -> dict:
    if is_genesis_session(session):
        _require_genesis_released_target(snapshot, session.target_surface_id)
    return _production_dry_run(snapshot, session.target_surface_id, created_at)


class ReceiptRecoveryService:
    def __init__(self, workspace: OnboardingWorkspace, registry: RegistryStore) -> None:
        self.workspace = workspace
        self.registry = registry
        self.artifacts = ArtifactStore(workspace)

    def _recover_genesis_commit(self, session: OnboardingSession, state_hash: str) -> OnboardingSession:
        claim = GenesisClaimStore(self.registry).load_optional()
        expected_claim = GenesisClaim(session.onboarding_id, session.proposed_registry_view_hash, session.governance_hash, session.selection_hash)
        try:
            expected_claim.validate()
        except ContractError as exc:
            raise OnboardingError("receipt_recovery_conflict") from exc
        if claim is None:
            raise OnboardingError("receipt_recovery_conflict")
        if claim != expected_claim:
            raise OnboardingError("genesis_claim_conflict")
        try:
            current = self.registry.load()
        except Exception as exc:
            raise OnboardingError("receipt_recovery_conflict") from exc
        if current.view_hash != session.proposed_registry_view_hash:
            raise OnboardingError("receipt_recovery_conflict")
        stored_dry_run = self.artifacts.load(session.onboarding_id, "selected-dry-run", session.selected_dry_run_hash)
        rerun = _dry_run_for_session(session, current, stored_dry_run["created_at"])
        if rerun != {key: value for key, value in stored_dry_run.items() if key != "created_at"}:
            raise OnboardingError("receipt_recovery_conflict")
        committed = replace(session, status=SessionStatus.REGISTRY_COMMITTED)
        try:
            self.workspace.commit(session.onboarding_id, state_hash, committed)
        except ConcurrentOnboardingConflict:
            committed, _ = self.workspace.load(session.onboarding_id)
            if committed.status is not SessionStatus.REGISTRY_COMMITTED or committed.proposed_registry_view_hash != current.view_hash:
                raise OnboardingError("receipt_recovery_conflict")
        return committed

    def recover(self, onboarding_id: str) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        if session.status is SessionStatus.DRY_RUN_PASSED and is_genesis_session(session):
            self._recover_genesis_commit(session, state_hash)
            session, state_hash = self.workspace.load(onboarding_id)
        if session.status is SessionStatus.COMPLETED:
            receipt = ReceiptStore(self.workspace).load(onboarding_id)
            if receipt.receipt_id != session.receipt_id:
                raise OnboardingError("receipt_recovery_conflict")
            return session
        if session.status is not SessionStatus.REGISTRY_COMMITTED:
            raise OnboardingError("commit_recovery_required")
        current = self.registry.load()
        if current.view_hash == session.expected_registry_view_hash:
            raise OnboardingError("commit_recovery_required")
        if current.view_hash != session.proposed_registry_view_hash:
            raise OnboardingError("receipt_recovery_conflict")
        governance = GovernanceSnapshot.from_mapping(
            self.artifacts.load(onboarding_id, "governance", session.governance_hash)
        )
        selection = CommitSelection.from_mapping(
            self.artifacts.load(onboarding_id, "selection", session.selection_hash)
        )
        if session.selected_dry_run_hash is None:
            raise OnboardingError("receipt_recovery_conflict")
        stored_dry_run = self.artifacts.load(
            onboarding_id,
            "selected-dry-run",
            session.selected_dry_run_hash,
        )
        rerun = _dry_run_for_session(session, current, stored_dry_run["created_at"])
        if rerun != {
            key: value for key, value in stored_dry_run.items() if key != "created_at"
        }:
            raise OnboardingError("receipt_recovery_conflict")
        receipt = build_receipt(
            governance,
            selection,
            session.expected_registry_view_hash,
            current,
            session.selected_dry_run_hash,
        )
        ReceiptStore(self.workspace).save(onboarding_id, receipt)
        completed = replace(
            session,
            status=SessionStatus.COMPLETED,
            candidates=tuple(
                replace(item, status=CandidateStatus.COMMITTED)
                if item.status is CandidateStatus.READY_TO_COMMIT
                else item
                for item in session.candidates
            ),
            receipt_id=receipt.receipt_id,
        )
        self.workspace.commit(onboarding_id, state_hash, completed)
        return completed


class OnboardingWorkflow:
    def __init__(self, workspace: OnboardingWorkspace, registry: RegistryStore, source: ApprovedSnapshotSource) -> None:
        self.workspace = workspace
        self.registry = registry
        self.source = source
        self.artifacts = ArtifactStore(workspace)

    def _approvals(self, session: OnboardingSession) -> tuple[CandidateApproval, ...]:
        value = self.artifacts.load(session.onboarding_id, "approvals", session.approvals_hash)
        if set(value) != {"schema_version", "approvals"} or value["schema_version"] != 1 or type(value["approvals"]) is not list:
            raise ContractError("approvals file is invalid")
        return tuple(CandidateApproval.from_mapping(item) for item in value["approvals"])

    def _create_session(self, onboarding_id: str, target_surface_id: str, expected_registry_view_hash: str, approvals: tuple[CandidateApproval, ...]) -> OnboardingSession:
        if type(approvals) is not tuple or not approvals:
            raise OnboardingError("source_not_approved")
        for item in approvals:
            item.validate()
        session = OnboardingSession.empty(onboarding_id, target_surface_id, expected_registry_view_hash)
        candidates = tuple(
            CandidateRecord(item.candidate_id, CandidateStatus.CANDIDATE_CREATED, sha256_bytes(canonical_json(item.to_mapping())), None, (), None, None)
            for item in sorted(approvals, key=lambda item: item.candidate_id)
        )
        session = replace(session, candidates=candidates)
        self.workspace.create(session)
        approval_payload = {"schema_version": 1, "approvals": [item.to_mapping() for item in sorted(approvals, key=lambda item: item.candidate_id)]}
        approval_hash = self.artifacts.save(onboarding_id, "approvals", approval_payload)
        current, state_hash = self.workspace.load(onboarding_id)
        session = replace(current, approvals_hash=approval_hash)
        self.workspace.commit(onboarding_id, state_hash, session)
        return session

    def create(self, onboarding_id: str, target_surface_id: str, expected_registry_view_hash: str, approvals: tuple[CandidateApproval, ...]) -> OnboardingSession:
        if expected_registry_view_hash == GENESIS_BASE_VIEW_HASH:
            raise OnboardingError("registry_view_changed")
        if self.registry.load().view_hash != expected_registry_view_hash:
            raise OnboardingError("registry_view_changed")
        return self._create_session(onboarding_id, target_surface_id, expected_registry_view_hash, approvals)

    def create_genesis(self, onboarding_id: str, target_surface_id: str, approvals: tuple[CandidateApproval, ...]) -> OnboardingSession:
        state, _ = registry_presence(self.registry)
        if state != "absent":
            raise OnboardingError("registry_already_exists")
        return self._create_session(onboarding_id, target_surface_id, GENESIS_BASE_VIEW_HASH, approvals)

    def _ensure_genesis_precondition(self, session: OnboardingSession) -> None:
        if not is_genesis_session(session):
            return
        state, _ = registry_presence(self.registry)
        if state != "absent":
            raise OnboardingError("genesis_precondition_changed")

    def _project(self, session: OnboardingSession, included_candidate_ids: tuple[str, ...] | None = None, governance: GovernanceSnapshot | None = None) -> RegistrySnapshot:
        governance = self._governance(session) if governance is None else governance
        proposal = self._proposal(session)
        if is_genesis_session(session):
            return project_genesis_registry(governance, session.observations, proposal, included_candidate_ids)
        return project_registry(self.registry.load(), governance, session.observations, proposal, included_candidate_ids)

    def observe_sources(self, onboarding_id: str, observed_at: str) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        if session.status is not SessionStatus.DRAFT:
            raise OnboardingError("governance_incomplete")
        approvals = self._approvals(session)
        observations = tuple(self.source.observe(item, observed_at) for item in approvals)
        by_id = {item.candidate_id: item for item in observations}
        candidates = tuple(replace(item, status=CandidateStatus.REVISION_LOCKED, observation_revision=by_id[item.candidate_id].revision) for item in session.candidates)
        updated = replace(session, status=SessionStatus.SOURCES_CONFIRMED, candidates=candidates, observations=tuple(sorted(observations, key=lambda item: item.candidate_id)))
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def import_proposals(self, onboarding_id: str, bundle: AnalysisProposal) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        approvals = self._approvals(session)
        validated = ProposalValidator().validate(bundle, session, approvals, session.observations)
        proposal_hash = self.artifacts.save(onboarding_id, "proposal", validated.to_mapping())
        proposal_ids: dict[str, list[str]] = {}
        for item in validated.proposals:
            proposal_ids.setdefault(item.candidate_id, []).append(item.proposal_id)
        candidates = tuple(replace(item, status=CandidateStatus.ANALYZED, proposal_ids=tuple(sorted(proposal_ids.get(item.candidate_id, ())))) for item in session.candidates)
        updated = replace(session, status=SessionStatus.ANALYSIS_READY, candidates=candidates, proposal_hash=proposal_hash)
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def _proposal(self, session: OnboardingSession) -> AnalysisProposal:
        return AnalysisProposal.from_mapping(self.artifacts.load(session.onboarding_id, "proposal", session.proposal_hash))

    def _governance(self, session: OnboardingSession) -> GovernanceSnapshot:
        return GovernanceSnapshot.from_mapping(self.artifacts.load(session.onboarding_id, "governance", session.governance_hash))

    def confirm_governance(self, onboarding_id: str, governance: GovernanceSnapshot) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        if session.status is not SessionStatus.ANALYSIS_READY or governance.onboarding_id != onboarding_id:
            raise OnboardingError("governance_incomplete")
        proposal = self._proposal(session)
        projected = self._project(session, governance=governance)
        del projected
        governance_hash = self.artifacts.save(onboarding_id, "governance", governance.to_mapping())
        stable = {item.candidate_id: item.document.source_id for item in governance.candidates}
        candidates = tuple(replace(item, status=CandidateStatus.GOVERNANCE_CONFIRMED, formal_source_id=stable.get(item.candidate_id)) for item in session.candidates)
        updated = replace(session, status=SessionStatus.GOVERNANCE_CONFIRMED, candidates=candidates, governance_hash=governance_hash)
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def project_governance(self, onboarding_id: str) -> RegistrySnapshot:
        session, _ = self.workspace.load(onboarding_id)
        if session.status not in {SessionStatus.GOVERNANCE_CONFIRMED, SessionStatus.DRY_RUN_PASSED, SessionStatus.REGISTRY_COMMITTED, SessionStatus.COMPLETED}:
            raise OnboardingError("governance_incomplete")
        return self._project(session)

    def dry_run_full(self, onboarding_id: str, created_at: str) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        if session.status is not SessionStatus.GOVERNANCE_CONFIRMED:
            raise OnboardingError("governance_incomplete")
        snapshot = self._project(session)
        dry_run = _dry_run_for_session(session, snapshot, created_at)
        dry_run_hash = self.artifacts.save(onboarding_id, "full-dry-run", {**dry_run, "created_at": created_at})
        updated = replace(session, full_dry_run_hash=dry_run_hash)
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def select_submission(self, onboarding_id: str, selection: CommitSelection) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        if session.status is not SessionStatus.GOVERNANCE_CONFIRMED or session.full_dry_run_hash is None:
            raise OnboardingError("baseline_dry_run_failed")
        selection.validate()
        if selection.onboarding_id != onboarding_id:
            raise OnboardingError("governance_incomplete")
        known = {item.candidate_id for item in session.candidates}
        selected = set(selection.included_candidate_ids)
        excluded = {item.candidate_id for item in selection.excluded_candidates}
        if selected | excluded != known or selected & excluded:
            raise OnboardingError("governance_incomplete")
        by_exclusion = {item.candidate_id: item for item in selection.excluded_candidates}
        candidates = tuple(
            replace(
                item,
                status=CandidateStatus.READY_TO_COMMIT if item.candidate_id in selected else item.status,
                exclusion_reason=None if item.candidate_id in selected else by_exclusion[item.candidate_id].reason,
            )
            for item in session.candidates
        )
        selection_hash = self.artifacts.save(onboarding_id, "selection", selection.to_mapping())
        updated = replace(session, candidates=candidates, selection_hash=selection_hash)
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def _selection(self, session: OnboardingSession) -> CommitSelection:
        return CommitSelection.from_mapping(self.artifacts.load(session.onboarding_id, "selection", session.selection_hash))

    def dry_run_selected(self, onboarding_id: str, created_at: str) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        self._ensure_genesis_precondition(session)
        if session.status is not SessionStatus.GOVERNANCE_CONFIRMED or session.selection_hash is None:
            raise OnboardingError("baseline_dry_run_failed")
        selection = self._selection(session)
        if any(item.affects_target_production or item.hard_prerequisite_of_included for item in selection.excluded_candidates):
            raise OnboardingError("baseline_dry_run_failed")
        snapshot = self._project(session, selection.included_candidate_ids)
        dry_run = _dry_run_for_session(session, snapshot, created_at)
        proposed_hash = self.artifacts.save(onboarding_id, "proposed-registry", snapshot.to_mapping())
        if proposed_hash != snapshot.view_hash:
            raise OnboardingError("baseline_dry_run_failed")
        selected_hash = self.artifacts.save(onboarding_id, "selected-dry-run", {**dry_run, "created_at": created_at})
        updated = replace(session, status=SessionStatus.DRY_RUN_PASSED, selected_dry_run_hash=selected_hash, proposed_registry_view_hash=snapshot.view_hash)
        self.workspace.commit(onboarding_id, state_hash, updated)
        return updated

    def _proposed(self, session: OnboardingSession) -> RegistrySnapshot:
        return RegistrySnapshot.from_mapping(self.artifacts.load(session.onboarding_id, "proposed-registry", session.proposed_registry_view_hash))

    def _selected_dry_run(self, session: OnboardingSession) -> dict:
        return self.artifacts.load(session.onboarding_id, "selected-dry-run", session.selected_dry_run_hash)

    @staticmethod
    def _dry_run_semantic(value: dict) -> dict:
        return {key: item for key, item in value.items() if key != "created_at"}

    def _genesis_claim(self, session: OnboardingSession) -> GenesisClaim:
        if session.governance_hash is None or session.selection_hash is None or session.proposed_registry_view_hash is None:
            raise OnboardingError("baseline_dry_run_failed")
        return GenesisClaim(session.onboarding_id, session.proposed_registry_view_hash, session.governance_hash, session.selection_hash)

    def _commit_append(self, session: OnboardingSession, state_hash: str, stop_after_registry: bool) -> OnboardingSession:
        governance = self._governance(session)
        selection = self._selection(session)
        proposed = self._proposed(session)
        stored_dry_run = self._selected_dry_run(session)
        before = self.registry.load()
        if before.view_hash != session.expected_registry_view_hash:
            raise OnboardingError("registry_view_changed")
        rerun = _dry_run_for_session(session, proposed, stored_dry_run["created_at"])
        if rerun != self._dry_run_semantic(stored_dry_run):
            raise OnboardingError("baseline_dry_run_failed")
        try:
            self.registry.commit(before.view_hash, proposed)
        except ConcurrentRegistryConflict as exc:
            raise OnboardingError("registry_view_changed") from exc
        committed = replace(session, status=SessionStatus.REGISTRY_COMMITTED, proposed_registry_view_hash=proposed.view_hash)
        try:
            self.workspace.commit(session.onboarding_id, state_hash, committed)
        except Exception:
            current, _ = self.workspace.load(session.onboarding_id)
            if current.proposed_registry_view_hash != proposed.view_hash:
                raise OnboardingError("commit_recovery_required")
            committed = current
        if stop_after_registry:
            return committed
        del governance, selection
        return self.recover_receipt(session.onboarding_id)

    def _commit_genesis(self, session: OnboardingSession, state_hash: str, *, stop_after_claim: bool, stop_after_registry_before_session: bool, stop_after_registry: bool) -> OnboardingSession:
        governance = self._governance(session);selection = self._selection(session);proposed = self._proposed(session);stored_dry_run = self._selected_dry_run(session)
        rerun = _dry_run_for_session(session, proposed, stored_dry_run["created_at"])
        if rerun != self._dry_run_semantic(stored_dry_run):raise OnboardingError("baseline_dry_run_failed")
        claim_store = GenesisClaimStore(self.registry);claim_store.install(self._genesis_claim(session))
        if stop_after_claim:return session
        try:self.registry.create_exclusive(proposed)
        except ConcurrentRegistryConflict as exc:
            current_claim = claim_store.load_optional()
            if current_claim != self._genesis_claim(session):raise OnboardingError("genesis_claim_conflict") from exc
            raise OnboardingError("commit_recovery_required") from exc
        if stop_after_registry_before_session:return session
        committed = replace(session, status=SessionStatus.REGISTRY_COMMITTED, proposed_registry_view_hash=proposed.view_hash)
        try:self.workspace.commit(session.onboarding_id, state_hash, committed)
        except Exception as exc:raise OnboardingError("commit_recovery_required") from exc
        if stop_after_registry:return committed
        del governance, selection
        return self.recover_receipt(session.onboarding_id)

    def commit(self, onboarding_id: str, *, stop_after_registry: bool = False, stop_after_claim: bool = False, stop_after_registry_before_session: bool = False) -> OnboardingSession:
        session, state_hash = self.workspace.load(onboarding_id)
        if session.status is not SessionStatus.DRY_RUN_PASSED:raise OnboardingError("baseline_dry_run_failed")
        if is_genesis_session(session):
            return self._commit_genesis(session, state_hash, stop_after_claim=stop_after_claim, stop_after_registry_before_session=stop_after_registry_before_session, stop_after_registry=stop_after_registry)
        if stop_after_claim or stop_after_registry_before_session:raise ContractError("Genesis fault injection is invalid")
        return self._commit_append(session, state_hash, stop_after_registry)

    def recover_receipt(self, onboarding_id: str) -> OnboardingSession:
        return ReceiptRecoveryService(self.workspace, self.registry).recover(onboarding_id)
