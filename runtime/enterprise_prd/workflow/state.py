from enterprise_prd.contracts import RunStatus
from enterprise_prd.errors import ContractError


ALLOWED = {
    RunStatus.READY: {RunStatus.DRAFTING, RunStatus.DEGRADED, RunStatus.BLOCKED},
    RunStatus.DEGRADED: {RunStatus.DRAFTING, RunStatus.BLOCKED},
    RunStatus.BLOCKED: {RunStatus.READY, RunStatus.DEGRADED},
    RunStatus.DRAFTING: {RunStatus.REVIEWING, RunStatus.BLOCKED, RunStatus.CONFLICT},
    RunStatus.REVIEWING: {RunStatus.REVISION_REQUIRED, RunStatus.PASSED, RunStatus.BLOCKED},
    RunStatus.REVISION_REQUIRED: {RunStatus.DRAFTING, RunStatus.BLOCKED},
    RunStatus.CONFLICT: {RunStatus.DRAFTING, RunStatus.BLOCKED},
    RunStatus.PASSED: set(),
}


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    if target not in ALLOWED[current]:
        raise ContractError(f"invalid workflow transition: {current.value}->{target.value}")
    return target
