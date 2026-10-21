from enterprise_prd.errors import EnterprisePrdError


class ConcurrentDirectoryConflict(EnterprisePrdError):
    code = "concurrent_directory_conflict"


class InputChangedDuringImport(EnterprisePrdError):
    code = "input_changed_during_import"
