class EnterprisePrdError(RuntimeError):
    code = "enterprise_prd_error"


class ContractError(EnterprisePrdError):
    code = "contract_error"


class IntegrityError(EnterprisePrdError):
    code = "integrity_error"


class RegistryUnavailable(EnterprisePrdError):
    code = "registry_unavailable"


class AccessDenied(EnterprisePrdError):
    code = "access_denied"


class RevisionConflict(EnterprisePrdError):
    code = "revision_conflict"
