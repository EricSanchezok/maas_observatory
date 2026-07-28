"""Experiment orchestration that records every model, lane, trial, and failure."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tooluse_bench.benchmarks.base import AdapterContext, BenchmarkAdapter
from tooluse_bench.config import (
    DEFAULT_CATALOG,
    DEFAULT_EXPERIMENT,
    PROJECT_ROOT,
    load_experiment,
    load_model_catalog,
)
from tooluse_bench.domain import ExperimentPlan, Lane, ModelCatalog, ModelDeployment
from tooluse_bench.provenance import create_manifest
from tooluse_bench.records import (
    ErrorCategory,
    RunSpec,
    TaskResult,
    TaskStatus,
    result_from_spec,
)
from tooluse_bench.registry import BenchmarkRegistry
from tooluse_bench.store import RunStore
from tooluse_bench.transport import OpenAITransport


def deterministic_seed(
    experiment_id: str,
    benchmark_id: str,
    deployment_id: str,
    lane: Lane,
    trial: int,
) -> int:
    value = ":".join(
        (experiment_id, benchmark_id, deployment_id, lane.value, str(trial))
    )
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:4], "big")


def select_deployments(
    experiment: ExperimentPlan, catalog: ModelCatalog
) -> list[ModelDeployment]:
    if experiment.models == ["*"]:
        return list(catalog.deployments)
    by_alias = {item.alias: item for item in catalog.deployments}
    unknown = sorted(set(experiment.models) - set(by_alias))
    if unknown:
        raise ValueError(f"experiment references unknown models: {', '.join(unknown)}")
    return [by_alias[alias] for alias in experiment.models]


def _safe_detail(error: BaseException, deployment: ModelDeployment) -> str:
    detail = f"{type(error).__name__}: {error}"
    for value in (deployment.api_key, deployment.base_url):
        if value:
            detail = detail.replace(value, "[REDACTED]")
    return detail[:4000]


def _benchmark_result(
    spec: RunSpec,
    *,
    status: TaskStatus,
    category: ErrorCategory,
    detail: str,
) -> TaskResult:
    now = datetime.now(UTC)
    return result_from_spec(
        spec,
        task_id="__benchmark__",
        status=status,
        started_at=now,
        finished_at=now,
        latency_seconds=0,
        attempts=0,
        error_category=category,
        error_detail=detail,
    )


def _execute_adapter(
    adapter: BenchmarkAdapter,
    spec: RunSpec,
    deployment: ModelDeployment,
    selection: object,
    workspace: Path,
    append_result: Callable[[TaskResult], None],
) -> int:
    from tooluse_bench.domain import BenchmarkSelection

    if not isinstance(selection, BenchmarkSelection):
        raise TypeError("selection must be BenchmarkSelection")
    transport: OpenAITransport | None = None
    try:
        if adapter.needs_native_transport():
            transport = OpenAITransport(
                deployment,
                timeout_seconds=(
                    float(selection.options["transport_timeout_seconds"])
                    if "transport_timeout_seconds" in selection.options
                    else None
                ),
                wall_timeout_seconds=(
                    float(selection.options["transport_wall_timeout_seconds"])
                    if "transport_wall_timeout_seconds" in selection.options
                    else None
                ),
                max_retries=int(selection.options.get("transport_max_retries", 2)),
            )
        context = AdapterContext(
            spec=spec,
            deployment=deployment,
            selection=selection,
            workspace=workspace,
            transport=transport,
        )
        result_count = 0
        for result in adapter.run(context):
            append_result(result)
            result_count += 1
        return result_count
    finally:
        if transport is not None:
            transport.close()


def run_experiment(
    *,
    experiment_path: Path = DEFAULT_EXPERIMENT,
    catalog_path: Path = DEFAULT_CATALOG,
    output_root: Path | None = None,
    registry: BenchmarkRegistry | None = None,
) -> Path:
    experiment = load_experiment(experiment_path)
    catalog = load_model_catalog(catalog_path)
    deployments = select_deployments(experiment, catalog)
    benchmark_registry = registry or BenchmarkRegistry.discover()
    adapters = [
        benchmark_registry.get(selection.benchmark_id)
        for selection in experiment.benchmarks
    ]
    destination = output_root or PROJECT_ROOT / experiment.output_root
    manifest = create_manifest(
        experiment=experiment,
        experiment_path=experiment_path,
        catalog_path=catalog_path,
        deployments=deployments,
        benchmarks=[adapter.metadata for adapter in adapters],
        output_root=destination,
    )
    store = RunStore.create(destination / manifest.run_id, manifest)

    for selection, adapter in zip(experiment.benchmarks, adapters, strict=True):
        for deployment in deployments:
            issues = adapter.validate(selection, deployment)
            validation_errors = [
                issue.message for issue in issues if issue.level == "error"
            ]
            for lane in experiment.lanes:
                for trial in range(1, selection.trials + 1):
                    spec = RunSpec(
                        run_id=manifest.run_id,
                        experiment_id=experiment.experiment_id,
                        benchmark_id=adapter.metadata.benchmark_id,
                        benchmark_version=adapter.metadata.version,
                        profile=selection.profile,
                        lane=lane,
                        deployment_id=deployment.deployment_id,
                        model_alias=deployment.alias,
                        trial=trial,
                        seed=deterministic_seed(
                            experiment.experiment_id,
                            adapter.metadata.benchmark_id,
                            deployment.deployment_id,
                            lane,
                            trial,
                        ),
                        options=selection.options,
                    )
                    if validation_errors:
                        store.append(
                            _benchmark_result(
                                spec,
                                status=TaskStatus.ERROR,
                                category=ErrorCategory.INFRASTRUCTURE,
                                detail="; ".join(validation_errors),
                            )
                        )
                        continue
                    supported, reason = adapter.supports_lane(
                        lane, selection, deployment
                    )
                    if not supported:
                        store.append(
                            _benchmark_result(
                                spec,
                                status=TaskStatus.NOT_RUN,
                                category=ErrorCategory.INSUFFICIENT_PROTOCOL,
                                detail=reason or "lane is not supported",
                            )
                        )
                        continue
                    configuration_errors = deployment.configuration_errors()
                    if configuration_errors:
                        store.append(
                            _benchmark_result(
                                spec,
                                status=TaskStatus.ERROR,
                                category=ErrorCategory.INFRASTRUCTURE,
                                detail="; ".join(configuration_errors),
                            )
                        )
                        continue

                    workspace = (
                        store.directory
                        / "artifacts"
                        / adapter.metadata.benchmark_id
                        / deployment.deployment_id
                        / lane.value
                        / f"trial-{trial}"
                    )
                    workspace.mkdir(parents=True, exist_ok=False)
                    try:
                        result_count = _execute_adapter(
                            adapter,
                            spec,
                            deployment,
                            selection,
                            workspace,
                            store.append,
                        )
                        if result_count == 0:
                            store.append(
                                _benchmark_result(
                                    spec,
                                    status=TaskStatus.ERROR,
                                    category=ErrorCategory.INFRASTRUCTURE,
                                    detail="adapter returned no task records",
                                )
                            )
                    except Exception as exc:
                        store.append(
                            _benchmark_result(
                                spec,
                                status=TaskStatus.ERROR,
                                category=ErrorCategory.INFRASTRUCTURE,
                                detail=_safe_detail(exc, deployment),
                            )
                        )

    store.finalize()
    return store.directory
