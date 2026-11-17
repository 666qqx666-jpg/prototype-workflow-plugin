class PrototypeError(RuntimeError):
    code = "prototype_error"


class ContractError(PrototypeError):
    code = "contract_invalid"


class IntegrityError(PrototypeError):
    code = "integrity_error"


class PathBoundaryError(PrototypeError):
    code = "path_boundary_error"


VALIDATOR_DIAGNOSTICS = frozenset({
    "E_RUNTIME_STATE_RESTORE", "E_RUNTIME_STATE_RESTORE_REPEAT",
    "E_RUNTIME_CONNECTOR_NOT_VISIBLE", "E_ANCHOR_NOT_VISIBLE",
    "E_ANCHOR_MISSING", "E_SNAPSHOT_SELECTOR_AMBIGUOUS",
    "E_SCHEMA_VALIDATION", "E_PROTOTYPE_HASH",
})


class GateBlockedError(PrototypeError):
    code = "gate_blocked"

    @classmethod
    def validator_failure(cls, output):
        # Only a fixed diagnostic identifier crosses the native boundary.
        # Never expose paths, card text, stack traces or raw service output.
        import re
        error = cls("v4 validator failed")
        tokens = re.findall(r"\bE_[A-Z0-9_]+\b", output)
        error.validator_error = next((item for item in tokens if item in VALIDATOR_DIAGNOSTICS), None)
        return error


class RestartRequiredError(PrototypeError):
    code = "restart_required"
