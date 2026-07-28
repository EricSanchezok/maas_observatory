"""Neutral release fixture used by publication validator tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tooluse_bench.domain import Lane
from tooluse_bench.publication import build_public_snapshot
from tooluse_bench.records import (
    BenchmarkMetadata,
    ExecutionAudit,
    RunManifest,
    RunSpec,
    TaskStatus,
    result_from_spec,
)
from tooluse_bench.release import build_release
from tooluse_bench.reporting import build_report
from tooluse_bench.store import RunStore

RUN_ID = "publication-fixture"
DEPLOYMENT_ID = "sii-holos-deepseek-v4-pro-w4a8"


def create_populated_public_results(root: Path) -> Path:
    """Build a complete synthetic public snapshot under ``root``."""

    workspace = root.parent / "publication-fixture-work"
    run_directory = workspace / "runs" / RUN_ID
    manifest = RunManifest(
        run_id=RUN_ID,
        experiment_id="publication-fixture",
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        git_commit="a" * 40,
        git_dirty=False,
        package_version="0.3.0",
        python_version="3.13.0",
        platform="test",
        configuration_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        dependency_locks_sha256={"uv.lock": "c" * 64},
        catalog_path="config/models.yaml",
        experiment_path="config/experiments/fixture.yaml",
        output_directory=run_directory,
        benchmarks=(
            BenchmarkMetadata(
                benchmark_id="probe",
                display_name="Protocol probe",
                version="1.0.0",
                source_url="https://example.com/probe",
                revision="fixture",
                hermetic_default=True,
                supported_profiles=("full",),
            ),
        ),
        selected_deployments=(DEPLOYMENT_ID,),
        lanes=(Lane.STANDARDIZED,),
    )
    store = RunStore.create(run_directory, manifest)
    spec = RunSpec(
        run_id=RUN_ID,
        experiment_id="publication-fixture",
        benchmark_id="probe",
        benchmark_version="1.0.0",
        profile="full",
        lane=Lane.STANDARDIZED,
        deployment_id=DEPLOYMENT_ID,
        model_alias="deepseek-v4-pro",
        trial=1,
        seed=1,
    )
    timestamp = datetime(2026, 7, 28, tzinfo=UTC)
    for task_id, status in (
        ("task-pass", TaskStatus.PASS),
        ("task-fail", TaskStatus.FAIL),
    ):
        store.append(
            result_from_spec(
                spec,
                task_id=task_id,
                status=status,
                score=float(status is TaskStatus.PASS),
                started_at=timestamp,
                finished_at=timestamp,
                latency_seconds=1,
            )
        )
    audit = ExecutionAudit(
        run_id=RUN_ID,
        benchmark_id="probe",
        deployment_id=DEPLOYMENT_ID,
        lane=Lane.STANDARDIZED,
        trial=1,
        started_at=timestamp,
        finished_at=timestamp,
        resource_controls={"max_retries": 0},
        observations={"observed_retry_count": 0},
    )
    audit_path = run_directory / "artifacts" / "probe" / "execution-audit.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        json.dumps(audit.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store.finalize()
    build_report(run_directory)
    release_directory, archive = build_release(
        run_directory,
        output_root=workspace / "release",
    )
    return build_public_snapshot(
        release_directory,
        archive,
        title="Publication fixture",
        root=root,
    )
