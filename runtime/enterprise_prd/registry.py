from __future__ import annotations

import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .canonical import sha256_file
from .errors import RegistryUnavailable


SKILL_BUNDLE_IGNORE = shutil.ignore_patterns(
    "node_modules",
    "__pycache__",
    "test-results",
    "*.pyc",
    "*.build.json",
)


def build_zip(source: Path, target: Path) -> str:
    source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative_path = path.relative_to(source)
            if (
                "governance" in relative_path.parts
                or "__pycache__" in relative_path.parts
                or relative_path.name == "catalog.local.yaml"
                or relative_path.suffix == ".pyc"
            ):
                continue
            relative = relative_path.as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return sha256_file(target)


def build_skill_bundle(package: Path, skills: Path, target: Path) -> str:
    with tempfile.TemporaryDirectory() as raw:
        staging = Path(raw)
        shutil.copytree(
            package,
            staging / "runtime" / package.name,
            ignore=SKILL_BUNDLE_IGNORE,
        )
        for skill in sorted(path for path in skills.iterdir() if path.is_dir()):
            if not (skill / "SKILL.md").is_file():
                continue
            shutil.copytree(skill, staging / skill.name, ignore=SKILL_BUNDLE_IGNORE)
        return build_zip(staging, target)


class FileRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def fetch_bytes(self, relative: str) -> bytes:
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise ValueError("registry path is invalid")
        if not path.is_file():
            raise RegistryUnavailable(f"registry object is unavailable: {relative}")
        return path.read_bytes()


class HttpsRegistry:
    def __init__(self, base_url: str, *, bearer_token: str | None = None) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https":
            raise ValueError("registry URL must use https")
        self.base_url = base_url.rstrip("/") + "/"
        self.bearer_token = bearer_token

    def fetch_bytes(self, relative: str) -> bytes:
        if relative.startswith("/") or ".." in relative.split("/"):
            raise ValueError("registry path is invalid")
        url = urllib.parse.urljoin(self.base_url, relative)
        delays = (0.0, 0.2, 0.5)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(url)
            if self.bearer_token:
                request.add_header("Authorization", f"Bearer {self.bearer_token}")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise RegistryUnavailable(f"registry HTTP {exc.code}") from exc
                last_error = exc
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
        raise RegistryUnavailable(f"registry unavailable after 3 attempts: {last_error}")
