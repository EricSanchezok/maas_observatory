"""Export deterministic JSON Schema files from the canonical Pydantic models."""

from __future__ import annotations

import json

from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.domain import ExperimentPlan, ModelCatalog

SCHEMAS = {
    "experiment.schema.json": ExperimentPlan,
    "model-catalog.schema.json": ModelCatalog,
}


def main() -> None:
    output_directory = PROJECT_ROOT / "schemas"
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, model in sorted(SCHEMAS.items()):
        payload = model.model_json_schema()
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        (output_directory / filename).write_text(f"{rendered}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
