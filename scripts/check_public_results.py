"""Validate every lightweight result snapshot consumed by GitHub Pages."""

from __future__ import annotations

from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.publication import (
    render_public_results_markdown,
    validate_public_results,
)


def main() -> None:
    index, snapshots = validate_public_results()
    print(
        f"Validated {len(snapshots)} public snapshot(s); latest={index.latest_run_id}."
    )
    expected = render_public_results_markdown()
    results_page = PROJECT_ROOT / "docs" / "results.md"
    if results_page.read_text(encoding="utf-8") != expected:
        raise SystemExit(
            "docs/results.md is stale relative to validated public snapshots"
        )
    print("Public results page is current.")


if __name__ == "__main__":
    main()
