from enterprise_prd.errors import ContractError


class TaskKnowledgeError(ContractError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
