from enterprise_prd.errors import EnterprisePrdError

class BaselineResolutionError(EnterprisePrdError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

class ConcurrentRegistryConflict(EnterprisePrdError):
    code = "concurrent_registry_conflict"

class ImmutableManifestConflict(EnterprisePrdError):
    code = "immutable_manifest_conflict"
