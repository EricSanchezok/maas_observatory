"""BFCL V4 adapter backed by the isolated EvalScope runtime."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
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
        spec = {
            "model_id": context.deployment.model_id,
            "subsets": list(PROFILE_SUBSETS[context.selection.profile]),
            "batch_size": int(context.selection.options.get("batch_size", 1)),
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
            timeout_seconds=float(
                context.selection.options.get("timeout_seconds", 21600)
            ),
        )
        finished_at = datetime.now(UTC)
        artifacts = (
            str(outcome.stdout_path.relative_to(context.workspace)),
            str(outcome.stderr_path.relative_to(context.workspace)),
        )
        normalized_path = output_root / "adapter-results.jsonl"
        if outcome.return_code != 0 or not normalized_path.exists():
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

        for line_number, line in enumerate(
            normalized_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            raw: dict[str, Any] = json.loads(line)
            score = float(raw["score"])
            yield result_from_spec(
                context.spec,
                task_id=str(raw.get("task_id", f"record-{line_number}")),
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
                artifact_paths=(
                    str(normalized_path.relative_to(context.workspace)),
                    str(raw.get("source_path", "")),
                ),
            )
