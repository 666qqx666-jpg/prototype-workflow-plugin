from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .canonical import load_yaml
from .contracts import PackManifest
from .errors import ContractError


@dataclass(frozen=True)
class CapabilityPack:
    root: Path
    manifest: PackManifest


def load_pack(root: Path) -> CapabilityPack:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ContractError(f"pack root does not exist: {root}")
    raw = load_yaml(resolved / "pack.yaml")
    expected = {
        "schema_version", "pack_id", "version", "compatible_skill",
        "published_by", "published_at", "runtime_paths",
    }
    if set(raw) != expected or type(raw["runtime_paths"]) is not list:
        raise ContractError("pack.yaml fields are invalid")
    manifest = PackManifest(
        schema_version=raw["schema_version"],
        pack_id=raw["pack_id"],
        version=raw["version"],
        compatible_skill=raw["compatible_skill"],
        published_by=raw["published_by"],
        published_at=raw["published_at"],
        runtime_paths=tuple(raw["runtime_paths"]),
    )
    manifest.validate()
    for relative in manifest.runtime_paths:
        path = (resolved / relative).resolve()
        if resolved not in path.parents or not path.exists():
            raise ContractError(f"runtime path is missing or escapes pack: {relative}")
    return CapabilityPack(root=resolved, manifest=manifest)


def runtime_files(pack: CapabilityPack) -> tuple[Path, ...]:
    files: list[Path] = [pack.root / "pack.yaml"]
    for relative in pack.manifest.runtime_paths:
        path = pack.root / relative
        if path.is_file():
            files.append(path)
        else:
            files.extend(item for item in sorted(path.rglob("*")) if item.is_file())
    for path in files:
        relative = path.relative_to(pack.root)
        if relative.parts and relative.parts[0] == "governance":
            raise ContractError("governance files cannot enter runtime release")
    return tuple(files)
