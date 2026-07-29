"""Deterministic statistics and public report rendering."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import Field

from tooluse_bench import __version__
from tooluse_bench.baselines import BaselineRegistry, Comparability
from tooluse_bench.benchmarks.bfcl import bfcl_failure_category
from tooluse_bench.config import load_baselines
from tooluse_bench.domain import Lane, StrictModel
from tooluse_bench.provenance import git_state
from tooluse_bench.records import (
    ErrorCategory,
    RunCompletion,
    RunManifest,
    TaskResult,
    TaskStatus,
)
from tooluse_bench.store import canonical_json, sha256_file
from tooluse_bench.visualization import build_benchmark_figure


class ReportMetadata(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    run_git_commit: str
    source_results_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_builder_git_commit: str
    report_builder_git_dirty: bool
    report_builder_package_version: str
    baseline_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TaskGroupAggregate(StrictModel):
    schema_version: Literal[1] = 1
    group_id: str
    complete: bool = True
    expected_trials: int = Field(ge=1)
    task_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    pass_at_1: float | None = Field(default=None, ge=0, le=1)
    pass_at_3: float | None = Field(default=None, ge=0, le=1)
    pass_pow_3: float | None = Field(default=None, ge=0, le=1)
    pass_at_1_ci_low: float | None = Field(default=None, ge=0, le=1)
    pass_at_1_ci_high: float | None = Field(default=None, ge=0, le=1)
    error_rate: float | None = Field(default=None, ge=0, le=1)
    status_counts: dict[str, int] = Field(default_factory=dict)
    error_categories: dict[str, int] = Field(default_factory=dict)


class AggregateResult(StrictModel):
    schema_version: Literal[1] = 1
    benchmark_id: str
    benchmark_version: str
    profile: str
    lane: Lane
    deployment_id: str
    model_alias: str
    complete: bool = True
    expected_trials: int = Field(ge=1)
    task_count: int = Field(ge=0)
    record_count: int = Field(ge=0)
    pass_at_1: float | None = Field(default=None, ge=0, le=1)
    pass_at_3: float | None = Field(default=None, ge=0, le=1)
    pass_pow_3: float | None = Field(default=None, ge=0, le=1)
    pass_at_1_ci_low: float | None = Field(default=None, ge=0, le=1)
    pass_at_1_ci_high: float | None = Field(default=None, ge=0, le=1)
    mean_score: float | None = Field(default=None, ge=0, le=1)
    error_rate: float | None = Field(default=None, ge=0, le=1)
    not_run_count: int = Field(ge=0)
    latency_p50_seconds: float | None = Field(default=None, ge=0)
    latency_p95_seconds: float | None = Field(default=None, ge=0)
    mean_turns: float | None = Field(default=None, ge=0)
    mean_tool_calls: float | None = Field(default=None, ge=0)
    mean_total_tokens: float | None = Field(default=None, ge=0)
    error_categories: dict[str, int] = Field(default_factory=dict)
    exact_baseline_id: str | None = None
    exact_baseline_score: float | None = None
    official_delta: float | None = None
    task_groups: tuple[TaskGroupAggregate, ...] = ()


def load_results(path: Path) -> list[TaskResult]:
    results: list[TaskResult] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            results.append(TaskResult.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid TaskResult") from exc
    return results


def load_completed_run(
    run_directory: Path,
) -> tuple[RunManifest, RunCompletion, list[TaskResult]]:
    manifest_path = run_directory / "manifest.json"
    completion_path = run_directory / "completion.json"
    results_path = run_directory / "results.jsonl"
    missing = [
        str(path)
        for path in (manifest_path, completion_path, results_path)
        if not path.is_file()
    ]
    if missing:
        raise ValueError(f"run is incomplete; missing: {', '.join(missing)}")

    manifest = RunManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    completion = RunCompletion.model_validate_json(
        completion_path.read_text(encoding="utf-8")
    )
    results = load_results(results_path)
    if manifest.run_id != completion.run_id:
        raise ValueError("manifest and completion run IDs do not match")
    if any(result.run_id != manifest.run_id for result in results):
        raise ValueError("one or more result run IDs do not match the manifest")
    if completion.results_sha256 != sha256_file(results_path):
        raise ValueError("results checksum does not match completion record")
    if completion.result_count != len(results):
        raise ValueError("result count does not match completion record")
    status_counts = Counter(result.status for result in results)
    if dict(status_counts) != completion.status_counts:
        raise ValueError("status counts do not match completion record")
    return manifest, completion, results


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bootstrap_ci(
    task_successes: list[list[float]],
    *,
    seed_material: str,
    iterations: int = 2000,
) -> tuple[float | None, float | None]:
    if not task_successes:
        return None, None
    seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
    generator = random.Random(seed)
    task_count = len(task_successes)
    samples: list[float] = []
    task_means = [mean(values) for values in task_successes]
    for _ in range(iterations):
        samples.append(
            mean(task_means[generator.randrange(task_count)] for _ in range(task_count))
        )
    return _percentile(samples, 0.025), _percentile(samples, 0.975)


def _numeric_values(results: list[TaskResult], key: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result.metrics.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _token_values(results: list[TaskResult]) -> list[float]:
    values: list[float] = []
    for result in results:
        if not result.usage:
            continue
        value = result.usage.get("total_tokens")
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _reported_error_category(result: TaskResult) -> ErrorCategory:
    if result.error_category is not ErrorCategory.NONE:
        return result.error_category
    if result.benchmark_id == "bfcl-v4" and result.status is TaskStatus.FAIL:
        return bfcl_failure_category(result.task_id)
    return ErrorCategory.NONE


def _exact_baseline(
    baselines: BaselineRegistry,
    *,
    benchmark_id: str,
    benchmark_version: str,
    deployment_id: str,
    lane: Lane,
    configuration_sha256: str | None,
) -> tuple[str, float] | None:
    if lane is not Lane.OFFICIAL_REPRODUCTION or configuration_sha256 is None:
        return None
    matches = [
        item
        for item in baselines.baselines
        if item.comparability is Comparability.EXACT
        and item.benchmark_id == benchmark_id
        and item.benchmark_release == benchmark_version
        and item.metric == "pass_at_1"
        and deployment_id in item.compatible_deployments
        and configuration_sha256 in item.compatible_configurations_sha256
    ]
    if len(matches) > 1:
        raise ValueError(
            f"multiple exact baselines match {benchmark_id}/{deployment_id}"
        )
    if not matches:
        return None
    return matches[0].baseline_id, matches[0].score / 100


def _task_statistics(
    records: list[TaskResult],
    *,
    expected_trials: int,
    seed_material: str,
) -> tuple[
    int,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    task_records: dict[str, list[TaskResult]] = defaultdict(list)
    for record in records:
        if record.status is not TaskStatus.NOT_RUN and not record.task_id.startswith(
            "__"
        ):
            task_records[record.task_id].append(record)
    successes = [
        [float(item.status is TaskStatus.PASS) for item in task_records[task_id]]
        for task_id in sorted(task_records)
    ]
    pass_at_1 = (
        mean(value for task in successes for value in task) if successes else None
    )
    pass_at_3 = (
        mean(float(any(task)) for task in successes)
        if successes and expected_trials == 3
        else None
    )
    pass_pow_3 = (
        mean(float(len(task) == expected_trials and all(task)) for task in successes)
        if successes and expected_trials == 3
        else None
    )
    ci_low, ci_high = _bootstrap_ci(
        successes,
        seed_material=seed_material,
    )
    return (
        len(task_records),
        pass_at_1,
        pass_at_3,
        pass_pow_3,
        ci_low,
        ci_high,
    )


def _task_group_aggregates(
    records: list[TaskResult],
    *,
    expected_trials: int,
    seed_material: str,
) -> tuple[TaskGroupAggregate, ...]:
    task_groups: dict[str, list[TaskResult]] = defaultdict(list)
    subset_failures: set[str] = set()
    for record in records:
        if record.task_id.startswith("__subset__/"):
            subset_failures.add(record.task_id.split("/", 1)[1])
            continue
        if record.task_id.startswith("__") or "/" not in record.task_id:
            continue
        task_groups[record.task_id.split("/", 1)[0]].append(record)

    output: list[TaskGroupAggregate] = []
    for group_id in sorted(set(task_groups) | subset_failures):
        group_records = task_groups.get(group_id, [])
        (
            task_count,
            pass_at_1,
            pass_at_3,
            pass_pow_3,
            ci_low,
            ci_high,
        ) = _task_statistics(
            group_records,
            expected_trials=expected_trials,
            seed_material=f"{seed_material}:{group_id}",
        )
        evaluated = [
            item for item in group_records if item.status is not TaskStatus.NOT_RUN
        ]
        status_counts = Counter(item.status.value for item in group_records)
        if group_id in subset_failures:
            status_counts[TaskStatus.ERROR.value] += 1
        error_categories = Counter(
            _reported_error_category(item).value
            for item in group_records
            if _reported_error_category(item) is not ErrorCategory.NONE
        )
        if group_id in subset_failures:
            error_categories[ErrorCategory.INFRASTRUCTURE.value] += 1
        denominator = len(evaluated) + int(group_id in subset_failures)
        error_count = sum(item.status is TaskStatus.ERROR for item in evaluated) + int(
            group_id in subset_failures
        )
        output.append(
            TaskGroupAggregate(
                group_id=group_id,
                complete=group_id not in subset_failures,
                expected_trials=expected_trials,
                task_count=task_count,
                record_count=denominator,
                pass_at_1=pass_at_1,
                pass_at_3=pass_at_3,
                pass_pow_3=pass_pow_3,
                pass_at_1_ci_low=ci_low,
                pass_at_1_ci_high=ci_high,
                error_rate=error_count / denominator if denominator else None,
                status_counts=dict(sorted(status_counts.items())),
                error_categories=dict(sorted(error_categories.items())),
            )
        )
    return tuple(output)


def aggregate_results(
    results: list[TaskResult],
    *,
    baselines: BaselineRegistry | None = None,
    configuration_sha256: str | None = None,
) -> list[AggregateResult]:
    registry = baselines or load_baselines()
    grouped: dict[tuple[str, str, str, str, str, str], list[TaskResult]] = defaultdict(
        list
    )
    for result in results:
        key = (
            result.benchmark_id,
            result.benchmark_version,
            result.profile,
            result.lane.value,
            result.deployment_id,
            result.model_alias,
        )
        grouped[key].append(result)

    aggregates: list[AggregateResult] = []
    for key in sorted(grouped):
        records = grouped[key]
        (
            benchmark_id,
            benchmark_version,
            profile,
            lane_value,
            deployment_id,
            model_alias,
        ) = key
        expected_trials = max(item.trial for item in records)
        (
            task_count,
            pass_at_1,
            pass_at_3,
            pass_pow_3,
            ci_low,
            ci_high,
        ) = _task_statistics(
            records,
            expected_trials=expected_trials,
            seed_material=":".join(key),
        )
        evaluated = [item for item in records if item.status is not TaskStatus.NOT_RUN]
        error_rate = (
            mean(float(item.status is TaskStatus.ERROR) for item in evaluated)
            if evaluated
            else None
        )
        scores = [item.score for item in evaluated if item.score is not None]
        latencies = [
            item.latency_seconds
            for item in evaluated
            if (
                item.attempts > 0
                and item.task_id != "__benchmark__"
                and item.latency_seconds is not None
            )
        ]
        turns = _numeric_values(evaluated, "turns")
        tool_calls = _numeric_values(evaluated, "tool_calls")
        total_tokens = _token_values(evaluated)
        categories = Counter(
            _reported_error_category(item).value
            for item in records
            if _reported_error_category(item) is not ErrorCategory.NONE
        )
        baseline = _exact_baseline(
            registry,
            benchmark_id=benchmark_id,
            benchmark_version=benchmark_version,
            deployment_id=deployment_id,
            lane=Lane(lane_value),
            configuration_sha256=configuration_sha256,
        )
        exact_baseline_id = baseline[0] if baseline else None
        exact_baseline_score = baseline[1] if baseline else None
        aggregates.append(
            AggregateResult(
                benchmark_id=benchmark_id,
                benchmark_version=benchmark_version,
                profile=profile,
                lane=Lane(lane_value),
                deployment_id=deployment_id,
                model_alias=model_alias,
                complete=not any(
                    item.task_id.startswith("__")
                    and item.status in {TaskStatus.ERROR, TaskStatus.NOT_RUN}
                    for item in records
                ),
                expected_trials=expected_trials,
                task_count=task_count,
                record_count=len(records),
                pass_at_1=pass_at_1,
                pass_at_3=pass_at_3,
                pass_pow_3=pass_pow_3,
                pass_at_1_ci_low=ci_low,
                pass_at_1_ci_high=ci_high,
                mean_score=mean(scores) if scores else None,
                error_rate=error_rate,
                not_run_count=sum(
                    item.status is TaskStatus.NOT_RUN for item in records
                ),
                latency_p50_seconds=_percentile(latencies, 0.5),
                latency_p95_seconds=_percentile(latencies, 0.95),
                mean_turns=mean(turns) if turns else None,
                mean_tool_calls=mean(tool_calls) if tool_calls else None,
                mean_total_tokens=mean(total_tokens) if total_tokens else None,
                error_categories=dict(sorted(categories.items())),
                exact_baseline_id=exact_baseline_id,
                exact_baseline_score=exact_baseline_score,
                official_delta=(
                    pass_at_1 - exact_baseline_score
                    if pass_at_1 is not None and exact_baseline_score is not None
                    else None
                ),
                task_groups=(
                    _task_group_aggregates(
                        records,
                        expected_trials=expected_trials,
                        seed_material=":".join(key),
                    )
                    if benchmark_id == "bfcl-v4"
                    else ()
                ),
            )
        )
    return aggregates


def _display_percentage(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_markdown_report(
    manifest: RunManifest,
    aggregates: list[AggregateResult],
    baselines: BaselineRegistry,
    metadata: ReportMetadata,
) -> str:
    lines = [
        f"# MaaS Tool-use Evaluation — `{manifest.run_id}`",
        "",
        "## Provenance",
        "",
        f"- Git commit: `{manifest.git_commit}`",
        f"- Report builder commit: `{metadata.report_builder_git_commit}`",
        "- Report builder working tree dirty: "
        f"`{str(metadata.report_builder_git_dirty).lower()}`",
        f"- Baseline registry SHA-256: `{metadata.baseline_registry_sha256}`",
        f"- Working tree dirty at launch: `{str(manifest.git_dirty).lower()}`",
        f"- Created at: `{manifest.created_at.isoformat()}`",
        f"- Configuration SHA-256: `{manifest.configuration_sha256}`",
        f"- Dependency lock SHA-256: `{manifest.dependency_lock_sha256}`",
        *[
            f"- Dependency lock `{path}`: `{digest}`"
            for path, digest in sorted(manifest.dependency_locks_sha256.items())
        ],
        "",
        "## Results",
        "",
        "| Benchmark | Lane | Deployment | Tasks | Pass@1 | 95% CI "
        "| Pass@3 | Pass^3 | Error rate | Coverage |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in aggregates:
        ci = (
            "—"
            if item.pass_at_1_ci_low is None or item.pass_at_1_ci_high is None
            else (
                f"{item.pass_at_1_ci_low * 100:.1f}-{item.pass_at_1_ci_high * 100:.1f}%"
            )
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    item.benchmark_id,
                    item.lane.value,
                    item.model_alias,
                    str(item.task_count),
                    _display_percentage(item.pass_at_1),
                    ci,
                    _display_percentage(item.pass_at_3),
                    _display_percentage(item.pass_pow_3),
                    _display_percentage(item.error_rate),
                    "complete" if item.complete else "partial / not run",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Pass@1 is the observed task-level result. `partial / not run` means "
            "one or more benchmark/subset sentinels reported missing evidence; "
            "the observed score must not be interpreted as complete coverage.",
            "",
            "No cross-benchmark composite score is calculated. A missing value "
            "means the lane did not produce comparable task-level observations.",
            "",
            "## BFCL subset results",
            "",
            "| Deployment | Subset | Tasks | Pass@1 | 95% CI | Error rate | Coverage |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for item in aggregates:
        for group in item.task_groups:
            ci = (
                "—"
                if group.pass_at_1_ci_low is None or group.pass_at_1_ci_high is None
                else (
                    f"{group.pass_at_1_ci_low * 100:.1f}-"
                    f"{group.pass_at_1_ci_high * 100:.1f}%"
                )
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        item.model_alias,
                        group.group_id,
                        str(group.task_count),
                        _display_percentage(group.pass_at_1),
                        ci,
                        _display_percentage(group.error_rate),
                        "complete" if group.complete else "partial",
                    )
                )
                + " |"
            )
    if not any(item.task_groups for item in aggregates):
        lines.append("| — | — | 0 | — | — | — | not evaluated |")
    lines.extend(
        [
            "",
            "## Official and published context",
            "",
        ]
    )
    evaluated_benchmarks = {item.benchmark_id for item in aggregates}
    relevant_baselines = [
        item
        for item in baselines.baselines
        if item.benchmark_id in evaluated_benchmarks
    ]
    if relevant_baselines:
        lines.extend(
            [
                "Only baselines marked `exact` may produce an official delta. "
                "The entries below are contextual unless their precision, benchmark "
                "release, harness, and reasoning configuration match the evaluated "
                "deployment.",
                "",
                "| Upstream model | Benchmark release | Metric | Score "
                "| Comparability | Source |",
                "|---|---|---|---:|---|---|",
            ]
        )
        for baseline_item in sorted(
            relevant_baselines, key=lambda value: value.baseline_id
        ):
            lines.append(
                "| "
                + " | ".join(
                    (
                        baseline_item.upstream_model,
                        (
                            f"{baseline_item.benchmark_id} / "
                            f"{baseline_item.benchmark_release}"
                        ),
                        baseline_item.metric,
                        f"{baseline_item.score:.1f}%",
                        baseline_item.comparability.value,
                        f"[source]({baseline_item.source_url})",
                    )
                )
                + " |"
            )
    else:
        lines.append(
            "No published baseline is registered for the evaluated benchmark release."
        )
    lines.extend(
        [
            "",
            "## Interpretation constraints",
            "",
            "- Transport and infrastructure errors are retained; they are never "
            "dropped.",
            "- Quantized local deployments are not assumed equivalent to "
            "upstream releases.",
            "- Toolathlon-Verified and pre-Verified Toolathlon are separate "
            "score series.",
            "- MCPMark, MCP-Atlas, MM-Claw, and Claw-Eval are distinct benchmarks.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    run_directory: Path,
    *,
    baselines: BaselineRegistry | None = None,
) -> Path:
    manifest, completion, results = load_completed_run(run_directory)
    registry = baselines or load_baselines()
    builder_commit, builder_dirty = git_state()
    baseline_registry_sha256 = hashlib.sha256(
        canonical_json(registry.model_dump(mode="json")).encode()
    ).hexdigest()
    metadata = ReportMetadata(
        run_id=manifest.run_id,
        run_git_commit=manifest.git_commit,
        source_results_sha256=completion.results_sha256,
        report_builder_git_commit=builder_commit,
        report_builder_git_dirty=builder_dirty,
        report_builder_package_version=__version__,
        baseline_registry_sha256=baseline_registry_sha256,
    )
    aggregates = aggregate_results(
        results,
        baselines=registry,
        configuration_sha256=manifest.configuration_sha256,
    )
    report_directory = run_directory / "report"
    report_directory.mkdir(exist_ok=False)

    metrics_payload = [item.model_dump(mode="json") for item in aggregates]
    (report_directory / "metrics.json").write_text(
        json.dumps(metrics_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_directory / "report-metadata.json").write_text(
        json.dumps(
            metadata.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    build_benchmark_figure(
        report_directory,
        manifest,
        aggregates,
        metadata,
        registry,
    )
    fieldnames = list(AggregateResult.model_fields)
    with (report_directory / "metrics.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in aggregates:
            row = item.model_dump(mode="json")
            row["error_categories"] = json.dumps(
                row["error_categories"], sort_keys=True
            )
            row["task_groups"] = json.dumps(row["task_groups"], sort_keys=True)
            writer.writerow(row)
    (report_directory / "report.md").write_text(
        render_markdown_report(manifest, aggregates, registry, metadata),
        encoding="utf-8",
    )
    return report_directory
