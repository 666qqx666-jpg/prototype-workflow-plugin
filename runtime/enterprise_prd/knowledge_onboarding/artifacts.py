from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from enterprise_prd.canonical import canonical_json, sha256_bytes
from enterprise_prd.errors import ContractError

from .errors import OnboardingError
from .workspace import OnboardingWorkspace

MAX_ARTIFACT_BYTES = 1024 * 1024


class ArtifactStore:
    def __init__(self, workspace: OnboardingWorkspace) -> None:
        self.workspace = workspace

    def save(self, onboarding_id: str, logical_name: str, value: dict) -> str:
        canonical = canonical_json(value)
        payload = canonical + b"\n"
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ContractError("artifact is invalid")
        digest = sha256_bytes(canonical)
        directory = self.workspace.artifact_directory(onboarding_id)
        target = self.workspace.artifact_path(onboarding_id, logical_name, digest)
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".artifact-",
                suffix=".json",
                dir=directory,
            )
        except OSError as exc:
            raise ContractError("artifact is invalid") from exc
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                if self.load(onboarding_id, logical_name, digest) != value:
                    raise ContractError("artifact is invalid")
            except OSError as exc:
                raise ContractError("artifact is invalid") from exc
            Path(temporary).unlink(missing_ok=True)
            try:
                parent = os.open(directory, os.O_RDONLY)
            except OSError as exc:
                raise ContractError("artifact is invalid") from exc
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except OSError as exc:
            raise ContractError("artifact is invalid") from exc
        finally:
            if descriptor != -1:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)
        return digest

    def load(self, onboarding_id: str, logical_name: str, digest: str | None) -> dict:
        if digest is None:
            raise OnboardingError("governance_incomplete")
        path = self.workspace.artifact_path(onboarding_id, logical_name, digest)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise ContractError("artifact is invalid") from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o777 != 0o600
                or info.st_size > MAX_ARTIFACT_BYTES
            ):
                raise ContractError("artifact is invalid")
            payload = b""
            while len(payload) <= MAX_ARTIFACT_BYTES:
                chunk = os.read(
                    descriptor,
                    min(65536, MAX_ARTIFACT_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload += chunk
        except OSError as exc:
            raise ContractError("artifact is invalid") from exc
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("artifact is invalid") from exc
        if (
            type(value) is not dict
            or canonical_json(value) + b"\n" != payload
            or sha256_bytes(canonical_json(value)) != digest
        ):
            raise ContractError("artifact is invalid")
        return value
