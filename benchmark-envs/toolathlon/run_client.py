"""Launch the pinned official Toolathlon client without secrets in argv.

The API key is read from the environment and inserted into Python's in-process
``sys.argv`` only after startup, so it is not exposed by process listings.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main() -> None:
    client_path = Path(required("TOOLATHLON_CLIENT_PATH")).resolve()
    arguments = [
        str(client_path),
        "run",
        "--mode",
        "public",
        "--base-url",
        required("SII_BENCH_BASE_URL"),
        "--model-name",
        required("SII_BENCH_MODEL_ID"),
        "--output-dir",
        required("TOOLATHLON_OUTPUT_DIR"),
        "--server-host",
        required("TOOLATHLON_SERVER_HOST"),
        "--api-key",
        required("SII_BENCH_API_KEY"),
        "--workers",
        os.environ.get("TOOLATHLON_WORKERS", "10"),
        "--server-port",
        os.environ.get("TOOLATHLON_SERVER_PORT", "8080"),
        "--custom-job-id",
        required("TOOLATHLON_JOB_ID"),
    ]
    if task_list := os.environ.get("TOOLATHLON_TASK_LIST_FILE"):
        arguments.extend(["--task-list-file", task_list])
    sys.argv = arguments
    runpy.run_path(str(client_path), run_name="__main__")


if __name__ == "__main__":
    main()
