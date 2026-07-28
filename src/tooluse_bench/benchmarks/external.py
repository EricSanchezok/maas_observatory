"""Shared safe subprocess helpers for isolated benchmark runtimes."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SAFE_ENVIRONMENT_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "TMPDIR",
    "UV_CACHE_DIR",
    "XDG_CACHE_HOME",
}


@dataclass(frozen=True)
class CommandOutcome:
    return_code: int
    wall_seconds: float
    stdout_path: Path
    stderr_path: Path


def isolated_environment(extra: dict[str, str]) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in SAFE_ENVIRONMENT_KEYS
    }
    environment.update(extra)
    return environment


def run_logged_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: float,
) -> CommandOutcome:
    stdout_path = cwd / "adapter.stdout.log"
    stderr_path = cwd / "adapter.stderr.log"
    started = time.perf_counter()
    with (
        stdout_path.open("wb") as stdout,
        stderr_path.open("wb") as stderr,
    ):
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
            )
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            return_code = 124
    return CommandOutcome(
        return_code=return_code,
        wall_seconds=round(time.perf_counter() - started, 6),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
