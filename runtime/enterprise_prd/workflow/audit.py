from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from enterprise_prd.errors import ContractError


@dataclass(frozen=True)
class AuditRecord:
    schema_version: int
    run_id: str
    participant_id: str
    skill_version: str
    pack_version: str
    adapter: str
    source_revisions: tuple[tuple[str, str], ...]
    started_at: str
    completed_at: str
    final_status: str
    review_rounds: int
    blocker_count: int
    degraded_flags: tuple[str, ...]

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ContractError("audit schema_version must be 1")
        identities = (
            self.run_id,
            self.participant_id,
            self.skill_version,
            self.pack_version,
            self.adapter,
        )
        if not all(type(value) is str and value.strip() for value in identities):
            raise ContractError("audit identity fields are required")
        if self.final_status not in {"blocked", "revision_required", "passed", "conflict"}:
            raise ContractError("audit final_status is invalid")
        if type(self.review_rounds) is not int or self.review_rounds < 0:
            raise ContractError("review_rounds is invalid")
        if type(self.blocker_count) is not int or self.blocker_count < 0:
            raise ContractError("blocker_count is invalid")
        if (
            type(self.source_revisions) is not tuple
            or any(
                type(item) is not tuple
                or len(item) != 2
                or any(
                    type(value) is not str
                    or not value.strip()
                    or "\n" in value
                    or len(value) > 512
                    for value in item
                )
                for item in self.source_revisions
            )
        ):
            raise ContractError("source_revisions are invalid")
        if (
            type(self.degraded_flags) is not tuple
            or any(
                type(value) is not str or not value.strip()
                for value in self.degraded_flags
            )
        ):
            raise ContractError("degraded_flags are invalid")
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
        except (TypeError, ValueError) as exc:
            raise ContractError("audit timestamps are invalid") from exc
        if (
            start.tzinfo is None
            or start.utcoffset() is None
            or end.tzinfo is None
            or end.utcoffset() is None
            or end < start
        ):
            raise ContractError("audit timestamps are invalid")


def write_audit(record: AuditRecord, output: Path) -> None:
    record.validate()
    payload = asdict(record)
    payload["source_revisions"] = [list(item) for item in record.source_revisions]
    payload["degraded_flags"] = list(record.degraded_flags)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
