from enterprise_prd.errors import EnterprisePrdError


class SourceUnavailable(EnterprisePrdError):
    code = "source_unavailable"


class ConcurrentStateConflict(EnterprisePrdError):
    code = "concurrent_state_conflict"
