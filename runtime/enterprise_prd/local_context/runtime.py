from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from enterprise_prd.canonical import canonical_json
from enterprise_prd.contracts import runtime_id
from enterprise_prd.errors import ContractError


def contained(root: Path, identifier: str, field: str) -> Path:
    base = root.expanduser().resolve()
    value = runtime_id(identifier, field)
    path = (base / value).resolve()
    if path.parent != base:
        raise ContractError(f"{field} path escapes runtime root")
    return path


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exclusive_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("local context state is unavailable or corrupt") from exc
    if type(value) is not dict:
        raise ContractError("local context state must be an object")
    return value


@contextmanager
def session_lock(root: Path, session_id: str) -> Iterator[None]:
    base = root.expanduser().resolve()
    contained(base, session_id, "session_id")
    lock_dir = (base / ".locks").resolve()
    if lock_dir.parent != base:
        raise ContractError("lock directory escapes runtime root")
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = (lock_dir / f"{session_id}.lock").resolve()
    if path.parent != lock_dir:
        raise ContractError("lock path escapes runtime root")
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
