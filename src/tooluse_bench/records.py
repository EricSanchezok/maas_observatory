"""Versioned run and result records shared by every benchmark adapter."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from tooluse_bench.domain import Lane, StrictModel


class TaskStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    NOT_RUN = "not_run"


class ErrorCategory(StrEnum):
    NONE = "none"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    SELECTION = "selection"
    ARGUMENTS = "arguments"
    PLANNING = "planning"
    TOOL_RESULT_INTEGRATION = "tool_result_integration"
    POLICY = "policy"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    INSUFFICIENT_PROTOCOL = "insufficient_protocol"


class BenchmarkMetadata(StrictModel):
    benchmark_id: str
    display_name: str
    version: str
    source_url: str
    revision: str
    hermetic_default: bool
    supported_profiles: tuple[str, ...]


class ValidationIssue(StrictModel):
    level: Literal["warning", "error"]
    code: str
    message: str


class RunSpec(StrictModel):
    run_id: str
    experiment_id: str
    benchmark_id: str
    benchmark_version: str
    profile: str
    lane: Lane
    deployment_id: str
    model_alias: str
    trial: int = Field(ge=1)
    seed: int = Field(ge=0)
    options: dict[str, Any] = Field(default_factory=dict)


class TaskResult(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    experiment_id: str
    benchmark_id: str
    benchmark_version: str
    profile: str
    lane: Lane
    deployment_id: str
    model_alias: str
    task_id: str
    trial: int = Field(ge=1)
    seed: int = Field(ge=0)
    status: TaskStatus
    score: float | None = Field(default=None, ge=0, le=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    latency_seconds: float = Field(ge=0)
    attempts: int = Field(default=1, ge=0)
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    error_category: ErrorCategory = ErrorCategory.NONE
    error_detail: str | None = None
    artifact_paths: tuple[str, ...] = ()
    started_at: datetime
    finished_at: datetime


class RunManifest(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    experiment_id: str
    created_at: datetime
    git_commit: str
    git_dirty: bool
    package_version: str
    python_version: str
    platform: str
    configuration_sha256: str
    dependency_lock_sha256: str
    catalog_path: str
    experiment_path: str
    output_directory: Path
    benchmarks: tuple[BenchmarkMetadata, ...]
    selected_deployments: tuple[str, ...]
    lanes: tuple[Lane, ...]


class RunCompletion(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    finished_at: datetime
    result_count: int = Field(ge=0)
    status_counts: dict[TaskStatus, int]
    results_sha256: str
    complete: Literal[True] = True


def result_from_spec(
    spec: RunSpec,
    *,
    task_id: str,
    status: TaskStatus,
    started_at: datetime,
    finished_at: datetime,
    latency_seconds: float,
    score: float | None = None,
    metrics: dict[str, float] | None = None,
    attempts: int = 1,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    error_category: ErrorCategory = ErrorCategory.NONE,
    error_detail: str | None = None,
    artifact_paths: tuple[str, ...] = (),
) -> TaskResult:
    """Create a task record without duplicating run identity fields."""

    return TaskResult(
        run_id=spec.run_id,
        experiment_id=spec.experiment_id,
        benchmark_id=spec.benchmark_id,
        benchmark_version=spec.benchmark_version,
        profile=spec.profile,
        lane=spec.lane,
        deployment_id=spec.deployment_id,
        model_alias=spec.model_alias,
        task_id=task_id,
        trial=spec.trial,
        seed=spec.seed,
        status=status,
        score=score,
        metrics=metrics or {},
        latency_seconds=latency_seconds,
        attempts=attempts,
        request=request,
        response=response,
        usage=usage,
        error_category=error_category,
        error_detail=error_detail,
        artifact_paths=artifact_paths,
        started_at=started_at,
        finished_at=finished_at,
    )
