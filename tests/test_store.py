from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tooluse_bench.domain import Lane
from tooluse_bench.records import (
    BenchmarkMetadata,
    RunManifest,
    RunSpec,
    TaskStatus,
    result_from_spec,
)
from tooluse_bench.store import RunStore, sha256_file


def manifest(directory: Path) -> RunManifest:
    return RunManifest(
        run_id="run-test",
        experiment_id="test",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        git_commit="a" * 40,
        git_dirty=False,
        package_version="0.2.0",
        python_version="3.12.0",
        platform="test",
        configuration_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        catalog_path="config/models.yaml",
        experiment_path="config/experiments/test.yaml",
        output_directory=directory,
        benchmarks=(
            BenchmarkMetadata(
                benchmark_id="fake",
                display_name="Fake",
                version="1",
                source_url="https://example.invalid",
                revision="abc",
                hermetic_default=True,
                supported_profiles=("full",),
            ),
        ),
        selected_deployments=("deployment",),
        lanes=(Lane.STANDARDIZED,),
    )


def test_store_is_append_only_and_finalization_is_verifiable(tmp_path: Path) -> None:
    directory = tmp_path / "run-test"
    store = RunStore.create(directory, manifest(directory))
    spec = RunSpec(
        run_id="run-test",
        experiment_id="test",
        benchmark_id="fake",
        benchmark_version="1",
        profile="full",
        lane=Lane.STANDARDIZED,
        deployment_id="deployment",
        model_alias="model",
        trial=1,
        seed=1,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = result_from_spec(
        spec,
        task_id="task",
        status=TaskStatus.PASS,
        score=1,
        started_at=now,
        finished_at=now,
        latency_seconds=0,
    )
    store.append(result)
    completion = store.finalize()

    assert completion.result_count == 1
    assert completion.status_counts == {TaskStatus.PASS: 1}
    assert completion.results_sha256 == sha256_file(store.results_path)
    assert json.loads((directory / "manifest.json").read_text())["run_id"] == "run-test"
    with pytest.raises(RuntimeError):
        store.append(result)
    with pytest.raises(FileExistsError):
        RunStore.create(directory, manifest(directory))
