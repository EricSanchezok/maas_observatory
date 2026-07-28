"""Check repository-local Markdown links without relying on the network."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from tooluse_bench.config import PROJECT_ROOT

INLINE_LINK = re.compile(r"!?\[[^\]]*]\((?P<target>[^)\n]+)\)")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "data:")


def tracked_markdown_files(root: Path = PROJECT_ROOT) -> list[Path]:
    process = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        root / path
        for path in process.stdout.rstrip("\0").split("\0")
        if path and (root / path).is_file()
    ]


def normalize_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        if closing == -1:
            return target
        return target[1:closing]
    return target.split(maxsplit=1)[0]


def check_local_links(root: Path = PROJECT_ROOT) -> list[str]:
    failures: list[str] = []
    repository = root.resolve()
    for source in tracked_markdown_files(root):
        text = source.read_text(encoding="utf-8")
        for match in INLINE_LINK.finditer(text):
            target = normalize_target(match.group("target"))
            if not target or target.startswith(("#", *EXTERNAL_SCHEMES)):
                continue
            path_text = unquote(target.split("#", 1)[0])
            destination = (source.parent / path_text).resolve()
            try:
                destination.relative_to(repository)
            except ValueError:
                failures.append(
                    f"{source.relative_to(root)}: escapes repository: {target}"
                )
                continue
            if not destination.exists():
                failures.append(f"{source.relative_to(root)}: missing target: {target}")
    return failures


def main() -> None:
    markdown_files = tracked_markdown_files()
    failures = check_local_links()
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Validated local links in {len(markdown_files)} Markdown files.")


if __name__ == "__main__":
    main()
