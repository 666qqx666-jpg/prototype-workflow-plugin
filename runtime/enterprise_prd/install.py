from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .contracts import runtime_id
from .errors import ContractError
from .prototype.store import sha256_file, write_once_json


PRD_SKILLS = (
    "enterprise-prd-chain",
    "enterprise-prd-writer",
    "enterprise-prd-review",
)
CLIENT_ROOTS = (".codex", ".claude", ".openclaw")
RECEIPT_KEYS = {
    "schema_version", "home", "source", "skill_names",
    "links", "dependency", "smoke",
}
LINK_KEYS = {"path", "previous_state", "previous_target", "installed_target"}


def _names(skill_names: tuple[str, ...]) -> tuple[str, ...]:
    if type(skill_names) is not tuple or not skill_names:
        raise ContractError("skill_names must be a non-empty tuple")
    result = tuple(runtime_id(name, "Skill name") for name in skill_names)
    if len(result) != len(set(result)):
        raise ContractError("skill_names has duplicates")
    return result


def _link_paths(home: Path, name: str) -> tuple[Path, ...]:
    shared = home / ".agents/skills" / name
    return (
        shared,
        *(home / client / "skills" / name for client in CLIENT_ROOTS),
    )


def _installed_target(home: Path, source: Path, name: str, path: Path) -> Path:
    shared = home / ".agents/skills" / name
    return source / name if path == shared else shared


def _replace_link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_symlink():
        raise ContractError(f"refusing to overwrite real path: {path}")
    if path.is_symlink():
        path.unlink()
    path.symlink_to(target)


def _capture_links(home: Path, source: Path, names: tuple[str, ...]) -> list[dict]:
    links: list[dict] = []
    for name in names:
        for path in _link_paths(home, name):
            if path.exists() and not path.is_symlink():
                raise ContractError(f"refusing to overwrite real path: {path}")
            links.append({
                "path": str(path),
                "previous_state": "symlink" if path.is_symlink() else "absent",
                "previous_target": os.readlink(path) if path.is_symlink() else None,
                "installed_target": str(_installed_target(home, source, name, path)),
            })
    return links


def _restore_links(links: list[dict]) -> None:
    for item in reversed(links):
        path = Path(item["path"])
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            raise ContractError(f"rollback target became a real path: {path}")
        if item["previous_state"] == "symlink":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(item["previous_target"])


def _restore_dependencies(source: Path) -> tuple[dict, dict]:
    core = source / "prototype-design-workflow"
    shell = core / "assets/developer-reader-shell"
    lockfile = shell / "package-lock.json"
    tests = tuple(sorted((core / "tests").glob("*.test.mjs")))
    required_tests = {
        "developer-readable-assets.test.mjs", "validator.test.mjs",
        "first-generation.test.mjs", "manifest-v4.test.mjs", "lineage.test.mjs",
        "independent-review-v2.test.mjs", "product-convergence-freeze.test.mjs",
    }
    if not lockfile.is_file() or not required_tests <= {path.name for path in tests}:
        raise ContractError("Prototype dependency or smoke inputs are missing")
    version = subprocess.run(
        ["npm", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if version.returncode != 0:
        raise ContractError("npm is unavailable")
    install = subprocess.run(
        ["npm", "ci"],
        cwd=shell,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if install.returncode != 0:
        raise ContractError("npm ci failed")
    playwright = shell / "node_modules/.bin/playwright"
    if not playwright.is_file():
        raise ContractError("Playwright CLI is missing after npm ci")
    browser = subprocess.run(
        [str(playwright), "install", "chromium"],
        cwd=shell,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if browser.returncode != 0:
        raise ContractError("Playwright Chromium restore failed")
    smoke = subprocess.run(
        ["node", "--test", *(str(path) for path in tests)],
        cwd=core,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    summary = smoke.stdout + "\n" + smoke.stderr
    tests_match = re.search(r"(?m)^(?:#|ℹ) tests (\d+)$", summary)
    pass_match = re.search(r"(?m)^(?:#|ℹ) pass (\d+)$", summary)
    fail_match = re.search(r"(?m)^(?:#|ℹ) fail (\d+)$", summary)
    counts = (
        int(tests_match.group(1)) if tests_match else -1,
        int(pass_match.group(1)) if pass_match else -1,
        int(fail_match.group(1)) if fail_match else -1,
    )
    if smoke.returncode != 0 or counts[0] < 32 or counts[1] != counts[0] or counts[2] != 0:
        raise ContractError("Prototype smoke must pass every discovered test (at least 32)")
    return (
        {
            "required": True,
            "lockfile_sha256": sha256_file(lockfile),
            "npm_version": version.stdout.strip(),
            "browser": "chromium",
            "browser_status": "pass",
            "status": "pass",
        },
        {"status": "pass", "tests": counts[0], "passed": counts[1], "failed": counts[2]},
    )


def install_shared_links(
    source: Path,
    *,
    home: Path | None = None,
    skill_names: tuple[str, ...] = PRD_SKILLS,
    receipt_path: Path | None = None,
    restore_prototype_dependencies: bool = False,
) -> dict:
    source = Path(os.path.abspath(source.expanduser()))
    home = (home or Path.home()).resolve()
    names = _names(skill_names)
    for name in names:
        if not (source / name / "SKILL.md").is_file():
            raise ContractError(f"missing Skill source: {source / name}")
    if restore_prototype_dependencies and receipt_path is None:
        raise ContractError("dependency restore requires --receipt")
    if receipt_path is not None and receipt_path.exists():
        raise ContractError("install receipt already exists")
    links = _capture_links(home, source, names)
    dependency = {
        "required": False,
        "lockfile_sha256": None,
        "npm_version": None,
        "browser": None,
        "browser_status": "skipped",
        "status": "skipped",
    }
    smoke = {"status": "skipped", "tests": 0, "passed": 0, "failed": 0}
    published = False
    try:
        if restore_prototype_dependencies:
            dependency, smoke = _restore_dependencies(source)
        published = True
        for name in names:
            for path in _link_paths(home, name):
                _replace_link(path, _installed_target(home, source, name, path))
        if not all(verify_shared_links(source, home=home, skill_names=names).values()):
            raise ContractError("shared Skill smoke verification failed")
        receipt = {
            "schema_version": 1,
            "home": str(home),
            "source": str(source),
            "skill_names": list(names),
            "links": links,
            "dependency": dependency,
            "smoke": smoke,
        }
        if receipt_path is not None:
            write_once_json(receipt_path, receipt)
        return receipt
    except Exception:
        if published:
            _restore_links(links)
        raise


def verify_shared_links(
    source: Path,
    *,
    home: Path | None = None,
    skill_names: tuple[str, ...] = PRD_SKILLS,
) -> dict[str, bool]:
    source = Path(os.path.abspath(source.expanduser()))
    home = (home or Path.home()).resolve()
    names = _names(skill_names)
    result = {}
    for name in names:
        expected = (source / name).resolve()
        result[name] = all(
            path.is_symlink() and path.resolve() == expected
            for path in _link_paths(home, name)
        )
    return result


def rollback_shared_links(receipt_path: Path, *, home: Path | None = None) -> dict:
    raw = json.loads(receipt_path.read_text(encoding="utf-8"))
    if type(raw) is not dict or set(raw) != RECEIPT_KEYS:
        raise ContractError("install receipt fields are invalid")
    expected_home = (home or Path(raw["home"])).resolve()
    if raw["schema_version"] != 1 or Path(raw["home"]).resolve() != expected_home:
        raise ContractError("install receipt home differs")
    names = _names(tuple(raw["skill_names"]))
    if type(raw["links"]) is not list:
        raise ContractError("install receipt links must be a list")
    allowed = {
        str(path): name
        for name in names
        for path in _link_paths(expected_home, name)
    }
    if len(raw["links"]) != len(allowed):
        raise ContractError("install receipt link count is invalid")
    seen: set[str] = set()
    for item in raw["links"]:
        if type(item) is not dict or set(item) != LINK_KEYS:
            raise ContractError("install receipt link fields are invalid")
        path_text = item["path"]
        if path_text not in allowed or path_text in seen:
            raise ContractError("install receipt link path is invalid")
        seen.add(path_text)
        if item["previous_state"] not in {"absent", "symlink"}:
            raise ContractError("install receipt previous state is invalid")
        if (
            item["previous_state"] == "absent"
            and item["previous_target"] is not None
        ) or (
            item["previous_state"] == "symlink"
            and type(item["previous_target"]) is not str
        ):
            raise ContractError("install receipt previous target is invalid")
        path = Path(path_text)
        if not path.is_symlink() or os.readlink(path) != item["installed_target"]:
            raise ContractError("installed Skill link drifted; rollback is unsafe")
    _restore_links(raw["links"])
    return {"status": "pass", "restored_links": len(raw["links"])}
