from __future__ import annotations

from pathlib import Path
from typing import Callable

from enterprise_prd.errors import ContractError
from enterprise_prd.local_context.contracts import AcquisitionScope, SessionStatus
from enterprise_prd.local_context.grant import ReadGrantService


class LocalSnapshotGrantGuard:
    def __init__(self, root: Path, workflow, *, id_factory: Callable[[], str] | None = None) -> None:
        self.workflow = workflow
        self.service = ReadGrantService(root, workflow, id_factory=id_factory)

    def issue(self, session_id: str) -> Path:
        state = self.workflow.load(session_id)
        if state["status"] != SessionStatus.SECTIONS_CONFIRMED.value:
            raise ContractError("local snapshot grant is not issuable now")
        candidates = {item["source_id"]: item for item in state["candidates"]}
        for source_id in state["confirmed_source_ids"]:
            candidate = candidates[source_id]
            if candidate["adapter"] != "local-snapshot": continue
            selections = tuple(item for item in state["selections"] if item["source_id"] == source_id)
            paths = tuple(item["normalized_path"] for item in selections)
            if not selections or len(paths) != len(set(paths)) or any(item["acquisition_scope"] != AcquisitionScope.FULL_DOCUMENT_LOCAL_EXTRACT.value for item in selections) or any(right.startswith(left + "/") or left.startswith(right + "/") for index, left in enumerate(paths) for right in paths[index + 1:]):
                raise ContractError("local snapshot selections overlap or are invalid")
        return self.service.issue(session_id)
