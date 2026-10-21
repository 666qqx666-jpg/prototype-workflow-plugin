from enterprise_prd.errors import EnterprisePrdError


class OnboardingError(EnterprisePrdError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConcurrentOnboardingConflict(OnboardingError):
    def __init__(self) -> None:
        super().__init__("concurrent_state_conflict")
