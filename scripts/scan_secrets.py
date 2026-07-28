"""Conservative secret scan for tracked and untracked public-repository files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tooluse_bench.config import PROJECT_ROOT

PATTERNS = {
    "bearer token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "common API key": re.compile(r"\b(?:sk|key)-[A-Za-z0-9_-]{12,}\b"),
    "nonblank example secret": re.compile(
        r"(?m)^[A-Z][A-Z0-9_]*(?:API_KEY|BASE_URL)=\S+"
    ),
}
SKIP_SUFFIXES = {
    ".gz",
    ".jpg",
    ".jpeg",
    ".lock",
    ".pdf",
    ".png",
    ".pyc",
    ".tar",
    ".whl",
    ".zip",
}


def candidate_files() -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        PROJECT_ROOT / item.decode()
        for item in completed.stdout.split(b"\0")
        if item and not item.decode().startswith(".git/")
    ]


def main() -> None:
    findings: list[str] = []
    for path in candidate_files():
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(PROJECT_ROOT)
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{relative}: {name}")
    if findings:
        raise SystemExit("Potential secrets detected:\n" + "\n".join(findings))
    print("No public-repository secret patterns detected.")


if __name__ == "__main__":
    main()
