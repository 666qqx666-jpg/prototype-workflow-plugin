from __future__ import annotations

import fcntl
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .canonical import sha256_bytes
from .contracts import ReleaseRef, StableManifest, UpdateStatus
from .errors import ContractError, IntegrityError, RegistryUnavailable


def _safe_extract(payload: bytes, target: Path) -> None:
    target_resolved = target.resolve()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if destination != target_resolved and target_resolved not in destination.parents:
                raise IntegrityError("archive contains path traversal")
        archive.extractall(target)


def _fetch_verified(ref: ReleaseRef, registry) -> bytes:
    payload = registry.fetch_bytes(ref.archive)
    if sha256_bytes(payload) != ref.sha256:
        raise IntegrityError(f"checksum mismatch for {ref.name}")
    return payload


def _install_release(ref: ReleaseRef, payload: bytes, parent: Path) -> Path:
    destination = parent / f"{ref.name}-{ref.version}"
    if destination.exists():
        return destination
    raw = tempfile.mkdtemp(dir=parent, prefix=".extract-")
    extracted = Path(raw)
    try:
        _safe_extract(payload, extracted)
        os.replace(extracted, destination)
    finally:
        if extracted.exists():
            shutil.rmtree(extracted)
    return destination


def _apply_manifest_locked(manifest: StableManifest, registry, install_root: Path) -> Path:
    manifest.validate()
    install_root.mkdir(parents=True, exist_ok=True)
    active_path = install_root / "active-release.json"
    incoming_identity = {
        "published_at": manifest.published_at,
        "skill_version": manifest.skill.version,
        "skill_sha256": manifest.skill.sha256,
        "pack_version": manifest.company_pack.version,
        "pack_sha256": manifest.company_pack.sha256,
    }
    if active_path.is_file():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if type(active) is not dict or set(active) != set(incoming_identity):
            raise IntegrityError("active release receipt is invalid")
        incoming_time = datetime.fromisoformat(manifest.published_at)
        active_time = datetime.fromisoformat(active["published_at"])
        same_versions = (
            manifest.skill.version == active["skill_version"]
            and manifest.company_pack.version == active["pack_version"]
        )
        same_hashes = (
            manifest.skill.sha256 == active["skill_sha256"]
            and manifest.company_pack.sha256 == active["pack_sha256"]
        )
        if same_versions and not same_hashes:
            raise ContractError("a released version cannot be reused for different bytes")
        if incoming_time < active_time:
            raise ContractError("stale manifest cannot replace a newer published pair")
        if incoming_time == active_time and active != incoming_identity:
            raise ContractError("one published_at cannot identify two release pairs")

    skill_payload = _fetch_verified(manifest.skill, registry)
    pack_payload = _fetch_verified(manifest.company_pack, registry)

    versions = install_root / "versions"
    versions.mkdir(exist_ok=True)
    pair_name = f"{manifest.skill.version}__{manifest.company_pack.version}"
    pair = versions / pair_name
    if not pair.exists():
        staging = versions / f".staging-{pair_name}"
        staging.mkdir()
        try:
            skill = _install_release(manifest.skill, skill_payload, staging)
            pack = _install_release(manifest.company_pack, pack_payload, staging)
            (staging / "skill").symlink_to(skill.name)
            (staging / "pack").symlink_to(pack.name)
            os.replace(staging, pair)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    active_new = install_root / ".active-release.new"
    active_new.write_text(json.dumps(incoming_identity, sort_keys=True), encoding="utf-8")
    os.replace(active_new, active_path)
    new_link = install_root / ".current-new"
    if new_link.exists() or new_link.is_symlink():
        new_link.unlink()
    new_link.symlink_to(Path("versions") / pair_name)
    os.replace(new_link, install_root / "current")
    return pair


def apply_manifest(manifest: StableManifest, registry, install_root: Path) -> Path:
    install_root.mkdir(parents=True, exist_ok=True)
    lock_path = install_root / ".update.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _apply_manifest_locked(manifest, registry, install_root)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def inspect_update(
    manifest: StableManifest,
    registry,
    install_root: Path,
) -> UpdateStatus:
    manifest.validate()
    try:
        for ref in (manifest.skill, manifest.company_pack):
            if sha256_bytes(registry.fetch_bytes(ref.archive)) != ref.sha256:
                return UpdateStatus.FAILED_INTEGRITY
    except RegistryUnavailable:
        return UpdateStatus.DEGRADED_CACHE
    expected = (
        install_root
        / "versions"
        / f"{manifest.skill.version}__{manifest.company_pack.version}"
    )
    current = install_root / "current"
    if current.is_symlink() and current.resolve() == expected.resolve():
        return UpdateStatus.CURRENT
    if manifest.force_update:
        return UpdateStatus.BLOCKED_FORCE_UPDATE
    return UpdateStatus.UPDATE_AVAILABLE
