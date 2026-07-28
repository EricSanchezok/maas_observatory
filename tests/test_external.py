from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tooluse_bench.benchmarks.external import (
    isolated_environment,
    run_logged_command,
)


def test_isolated_environment_does_not_inherit_unrelated_secrets() -> None:
    with patch.dict(
        os.environ,
        {
            "PATH": "/bin",
            "UNRELATED_API_KEY": "must-not-leak",
            "SII_HOLOS_OTHER_API_KEY": "must-not-leak-either",
        },
        clear=True,
    ):
        environment = isolated_environment({"SII_BENCH_API_KEY": "target"})
    assert environment == {"PATH": "/bin", "SII_BENCH_API_KEY": "target"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_logged_command_terminates_descendants_on_timeout(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    outcome = run_logged_command(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        environment=isolated_environment({"PYTHONUNBUFFERED": "1"}),
        timeout_seconds=0.5,
    )

    assert outcome.return_code == 124
    child_pid = int(child_pid_path.read_text())
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("timed-out command left a descendant process running")
