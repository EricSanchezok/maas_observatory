"""Fail when committed JSON schemas differ from the canonical Pydantic models."""

from __future__ import annotations

import tempfile
from pathlib import Path

from export_schemas import export_schemas

from tooluse_bench.config import PROJECT_ROOT


def main() -> None:
    committed = PROJECT_ROOT / "schemas"
    with tempfile.TemporaryDirectory() as directory:
        generated = Path(directory)
        export_schemas(generated)
        expected_files = {path.name for path in committed.glob("*.json")}
        actual_files = {path.name for path in generated.glob("*.json")}
        if expected_files != actual_files:
            raise SystemExit(
                "schema file inventory is stale; run scripts/export_schemas.py"
            )
        stale = [
            filename
            for filename in sorted(expected_files)
            if (committed / filename).read_bytes()
            != (generated / filename).read_bytes()
        ]
        if stale:
            raise SystemExit(
                "stale schemas: " + ", ".join(stale) + "; run scripts/export_schemas.py"
            )
    print("Schemas are current.")


if __name__ == "__main__":
    main()
