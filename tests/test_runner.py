from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tooluse_bench.benchmarks.base import AdapterContext, BenchmarkAdapter
from tooluse_bench.config import DEFAULT_CATALOG, load_catalog
from tooluse_bench.records import (
    BenchmarkMetadata,
    TaskResult,
    TaskStatus,
    result_from_spec,
)
from tooluse_bench.registry import BenchmarkRegistry
from tooluse_bench.runner import run_experiment


class FakeAdapter(BenchmarkAdapter):
    @property
    def metadata(self) -> BenchmarkMetadata:
        return BenchmarkMetadata(
            benchmark_id="fake",
            display_name="Fake benchmark",
            version="1",
            source_url="https://example.invalid",
            revision="fake-revision",
            hermetic_default=True,
            supported_profiles=("full",),
        )

    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        now = datetime.now(UTC)
        yield result_from_spec(
            context.spec,
            task_id="task-1",
            status=TaskStatus.PASS,
            score=1,
            started_at=now,
            finished_at=now,
            latency_seconds=0,
        )


class PartiallyFailingAdapter(FakeAdapter):
    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        yield from super().run(context)
        raise RuntimeError("synthetic adapter failure")


class LifecycleAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.events: list[str] = []

    def prepare(self, context: AdapterContext) -> None:
        del context
        self.events.append("prepare")

    def collect(
        self,
        context: AdapterContext,
        records: Iterable[TaskResult],
    ) -> Iterable[TaskResult]:
        del context
        self.events.append("collect")
        yield from records

    def score(self, context: AdapterContext, record: TaskResult) -> TaskResult:
        del context
        self.events.append("score")
        return record.model_copy(update={"metrics": {"scored": 1.0}})


def test_runner_records_supported_and_unsupported_lanes(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(
        """
schema_version: 1
experiment_id: runner-test
description: Runner test
models: [deepseek-v4-pro]
lanes: [standardized, official-reproduction]
output_root: ignored
benchmarks:
  - benchmark_id: fake
    profile: full
    trials: 2
    options: {}
""".lstrip(),
        encoding="utf-8",
    )
    deployment = load_catalog()[0]
    environment = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "secret",
    }
    with patch.dict(os.environ, environment, clear=False):
        directory = run_experiment(
            experiment_path=experiment,
            catalog_path=DEFAULT_CATALOG,
            output_root=tmp_path / "runs",
            registry=BenchmarkRegistry([FakeAdapter()]),
        )
    records = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text().splitlines()
    ]
    assert len(records) == 4
    assert [record["status"] for record in records].count("pass") == 2
    assert [record["status"] for record in records].count("not_run") == 2
    assert (directory / "completion.json").exists()


def test_runner_preserves_records_emitted_before_adapter_failure(
    tmp_path: Path,
) -> None:
    experiment = tmp_path / "partial.yaml"
    experiment.write_text(
        """
schema_version: 1
experiment_id: partial-test
description: Partial adapter failure test
models: [deepseek-v4-pro]
lanes: [standardized]
output_root: ignored
benchmarks:
  - benchmark_id: fake
    profile: full
    trials: 1
    options: {}
""".lstrip(),
        encoding="utf-8",
    )
    deployment = load_catalog()[0]
    environment = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "secret",
    }
    with patch.dict(os.environ, environment, clear=False):
        directory = run_experiment(
            experiment_path=experiment,
            catalog_path=DEFAULT_CATALOG,
            output_root=tmp_path / "partial-runs",
            registry=BenchmarkRegistry([PartiallyFailingAdapter()]),
        )
    records = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text().splitlines()
    ]
    assert [record["status"] for record in records] == ["pass", "error"]
    assert records[1]["task_id"] == "__benchmark__"
    assert "synthetic adapter failure" in records[1]["error_detail"]


def test_runner_executes_the_complete_adapter_lifecycle(tmp_path: Path) -> None:
    experiment = tmp_path / "lifecycle.yaml"
    experiment.write_text(
        """
schema_version: 1
experiment_id: lifecycle-test
description: Adapter lifecycle test
models: [deepseek-v4-pro]
lanes: [standardized]
output_root: ignored
benchmarks:
  - benchmark_id: fake
    profile: full
    trials: 1
    options: {}
""".lstrip(),
        encoding="utf-8",
    )
    deployment = load_catalog()[0]
    environment = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "secret",
    }
    adapter = LifecycleAdapter()
    with patch.dict(os.environ, environment, clear=False):
        directory = run_experiment(
            experiment_path=experiment,
            catalog_path=DEFAULT_CATALOG,
            output_root=tmp_path / "lifecycle-runs",
            registry=BenchmarkRegistry([adapter]),
        )
    [record] = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text().splitlines()
    ]
    assert adapter.events == ["prepare", "collect", "score"]
    assert record["metrics"] == {"scored": 1.0}
