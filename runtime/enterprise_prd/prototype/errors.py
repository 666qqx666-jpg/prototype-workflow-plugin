class PrototypeError(RuntimeError):
    code = "prototype_error"


class ContractError(PrototypeError):
    code = "contract_invalid"


class IntegrityError(PrototypeError):
    code = "integrity_error"


class PathBoundaryError(PrototypeError):
    code = "path_boundary_error"


class GateBlockedError(PrototypeError):
    code = "gate_blocked"


class RestartRequiredError(PrototypeError):
    code = "restart_required"
