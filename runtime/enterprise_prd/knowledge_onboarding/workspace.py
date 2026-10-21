from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.contracts import HASH_RE, runtime_id
from enterprise_prd.errors import ContractError

from .contracts import OnboardingSession
from .errors import ConcurrentOnboardingConflict, OnboardingError

PRIVATE_FILES = frozenset(
    {
        "approvals.json",
        "proposal.json",
        "governance.json",
        "selection.json",
        "proposed-registry.json",
        "full-dry-run.json",
        "selected-dry-run.json",
        "receipt.json",
    }
)

ARTIFACT_NAMES = frozenset(
    {
        "approvals",
        "proposal",
        "governance",
        "selection",
        "proposed-registry",
        "full-dry-run",
        "selected-dry-run",
    }
)


def _directory(path: Path, message: str) -> Path:
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError(message)
        if info.st_mode & 0o777 != 0o700:
            raise ContractError(message)
    else:
        path.mkdir(mode=0o700, parents=True)
    return path.resolve(strict=True)


def _validate_private_root(root: Path) -> Path:
    path = Path(root)
    if path.is_symlink():
        raise ContractError("onboarding root cannot be a symlink")
    return _directory(path, "onboarding root is invalid")


class OnboardingWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = _validate_private_root(root)

    def _session(self, onboarding_id: str, *, create: bool) -> Path:
        path = self.root / onboarding_id
        if create:
            return _directory(path, "onboarding session directory is invalid")
        if not os.path.lexists(path):
            raise OnboardingError("onboarding_not_found")
        return _directory(path, "onboarding session directory is invalid")

    def _locks(self) -> Path:
        return _directory(self.root / ".locks", "onboarding lock directory is invalid")

    @contextmanager
    def _lock(self, onboarding_id: str):
        locks = self._locks()
        path = locks / f"{onboarding_id}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ContractError("onboarding lock cannot be opened safely") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600:
                raise ContractError("onboarding lock is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _state_path(self, onboarding_id: str, *, create_session: bool) -> Path:
        path = self._session(onboarding_id, create=create_session) / "state.json"
        if os.path.lexists(path) and path.is_symlink():
            raise ContractError("onboarding state cannot be a symlink")
        return path

    def _read(self, path: Path) -> tuple[OnboardingSession, str]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise OnboardingError("onboarding_not_found") from exc
        except OSError as exc:
            raise ContractError("onboarding state cannot be read safely") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600 or info.st_size > 1024 * 1024:
                raise ContractError("onboarding state is invalid")
            payload = b""
            while len(payload) <= 1024 * 1024:
                chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - len(payload)))
                if not chunk:
                    break
                payload += chunk
        finally:
            os.close(descriptor)
        if len(payload) > 1024 * 1024:
            raise ContractError("onboarding state is invalid")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("onboarding state is invalid") from exc
        session = OnboardingSession.from_mapping(value)
        canonical = canonical_json(session.to_mapping()) + b"\n"
        if payload != canonical:
            raise ContractError("onboarding state is not canonical")
        return session, sha256_bytes(payload)

    @staticmethod
    def _replace(path: Path, session: OnboardingSession) -> str:
        session.validate()
        payload = canonical_json(session.to_mapping()) + b"\n"
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor != -1:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)
        return sha256_bytes(payload)

    def _create_locked(self, session: OnboardingSession) -> str:
        session.validate()
        onboarding_id = runtime_id(session.onboarding_id, "onboarding_id")
        with self._lock(onboarding_id):
            path = self._state_path(onboarding_id, create_session=True)
            if path.exists():
                current, current_hash = self._read(path)
                if current != session:
                    raise ConcurrentOnboardingConflict()
                return current_hash
            return self._replace(path, session)

    def _load_locked(self, onboarding_id: str) -> tuple[OnboardingSession, str]:
        with self._lock(onboarding_id):
            return self._read(self._state_path(onboarding_id, create_session=False))

    def _commit_locked(self, onboarding_id: str, expected_state_hash: str, session: OnboardingSession) -> str:
        if HASH_RE.fullmatch(expected_state_hash) is None:
            raise ContractError("expected_state_hash is invalid")
        session.validate()
        if session.onboarding_id != onboarding_id:
            raise ContractError("onboarding_id changed")
        with self._lock(onboarding_id):
            path = self._state_path(onboarding_id, create_session=False)
            _, current_hash = self._read(path)
            if current_hash != expected_state_hash:
                raise ConcurrentOnboardingConflict()
            return self._replace(path, session)

    def create(self, session: OnboardingSession) -> str:
        return self._create_locked(session)

    def load(self, onboarding_id: str) -> tuple[OnboardingSession, str]:
        return self._load_locked(runtime_id(onboarding_id, "onboarding_id"))

    def commit(self, onboarding_id: str, expected_state_hash: str, session: OnboardingSession) -> str:
        return self._commit_locked(runtime_id(onboarding_id, "onboarding_id"), expected_state_hash, session)

    def private_path(self, onboarding_id: str, name: str) -> Path:
        return self._closed_private_path(runtime_id(onboarding_id, "onboarding_id"), name)

    def artifact_directory(self, onboarding_id: str) -> Path:
        safe_id = runtime_id(onboarding_id, "onboarding_id")
        return _directory(
            self._session(safe_id, create=False) / "artifacts",
            "onboarding artifact directory is invalid",
        )

    def artifact_path(self, onboarding_id: str, logical_name: str, digest: str) -> Path:
        if logical_name not in ARTIFACT_NAMES or HASH_RE.fullmatch(digest) is None:
            raise ContractError("onboarding artifact identity is invalid")
        path = self.artifact_directory(onboarding_id) / f"{logical_name}-{digest}.json"
        if os.path.lexists(path) and path.is_symlink():
            raise ContractError("onboarding artifact cannot be a symlink")
        return path

    def _closed_private_path(self, onboarding_id: str, name: str) -> Path:
        if name not in PRIVATE_FILES:
            raise ContractError("private onboarding filename is invalid")
        path = self._session(onboarding_id, create=False) / name
        if os.path.lexists(path) and path.is_symlink():
            raise ContractError("private onboarding file cannot be a symlink")
        return path
