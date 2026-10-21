from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml


def _configured_runtime() -> Path | None:
    config = Path.home() / ".enterprise-prd/config.yaml"
    if not config.is_file():
        return None
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    if type(raw) is not dict or type(raw.get("install_root")) is not str:
        return None
    install_root = Path(raw["install_root"]).expanduser()
    runtime = install_root / "current/skill/runtime"
    return runtime if (runtime / "enterprise_prd/cli.py").is_file() else None


def main() -> int:
    runtime = _configured_runtime()
    if runtime is not None:
        environment = os.environ.copy()
        loader = (
            "import runpy,sys;"
            "sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('enterprise_prd.cli',run_name='__main__')"
        )
        os.execve(
            sys.executable,
            [sys.executable, "-c", loader, str(runtime), *sys.argv[1:]],
            environment,
        )
    from .cli import main as cli_main

    return cli_main()
