"""BFCL V4 adapter backed by the isolated EvalScope runtime."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tooluse_bench.benchmarks.base import AdapterContext, BenchmarkAdapter
from tooluse_bench.benchmarks.external import (
    isolated_environment,
    run_logged_command,
)
from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.domain import BenchmarkSelection, ModelDeployment
from tooluse_bench.records import (
    BenchmarkMetadata,
    ErrorCategory,
    ExecutionAudit,
    TaskResult,
    TaskStatus,
    ValidationIssue,
    result_from_spec,
)

CORE_SUBSETS = (
    "simple_python",
    "multiple",
    "parallel",
    "parallel_multiple",
    "irrelevance",
    "multi_turn_base",
    "multi_turn_miss_func",
    "multi_turn_miss_param",
)
ALL_PUBLIC_SUBSETS = (
    "simple_python",
    "simple_java",
    "simple_javascript",
    "multiple",
    "parallel",
    "parallel_multiple",
    "live_simple",
    "live_multiple",
    "live_parallel",
    "live_parallel_multiple",
    "irrelevance",
    "live_irrelevance",
    "live_relevance",
    "multi_turn_base",
    "multi_turn_miss_func",
    "multi_turn_miss_param",
    "multi_turn_long_context",
    "web_search_base",
    "web_search_no_snippet",
    "memory_kv",
    "memory_vector",
    "memory_rec_sum",
)
PROFILE_SUBSETS = {
    "smoke": ("simple_python", "parallel", "irrelevance"),
    "core": CORE_SUBSETS,
    "full-public": ALL_PUBLIC_SUBSETS,
}


def _failure_category(task_id: str) -> ErrorCategory:
    subset = task_id.split("/", 1)[0]
    if "irrelevance" in subset or subset in {"multiple", "live_multiple"}:
        return ErrorCategory.SELECTION
    if "parallel" in subset:
        return ErrorCategory.PLANNING
    if subset.startswith("multi_turn"):
        return ErrorCategory.TOOL_RESULT_INTEGRATION
    if subset.startswith(("web_search", "memory")):
        return ErrorCategory.PLANNING
    return ErrorCategory.ARGUMENTS


def _source_artifacts(normalized_path: Path, source_path: object) -> tuple[str, ...]:
    artifacts = [str(normalized_path)]
    if isinstance(source_path, str) and source_path:
        artifacts.append(f"upstream/{source_path}")
    return tuple(artifacts)


class BFCLAdapter(BenchmarkAdapter):
    @property
    def metadata(self) -> BenchmarkMetadata:
        return BenchmarkMetadata(
            benchmark_id="bfcl-v4",
            display_name="Berkeley Function-Calling Leaderboard V4",
            version="bfcl-eval-2025.12.17+evalscope-1.2.0",
            source_url="https://gorilla.cs.berkeley.edu/leaderboard.html",
            revision="bfcl-eval==2025.12.17",
            hermetic_default=False,
            supported_profiles=tuple(PROFILE_SUBSETS),
        )

    def validate(
        self, selection: BenchmarkSelection, deployment: ModelDeployment
    ) -> tuple[ValidationIssue, ...]:
        issues = list(super().validate(selection, deployment))
        for option, default, minimum, maximum in (
            ("batch_size", 1, 1, None),
            ("sdk_max_retries", 2, 0, 2),
        ):
            value = selection.options.get(option, default)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
                or (maximum is not None and value > maximum)
            ):
                range_text = (
                    f"between {minimum} and {maximum}"
                    if maximum is not None
                    else f"at least {minimum}"
                )
                issues.append(
                    ValidationIssue(
                        level="error",
                        code=f"invalid_{option}",
                        message=f"{option} must be an integer {range_text}",
                    )
                )
        request_timeout = selection.options.get("request_timeout_seconds", 180)
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, int | float)
            or request_timeout <= 0
        ):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="invalid_request_timeout_seconds",
                    message="request_timeout_seconds must be a positive number",
                )
            )
        if selection.profile == "full-public" and not os.getenv("SERPAPI_API_KEY"):
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="missing_serpapi_key",
                    message=(
                        "SERPAPI_API_KEY is absent; web-search subsets may produce "
                        "infrastructure failures"
                    ),
                )
            )
        return tuple(issues)

    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        runtime = PROJECT_ROOT / "benchmark-envs" / "bfcl"
        output_root = context.workspace / "upstream"
        output_root.mkdir()
        limit = context.selection.options.get("limit")
        if limit is None and context.selection.profile == "smoke":
            limit = 10
        batch_size = int(context.selection.options.get("batch_size", 1))
        sdk_max_retries = int(context.selection.options.get("sdk_max_retries", 2))
        request_timeout_seconds = float(
            context.selection.options.get("request_timeout_seconds", 180)
        )
        spec = {
            "model_id": context.deployment.model_id,
            "subsets": list(PROFILE_SUBSETS[context.selection.profile]),
            "batch_size": batch_size,
            "sdk_max_retries": sdk_max_retries,
            "request_timeout_seconds": request_timeout_seconds,
            "limit": limit,
            "generation_config": {
                "temperature": float(context.selection.options.get("temperature", 0)),
                "seed": context.spec.seed,
            },
            "output_root": str(output_root),
        }
        spec_path = context.workspace / "bfcl-spec.json"
        spec_path.write_text(
            json.dumps(spec, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        environment_values = {
            "SII_BENCH_BASE_URL": context.deployment.base_url or "",
            "SII_BENCH_API_KEY": context.deployment.api_key or "",
            "PYTHONUNBUFFERED": "1",
        }
        if serpapi_key := os.getenv("SERPAPI_API_KEY"):
            environment_values["SERPAPI_API_KEY"] = serpapi_key
        process_timeout_seconds = float(
            context.selection.options.get("timeout_seconds", 21600)
        )
        started_at = datetime.now(UTC)
        outcome = run_logged_command(
            [
                "uv",
                "run",
                "--project",
                str(runtime),
                "--frozen",
                "python",
                str(runtime / "run_eval.py"),
                str(spec_path),
            ],
            cwd=context.workspace,
            environment=isolated_environment(environment_values),
            timeout_seconds=process_timeout_seconds,
        )
        finished_at = datetime.now(UTC)
        stderr_text = outcome.stderr_path.read_text(encoding="utf-8", errors="replace")
        audit_path = context.workspace / "execution-audit.json"
        audit = ExecutionAudit(
            run_id=context.spec.run_id,
            benchmark_id=context.spec.benchmark_id,
            deployment_id=context.spec.deployment_id,
            lane=context.spec.lane,
            trial=context.spec.trial,
            started_at=started_at,
            finished_at=finished_at,
            resource_controls={
                "batch_size": batch_size,
                "request_timeout_seconds": request_timeout_seconds,
                "sdk_max_retries": sdk_max_retries,
                "process_timeout_seconds": process_timeout_seconds,
            },
            observations={
                "process_return_code": outcome.return_code,
                "process_wall_seconds": outcome.wall_seconds,
                "observed_sdk_retry_log_count": stderr_text.count("Retrying request"),
                "upstream_unbounded_rate_limit_retry_disabled": True,
            },
        )
        audit_path.write_text(
            json.dumps(audit.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifacts = (
            str(outcome.stdout_path.relative_to(context.workspace)),
            str(outcome.stderr_path.relative_to(context.workspace)),
            str(audit_path.relative_to(context.workspace)),
        )
        normalized_path = output_root / "adapter-results.jsonl"
        error_path = output_root / "adapter-errors.jsonl"
        if not normalized_path.exists() and not error_path.exists():
            category = (
                ErrorCategory.TIMEOUT
                if outcome.return_code == 124
                else ErrorCategory.INFRASTRUCTURE
            )
            yield result_from_spec(
                context.spec,
                task_id="__benchmark__",
                status=TaskStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                latency_seconds=outcome.wall_seconds,
                attempts=1,
                error_category=category,
                error_detail=f"BFCL runtime exited with code {outcome.return_code}",
                artifact_paths=artifacts,
            )
            return

        if normalized_path.exists():
            with normalized_path.open(encoding="utf-8") as normalized:
                for line_number, line in enumerate(normalized, start=1):
                    if not line.strip():
                        continue
                    raw: dict[str, Any] = json.loads(line)
                    raw_error_category = raw.get("error_category")
                    if isinstance(raw_error_category, str):
                        yield result_from_spec(
                            context.spec,
                            task_id=str(raw.get("task_id", f"record-{line_number}")),
                            status=TaskStatus.ERROR,
                            started_at=started_at,
                            finished_at=finished_at,
                            latency_seconds=None,
                            attempts=1,
                            error_category=ErrorCategory(raw_error_category),
                            error_detail=str(
                                raw.get("error_detail", "BFCL inference failed")
                            ),
                            response={
                                "upstream_error": str(
                                    raw.get(
                                        "error_detail",
                                        "BFCL inference failed",
                                    )
                                )
                            },
                            artifact_paths=_source_artifacts(
                                normalized_path.relative_to(context.workspace),
                                raw.get("source_path"),
                            ),
                        )
                        continue
                    score = float(raw["score"])
                    task_id = str(raw.get("task_id", f"record-{line_number}"))
                    yield result_from_spec(
                        context.spec,
                        task_id=task_id,
                        status=TaskStatus.PASS if score == 1 else TaskStatus.FAIL,
                        score=score,
                        started_at=started_at,
                        finished_at=finished_at,
                        latency_seconds=(
                            float(raw["latency_seconds"])
                            if isinstance(raw.get("latency_seconds"), int | float)
                            and not isinstance(raw["latency_seconds"], bool)
                            else None
                        ),
                        response={"upstream_record": raw.get("record")},
                        error_category=(
                            ErrorCategory.NONE
                            if score == 1
                            else _failure_category(task_id)
                        ),
                        artifact_paths=_source_artifacts(
                            normalized_path.relative_to(context.workspace),
                            raw.get("source_path"),
                        ),
                    )

        if error_path.exists():
            error_record_count = 0
            with error_path.open(encoding="utf-8") as errors:
                for line in errors:
                    if not line.strip():
                        continue
                    error_record_count += 1
                    error: dict[str, Any] = json.loads(line)
                    subset = str(error.get("subset", "unknown"))
                    yield result_from_spec(
                        context.spec,
                        task_id=f"__subset__/{subset}",
                        status=TaskStatus.ERROR,
                        started_at=started_at,
                        finished_at=finished_at,
                        latency_seconds=None,
                        attempts=1,
                        error_category=ErrorCategory.INFRASTRUCTURE,
                        error_detail=(
                            f"{error.get('error_type', 'Error')}: "
                            f"{error.get('error_detail', 'BFCL subset failed')}"
                        ),
                        artifact_paths=(
                            str(error_path.relative_to(context.workspace)),
                            *artifacts,
                        ),
                    )
        else:
            error_record_count = 0

        if outcome.return_code == 124:
            yield result_from_spec(
                context.spec,
                task_id="__benchmark__",
                status=TaskStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                latency_seconds=outcome.wall_seconds,
                attempts=1,
                error_category=ErrorCategory.TIMEOUT,
                error_detail="BFCL runtime exceeded the configured timeout",
                artifact_paths=artifacts,
            )
        elif outcome.return_code != 0 and error_record_count == 0:
            yield result_from_spec(
                context.spec,
                task_id="__benchmark__",
                status=TaskStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                latency_seconds=outcome.wall_seconds,
                attempts=1,
                error_category=ErrorCategory.INFRASTRUCTURE,
                error_detail=f"BFCL runtime exited with code {outcome.return_code}",
                artifact_paths=artifacts,
            )
