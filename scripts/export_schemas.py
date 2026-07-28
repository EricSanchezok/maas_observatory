"""Export deterministic JSON Schema files from the canonical Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path

from tooluse_bench.baselines import BaselineRegistry
from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.domain import ExperimentPlan, ModelCatalog
from tooluse_bench.publication import PublicResultIndex, PublicSnapshotMetadata
from tooluse_bench.records import ExecutionAudit, RunCompletion, RunManifest, TaskResult
from tooluse_bench.release import ReleaseMetadata
from tooluse_bench.reporting import AggregateResult

SCHEMAS = {
    "baseline-registry.schema.json": BaselineRegistry,
    "aggregate-result.schema.json": AggregateResult,
    "experiment.schema.json": ExperimentPlan,
    "execution-audit.schema.json": ExecutionAudit,
    "model-catalog.schema.json": ModelCatalog,
    "public-result-index.schema.json": PublicResultIndex,
    "public-snapshot-metadata.schema.json": PublicSnapshotMetadata,
    "run-completion.schema.json": RunCompletion,
    "run-manifest.schema.json": RunManifest,
    "release-metadata.schema.json": ReleaseMetadata,
    "task-result.schema.json": TaskResult,
}


def export_schemas(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, model in sorted(SCHEMAS.items()):
        payload = model.model_json_schema()
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        (output_directory / filename).write_text(f"{rendered}\n", encoding="utf-8")


def main() -> None:
    export_schemas(PROJECT_ROOT / "schemas")


if __name__ == "__main__":
    main()
