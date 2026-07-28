from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tooluse_bench.config import load_baselines, load_model_catalog
from tooluse_bench.domain import Lane
from tooluse_bench.records import BenchmarkMetadata, RunManifest
from tooluse_bench.reporting import (
    AggregateResult,
    ReportMetadata,
    TaskGroupAggregate,
)
from tooluse_bench.visualization import (
    FIGURE_FILES,
    FigureMetadata,
    build_benchmark_figure,
)


def manifest(directory: Path) -> RunManifest:
    catalog = load_model_catalog()
    return RunManifest(
        run_id="run-figure-test",
        experiment_id="figure-test",
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        git_commit="a" * 40,
        git_dirty=False,
        package_version="0.3.0",
        python_version="3.13.0",
        platform="test",
        configuration_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        catalog_path="config/models.yaml",
        experiment_path="config/experiments/release-v1.yaml",
        output_directory=directory,
        benchmarks=(
            BenchmarkMetadata(
                benchmark_id="probe",
                display_name="Probe",
                version="1",
                source_url="https://example.com/probe",
                revision="1",
                hermetic_default=True,
                supported_profiles=("full",),
            ),
        ),
        selected_deployments=tuple(
            deployment.deployment_id for deployment in catalog.deployments
        ),
        lanes=(Lane.STANDARDIZED,),
    )


def aggregates() -> list[AggregateResult]:
    output: list[AggregateResult] = []
    for index, deployment in enumerate(load_model_catalog().deployments):
        common = {
            "lane": Lane.STANDARDIZED,
            "deployment_id": deployment.deployment_id,
            "model_alias": deployment.alias,
            "complete": index != 1,
            "not_run_count": 0,
        }
        output.append(
            AggregateResult(
                benchmark_id="probe",
                benchmark_version="1",
                profile="full",
                expected_trials=3,
                task_count=5,
                record_count=15,
                pass_at_1=(index + 1) / 10,
                error_rate=0.1 if index == 0 else 0,
                **common,
            )
        )
        groups = tuple(
            TaskGroupAggregate(
                group_id=subset,
                complete=not (index == 1 and subset == "parallel"),
                expected_trials=1,
                task_count=2,
                record_count=2,
                pass_at_1=((index + column) % 10) / 10,
                error_rate=0.5 if index == 1 and subset == "parallel" else 0,
            )
            for column, subset in enumerate(
                ("simple_python", "parallel", "multi_turn_base", "memory_kv")
            )
        )
        output.append(
            AggregateResult(
                benchmark_id="bfcl-v4",
                benchmark_version="bfcl-test",
                profile="full-public",
                expected_trials=1,
                task_count=8,
                record_count=8,
                pass_at_1=(9 - index) / 10,
                error_rate=0.05 if index == 1 else 0,
                task_groups=groups,
                **common,
            )
        )
        output.append(
            AggregateResult(
                benchmark_id="toolathlon-verified",
                benchmark_version="verified-2026-06-30",
                profile="official",
                expected_trials=3,
                task_count=0 if index == 0 else 108,
                record_count=3 if index == 0 else 324,
                pass_at_1=None if index == 0 else (index + 1) / 10,
                not_run_count=3 if index == 0 else 0,
                lane=Lane.STANDARDIZED,
                deployment_id=deployment.deployment_id,
                model_alias=deployment.alias,
            )
        )
    return output


def report_metadata() -> ReportMetadata:
    return ReportMetadata(
        run_id="run-figure-test",
        run_git_commit="a" * 40,
        source_results_sha256="d" * 64,
        report_builder_git_commit="e" * 40,
        report_builder_git_dirty=False,
        report_builder_package_version="0.3.0",
        baseline_registry_sha256="f" * 64,
    )


def test_benchmark_figure_is_deterministic_and_traceable(tmp_path: Path) -> None:
    metric_rows = aggregates()
    directories = [tmp_path / "one", tmp_path / "two"]
    metadata: list[FigureMetadata] = []
    for directory in directories:
        directory.mkdir()
        (directory / "metrics.json").write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in metric_rows],
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        metadata.append(
            build_benchmark_figure(
                directory,
                manifest(directory),
                metric_rows,
                report_metadata(),
                load_baselines(),
            )
        )

    assert metadata[0].files == metadata[1].files
    assert metadata[0].baseline_registry_sha256 == "f" * 64
    for filename in FIGURE_FILES:
        assert (directories[0] / filename).read_bytes() == (
            directories[1] / filename
        ).read_bytes()
    assert (
        (directories[0] / "benchmark-overview.png")
        .read_bytes()
        .startswith(b"\x89PNG\r\n\x1a\n")
    )
    svg = (directories[0] / "benchmark-overview.svg").read_text(encoding="utf-8")
    assert "official ◇" in svg
    assert "BFCL V4 capability map" in svg
    assert "parallel" in svg


def test_figure_metadata_rejects_invalid_inventory() -> None:
    with pytest.raises(ValidationError, match="inventory"):
        FigureMetadata(
            run_id="run",
            source_metrics_sha256="a" * 64,
            figure_builder_git_commit="b" * 40,
            figure_builder_package_version="0.3.0",
            baseline_registry_sha256="c" * 64,
            files={"only.png": "d" * 64},
        )
