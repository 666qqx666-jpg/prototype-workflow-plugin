from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable

from .errors import AccessDenied, ContractError, RegistryUnavailable


PERMANENT_ACCESS = ("permission denied", "forbidden", "missing scope", "not found")
TRANSIENT = ("rate limit", "timeout", "temporary", "network", "connection reset")


class LarkRunner:
    def __init__(
        self,
        *,
        run: Callable = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.run = run
        self.sleep = sleep

    def json(self, arguments: list[str]) -> dict:
        argv = ["lark-cli", *arguments, "--as", "user", "--format", "json"]
        delays = (0.0, 0.2, 0.5)
        last = ""
        for delay in delays:
            if delay:
                self.sleep(delay)
            result = self.run(argv, text=True, capture_output=True)
            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError as exc:
                    raise ContractError("lark-cli returned non-JSON output") from exc
                if type(payload) is not dict:
                    raise ContractError("lark-cli JSON root must be an object")
                return payload
            last = (result.stderr or result.stdout).strip().casefold()
            if any(marker in last for marker in PERMANENT_ACCESS):
                raise AccessDenied(last)
            if not any(marker in last for marker in TRANSIENT):
                raise ContractError(f"lark-cli failed: {last}")
        raise RegistryUnavailable(f"lark-cli transient failure after 3 attempts: {last}")
