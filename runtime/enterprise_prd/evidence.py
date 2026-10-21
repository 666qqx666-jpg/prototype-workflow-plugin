from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .canonical import load_yaml, sha256_file
from .errors import ContractError


def build_evidence(
    repo: Path,
    required_tags: tuple[str, ...],
    manifest_path: Path,
) -> dict:
    repo = repo.resolve()
    tags = subprocess.run(
        ["git", "tag", "--list"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    missing = sorted(set(required_tags) - set(tags))
    if missing:
        raise ContractError(f"required tags are missing: {missing}")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    manifest_path = manifest_path.resolve()
    registry_root = (repo / "registry").resolve()
    if (
        registry_root not in manifest_path.parents
        or manifest_path.name
        not in {"canary-manifest.yaml", "stable-manifest.yaml"}
    ):
        raise ContractError(
            "evidence manifest path is outside the release registry"
        )
    manifest = load_yaml(manifest_path)
    releases = {}
    for key in ("skill", "company_pack"):
        archive = repo / "registry" / manifest[key]["archive"]
        actual = sha256_file(archive)
        if actual != manifest[key]["sha256"]:
            raise ContractError(f"{key} archive hash mismatch")
        releases[key] = {
            "archive": str(archive.relative_to(repo)),
            "sha256": actual,
        }
    bootstrap_wheels = sorted((repo / "registry/bootstrap").glob("*.whl"))
    if len(bootstrap_wheels) != 1:
        raise ContractError("evidence requires exactly one bootstrap wheel")
    bootstrap = bootstrap_wheels[0]
    return {
        "status": "pass",
        "git_head": head,
        "required_tags": list(required_tags),
        "release_manifest": str(manifest_path.relative_to(repo)),
        "release_manifest_sha256": sha256_file(manifest_path),
        "releases": releases,
        "bootstrap": {
            "wheel": str(bootstrap.relative_to(repo)),
            "sha256": sha256_file(bootstrap),
        },
    }


def write_evidence(payload: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
