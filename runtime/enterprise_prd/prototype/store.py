from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .contracts import ID_RE
from .errors import ContractError, IntegrityError, PathBoundaryError


def canonical_json_bytes(document: dict) -> bytes:
    if type(document) is not dict:
        raise ContractError("JSON document must be a mapping")
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_file():
        raise IntegrityError(f"artifact is missing: {path}")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_run_path(run_root: Path, runtime_id: str, filename: str) -> Path:
    if ID_RE.fullmatch(runtime_id) is None:
        raise PathBoundaryError("runtime ID is invalid")
    if (
        type(filename) is not str
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise PathBoundaryError("filename is invalid")
    root = run_root.resolve()
    target = (root / runtime_id / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathBoundaryError("run path escapes run root") from exc
    return target


def write_once_json(path: Path, document: dict, *, binding_check=None) -> str:
    payload = canonical_json_bytes(document)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                if binding_check is not None:
                    binding_check()
                current = path.read_bytes()
                if current != payload:
                    raise IntegrityError(f"refusing to replace immutable file: {path}")
                if binding_check is not None:
                    binding_check()
                return hashlib.sha256(current).hexdigest()
            descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
            temporary = Path(temporary_name)
            published_identity = None
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                    stat = os.fstat(stream.fileno())
                    prepared_identity = (stat.st_dev, stat.st_ino)
                if binding_check is not None:
                    binding_check()
                os.replace(temporary, path)
                published_identity = prepared_identity
                stat = path.lstat()
                if (stat.st_dev, stat.st_ino) != prepared_identity:
                    raise IntegrityError("published immutable record was replaced")
                parent_descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
                if binding_check is not None:
                    if path.read_bytes() != payload:
                        raise IntegrityError("published immutable record changed")
                    binding_check()
                return hashlib.sha256(payload).hexdigest()
            except BaseException:
                if published_identity is not None and path.exists():
                    stat = path.lstat()
                    if (stat.st_dev, stat.st_ino) == published_identity:
                        path.unlink()
                        parent_descriptor = os.open(path.parent, os.O_RDONLY)
                        try:
                            os.fsync(parent_descriptor)
                        finally:
                            os.close(parent_descriptor)
                raise
            finally:
                if temporary.exists():
                    temporary.unlink()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot load JSON artifact: {path}") from exc
    if type(value) is not dict:
        raise ContractError("JSON artifact must be a mapping")
    return value
