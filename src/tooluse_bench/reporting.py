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

from tooluse_bench.baselines import BaselineRegistry, Comparability
from tooluse_bench.config import load_baselines
from tooluse_bench.domain import Lane, StrictModel
from tooluse_bench.records import (
    RunCompletion,
    RunManifest,
    TaskResult,
    TaskStatus,
)
from tooluse_bench.store import sha256_file


class AggregateResult(StrictModel):
    schema_version: Literal[1] = 1
    benchmark_id: str
    benchmark_version: str
    profile: str
    lane: Lane
    deployment_id: str
    model_alias: str
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


def _exact_baseline(
    baselines: BaselineRegistry,
    *,
    benchmark_id: str,
    benchmark_version: str,
    deployment_id: str,
) -> tuple[str, float] | None:
    matches = [
        item
        for item in baselines.baselines
        if item.comparability is Comparability.EXACT
        and item.benchmark_id == benchmark_id
        and item.benchmark_release == benchmark_version
        and item.metric == "pass_at_1"
        and deployment_id in item.compatible_deployments
    ]
    if len(matches) > 1:
        raise ValueError(
            f"multiple exact baselines match {benchmark_id}/{deployment_id}"
        )
    if not matches:
        return None
    return matches[0].baseline_id, matches[0].score / 100


def aggregate_results(
    results: list[TaskResult],
    *,
    baselines: BaselineRegistry | None = None,
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
        task_records: dict[str, list[TaskResult]] = defaultdict(list)
        for record in records:
            if record.task_id != "__benchmark__" and (
                record.status is not TaskStatus.NOT_RUN
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
            mean(
                float(len(task) == expected_trials and all(task)) for task in successes
            )
            if successes and expected_trials == 3
            else None
        )
        ci_low, ci_high = _bootstrap_ci(
            successes,
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
            if item.attempts > 0 and item.task_id != "__benchmark__"
        ]
        turns = _numeric_values(evaluated, "turns")
        tool_calls = _numeric_values(evaluated, "tool_calls")
        total_tokens = _token_values(evaluated)
        categories = Counter(
            item.error_category.value
            for item in records
            if item.error_category.value != "none"
        )
        baseline = _exact_baseline(
            registry,
            benchmark_id=benchmark_id,
            benchmark_version=benchmark_version,
            deployment_id=deployment_id,
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
                expected_trials=expected_trials,
                task_count=len(task_records),
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
            )
        )
    return aggregates


def _display_percentage(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_markdown_report(
    manifest: RunManifest,
    aggregates: list[AggregateResult],
    baselines: BaselineRegistry,
) -> str:
    lines = [
        f"# SII Holos Tool-use Evaluation — `{manifest.run_id}`",
        "",
        "## Provenance",
        "",
        f"- Git commit: `{manifest.git_commit}`",
        f"- Working tree dirty at launch: `{str(manifest.git_dirty).lower()}`",
        f"- Created at: `{manifest.created_at.isoformat()}`",
        f"- Configuration SHA-256: `{manifest.configuration_sha256}`",
        f"- Dependency lock SHA-256: `{manifest.dependency_lock_sha256}`",
        "",
        "## Results",
        "",
        "| Benchmark | Lane | Deployment | Tasks | Pass@1 | 95% CI "
        "| Pass@3 | Pass^3 | Error rate |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
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
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "No cross-benchmark composite score is calculated. A missing value means "
            "the lane did not produce comparable task-level observations.",
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
            "- Quantized SII Holos deployments are not assumed equivalent to "
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
    manifest, _, results = load_completed_run(run_directory)
    registry = baselines or load_baselines()
    aggregates = aggregate_results(results, baselines=registry)
    report_directory = run_directory / "report"
    report_directory.mkdir(exist_ok=False)

    metrics_payload = [item.model_dump(mode="json") for item in aggregates]
    (report_directory / "metrics.json").write_text(
        json.dumps(metrics_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
            writer.writerow(row)
    (report_directory / "report.md").write_text(
        render_markdown_report(manifest, aggregates, registry),
        encoding="utf-8",
    )
    return report_directory
