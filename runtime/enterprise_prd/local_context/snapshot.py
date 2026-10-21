from __future__ import annotations

from dataclasses import dataclass

from .contracts import SessionStatus


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    status: SessionStatus
    state_bytes: bytes
    action: str
