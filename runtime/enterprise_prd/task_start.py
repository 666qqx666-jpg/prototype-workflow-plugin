from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .canonical import sha256_bytes
from .contracts import runtime_id
from .errors import ContractError, IntegrityError, RegistryUnavailable
from .manifest import manifest_from_bytes
from .registry import FileRegistry, HttpsRegistry
from .source_catalog import SourceCatalog, load_source_catalog
from .updater import apply_manifest


@dataclass(frozen=True)
class StartConfig:
    registry_url: str
    install_root: Path
    credential_env: str | None
    channel: str


def load_start_config(path: Path) -> StartConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if type(raw) is not dict or set(raw) != {
        "schema_version",
        "registry_url",
        "install_root",
        "credential_env",
        "channel",
    }:
        raise ContractError("task-start config fields are invalid")
    if raw["schema_version"] != 1 or type(raw["registry_url"]) is not str:
        raise ContractError("task-start config is invalid")
    install_root = Path(str(raw["install_root"])).expanduser()
    if not install_root.is_absolute():
        raise ContractError("install_root must be absolute or start with ~")
    credential_env = raw["credential_env"]
    if credential_env is not None and (
        type(credential_env) is not str
        or re.fullmatch(r"[A-Z][A-Z0-9_]+", credential_env) is None
    ):
        raise ContractError(
            "credential_env must be null or an uppercase environment variable name"
        )
    if raw["channel"] not in {"canary", "stable"}:
        raise ContractError("channel must be canary or stable")
    return StartConfig(
        raw["registry_url"],
        install_root,
        credential_env,
        raw["channel"],
    )


def registry_from_url(value: str, credential_env: str | None = None):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme == "file" and not parsed.netloc:
        return FileRegistry(Path(urllib.parse.unquote(parsed.path)))
    if parsed.scheme == "https":
        token = os.environ.get(credential_env) if credential_env else None
        if credential_env and not token:
            raise RegistryUnavailable(
                "registry credential environment variable is absent: "
                f"{credential_env}"
            )
        return HttpsRegistry(value.rstrip("/") + "/", bearer_token=token)
    raise ContractError("registry_url must use file:// or https://")


def write_start_config(
    registry_url: str,
    install_root: Path,
    output: Path,
    credential_env: str | None,
    channel: str,
) -> None:
    import yaml

    parsed = urllib.parse.urlparse(registry_url)
    if parsed.scheme not in {"file", "https"}:
        raise ContractError("registry_url must use file:// or https://")
    if (
        credential_env is not None
        and re.fullmatch(r"[A-Z][A-Z0-9_]+", credential_env) is None
    ):
        raise ContractError(
            "credential_env must be an uppercase environment variable name"
        )
    if channel not in {"canary", "stable"}:
        raise ContractError("channel must be canary or stable")
    expanded_root = install_root.expanduser()
    if not expanded_root.is_absolute():
        raise ContractError("install_root must be absolute")
    payload = yaml.safe_dump(
        {
            "schema_version": 1,
            "registry_url": registry_url,
            "install_root": str(expanded_root),
            "credential_env": credential_env,
            "channel": channel,
        },
        sort_keys=False,
    ).encode("utf-8")
    _atomic_bytes(output.expanduser(), payload)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _snapshot(
    run_id: str,
    manifest_payload: bytes,
    manifest,
    install_root: Path,
    status: str,
    last_sync_at: str,
    source_catalog: SourceCatalog | None = None,
) -> dict:
    run_id = runtime_id(run_id, "run_id")
    current = install_root / "current"
    if not current.is_symlink():
        raise ContractError("current release pair is absent")
    payload = {
        "schema_version": 1 if source_catalog is None else 3,
        "run_id": run_id,
        "status": status,
        "channel": manifest.channel,
        "skill_version": manifest.skill.version,
        "pack_version": manifest.company_pack.version,
        "manifest_sha256": sha256_bytes(manifest_payload),
        "pair_path": str(current.resolve()),
        "last_sync_at": last_sync_at,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if source_catalog is not None:
        payload["source_registry"] = {
            "path": str(source_catalog.path),
            "sha256": source_catalog.sha256,
            "schema_version": 2,
        }
    output = install_root / "runs" / f"{run_id}.json"
    if output.exists():
        raise ContractError("run_id already has a locked snapshot")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def require_snapshot_pack(snapshot_path: Path, pack_path: Path) -> dict:
    raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    base_fields = {
        "schema_version",
        "run_id",
        "status",
        "channel",
        "skill_version",
        "pack_version",
        "manifest_sha256",
        "pair_path",
        "last_sync_at",
        "started_at",
    }
    if type(raw) is not dict or raw.get("schema_version") not in {1, 2, 3}:
        raise ContractError("run snapshot fields are invalid")
    expected = base_fields if raw["schema_version"] == 1 else base_fields | ({"source_catalog"} if raw["schema_version"] == 2 else {"source_registry"})
    if set(raw) != expected:
        raise ContractError("run snapshot fields are invalid")
    runtime_id(raw.get("run_id"), "run_id")
    if raw["schema_version"] == 2 and (
        type(raw["source_catalog"]) is not dict
        or set(raw["source_catalog"]) != {"path", "sha256", "adapter"}
        or any(
            type(raw["source_catalog"][field]) is not str
            or not raw["source_catalog"][field]
            for field in ("path", "sha256", "adapter")
        )
    ):
        raise ContractError("run snapshot source-catalog lock is invalid")
    if raw["schema_version"] == 3 and (
        type(raw["source_registry"]) is not dict
        or set(raw["source_registry"]) != {"path", "sha256", "schema_version"}
        or raw["source_registry"].get("schema_version") != 2
    ):
        raise ContractError("run snapshot source-registry lock is invalid")
    if raw["status"] not in {"ready", "degraded", "restart_required"}:
        raise ContractError("run snapshot status is invalid")
    if raw["status"] == "restart_required":
        raise ContractError(
            "restart-required snapshot cannot load project context"
        )
    expected = (Path(raw["pair_path"]) / "pack").resolve()
    if pack_path.resolve() != expected:
        raise ContractError("capability pack is not the run-snapshot pack")
    return raw


def require_snapshot_catalog(
    snapshot_path: Path,
    catalog_path: Path,
    pack_path: Path,
) -> SourceCatalog:
    raw = require_snapshot_pack(snapshot_path, pack_path)
    if raw["schema_version"] not in {2, 3}:
        raise ContractError("Feishu context requires a source-catalog lock")
    catalog = load_source_catalog(catalog_path, pack_path)
    locked = raw["source_catalog"] if raw["schema_version"] == 2 else raw["source_registry"]
    if str(catalog.path) != locked["path"] or catalog.sha256 != locked["sha256"]:
        raise IntegrityError("source catalog changed after task start")
    return catalog


def require_snapshot_registry(
    snapshot_path: Path,
    registry_path: Path,
    pack_path: Path,
) -> SourceCatalog:
    raw = require_snapshot_pack(snapshot_path, pack_path)
    if raw["schema_version"] != 3:
        raise ContractError("context selection requires a source-registry lock")
    catalog = load_source_catalog(registry_path, pack_path)
    locked = raw["source_registry"]
    if str(catalog.path) != locked["path"] or catalog.sha256 != locked["sha256"]:
        raise IntegrityError("source registry changed after task start")
    return catalog


def start_new_task(
    run_id: str,
    config_path: Path,
    source_catalog_path: Path | None = None,
) -> dict:
    run_id = runtime_id(run_id, "run_id")
    config = load_start_config(config_path)
    cached = config.install_root / "cache/release-manifest.yaml"
    current = config.install_root / "current"
    before = current.resolve() if current.is_symlink() else None
    try:
        registry = registry_from_url(
            config.registry_url,
            config.credential_env,
        )
        manifest_payload = registry.fetch_bytes(
            f"{config.channel}-manifest.yaml"
        )
        manifest = manifest_from_bytes(manifest_payload)
        if manifest.channel != config.channel:
            raise ContractError(
                "release manifest channel does not match task configuration"
            )
        apply_manifest(manifest, registry, config.install_root)
        _atomic_bytes(cached, manifest_payload)
        after = current.resolve()
        status = (
            "restart_required"
            if before is not None and before != after
            else "ready"
        )
        synced_at = datetime.fromtimestamp(
            cached.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat(timespec="seconds")
        source_catalog = (
            load_source_catalog(source_catalog_path, current / "pack")
            if source_catalog_path is not None
            else None
        )
        return _snapshot(
            run_id,
            manifest_payload,
            manifest,
            config.install_root,
            status,
            synced_at,
            source_catalog,
        )
    except RegistryUnavailable:
        if not cached.is_file() or not current.is_symlink():
            raise
        manifest_payload = cached.read_bytes()
        manifest = manifest_from_bytes(manifest_payload)
        if manifest.channel != config.channel:
            raise ContractError(
                "cached manifest channel does not match task configuration"
            )
        expected = (
            config.install_root
            / "versions"
            / f"{manifest.skill.version}__{manifest.company_pack.version}"
        )
        if current.resolve() != expected.resolve():
            raise ContractError("offline cached pair is not allowed to start")
        synced_at = datetime.fromtimestamp(
            cached.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat(timespec="seconds")
        source_catalog = (
            load_source_catalog(source_catalog_path, current / "pack")
            if source_catalog_path is not None
            else None
        )
        return _snapshot(
            run_id,
            manifest_payload,
            manifest,
            config.install_root,
            "degraded",
            synced_at,
            source_catalog,
        )
