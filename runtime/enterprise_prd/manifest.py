from __future__ import annotations

from pathlib import Path

import yaml

from .contracts import ReleaseRef, StableManifest
from .errors import ContractError


def manifest_from_dict(raw: dict) -> StableManifest:
    if type(raw) is not dict:
        raise ContractError("release manifest must be a mapping")
    expected = {
        "schema_version", "channel", "skill", "company_pack",
        "compatible_skill", "published_by", "published_at", "force_update",
    }
    if set(raw) != expected or type(raw["skill"]) is not dict or type(raw["company_pack"]) is not dict:
        raise ContractError("release manifest fields are invalid")
    ref_fields = {"name", "version", "archive", "sha256"}
    if set(raw["skill"]) != ref_fields or set(raw["company_pack"]) != ref_fields:
        raise ContractError("release reference fields are invalid")
    manifest = StableManifest(
        schema_version=raw["schema_version"],
        channel=raw["channel"],
        skill=ReleaseRef(**raw["skill"]),
        company_pack=ReleaseRef(**raw["company_pack"]),
        compatible_skill=raw["compatible_skill"],
        published_by=raw["published_by"],
        published_at=raw["published_at"],
        force_update=raw["force_update"],
    )
    manifest.validate()
    return manifest


def manifest_from_bytes(payload: bytes) -> StableManifest:
    raw = yaml.safe_load(payload.decode("utf-8"))
    return manifest_from_dict(raw)


def manifest_from_file(path: Path) -> StableManifest:
    return manifest_from_bytes(path.read_bytes())
