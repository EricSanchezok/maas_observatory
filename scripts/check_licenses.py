"""Enforce the repository's code, data, and third-party license declarations."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.publication import validate_public_results


def require_phrases(path: Path, phrases: tuple[str, ...]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        f"{path.name}: missing {phrase!r}" for phrase in phrases if phrase not in text
    ]


def check_licenses(root: Path = PROJECT_ROOT) -> list[str]:
    failures: list[str] = []
    failures.extend(
        require_phrases(
            root / "LICENSE",
            ("Apache License", "Version 2.0, January 2004"),
        )
    )
    failures.extend(
        require_phrases(
            root / "LICENSE-DATA",
            ("Creative Commons Attribution 4.0 International", "CC BY 4.0"),
        )
    )
    failures.extend(
        require_phrases(
            root / "THIRD_PARTY_NOTICES.md",
            (
                "benchmark-envs/bfcl/uv.lock",
                "benchmark-envs/toolathlon/uv.lock",
                "not relicensed by this repository",
            ),
        )
    )

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("license") != "Apache-2.0":
        failures.append("pyproject.toml: project.license must be Apache-2.0")

    try:
        index, _ = validate_public_results(root / "public-results")
    except (OSError, ValueError) as exc:
        failures.append(f"public-results: cannot validate license metadata: {exc}")
    else:
        for reference in index.snapshots:
            release_path = (
                root / "public-results" / reference.path / "release-metadata.json"
            )
            release = json.loads(release_path.read_text(encoding="utf-8"))
            if release.get("data_license") != "CC-BY-4.0":
                failures.append(f"{reference.run_id}: data license must be CC-BY-4.0")
    return failures


def main() -> None:
    failures = check_licenses()
    if failures:
        raise SystemExit("\n".join(failures))
    print("Validated Apache-2.0 code and CC-BY-4.0 result declarations.")


if __name__ == "__main__":
    main()
