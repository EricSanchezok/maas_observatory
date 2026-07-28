"""Toolathlon-Verified adapter using the pinned official evaluation client."""

from __future__ import annotations

import json
import os
import subprocess
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
from tooluse_bench.domain import BenchmarkSelection, Lane, ModelDeployment
from tooluse_bench.records import (
    BenchmarkMetadata,
    ErrorCategory,
    TaskResult,
    TaskStatus,
    ValidationIssue,
    result_from_spec,
)

TOOLATHLON_REPOSITORY = "https://github.com/hkust-nlp/Toolathlon.git"
TOOLATHLON_REVISION = "2aed2468858f15818acafa178518390cc4b0f5cb"
TOOLATHLON_TASK_IMAGE_DIGEST = (
    "sha256:4d04fe4e0a6fdb4946f51bb05120cb44a0eef980231c11252f93b62897afcb9f"
)
TOOLATHLON_TASK_IMAGE = (
    "docker.io/lockon0927/toolathlon-task-image@" + TOOLATHLON_TASK_IMAGE_DIGEST
)
PUBLIC_SERVER_HOST = "47.253.6.47"


def _run_git(arguments: list[str], *, cwd: Path) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def ensure_checkout() -> Path:
    root = PROJECT_ROOT / "benchmark-worktrees"
    root.mkdir(exist_ok=True)
    checkout = root / f"toolathlon-{TOOLATHLON_REVISION[:12]}"
    if not checkout.exists():
        _run_git(
            [
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                TOOLATHLON_REPOSITORY,
                str(checkout),
            ],
            cwd=root,
        )
        _run_git(["checkout", "--detach", TOOLATHLON_REVISION], cwd=checkout)
    revision = _run_git(["rev-parse", "HEAD"], cwd=checkout)
    if revision != TOOLATHLON_REVISION:
        raise RuntimeError(
            f"Toolathlon checkout has revision {revision}, expected "
            f"{TOOLATHLON_REVISION}"
        )
    if _run_git(["status", "--porcelain"], cwd=checkout):
        raise RuntimeError("Toolathlon checkout is dirty")
    return checkout


def _find_eval_results(output_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    results: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(output_root.rglob("eval_res.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            results.append((path, payload))
    return results


class ToolathlonAdapter(BenchmarkAdapter):
    @property
    def metadata(self) -> BenchmarkMetadata:
        return BenchmarkMetadata(
            benchmark_id="toolathlon-verified",
            display_name="Toolathlon-Verified",
            version="verified-2026-06-30",
            source_url="https://github.com/hkust-nlp/Toolathlon",
            revision=TOOLATHLON_REVISION,
            hermetic_default=False,
            supported_profiles=("smoke", "official"),
        )

    def validate(
        self, selection: BenchmarkSelection, deployment: ModelDeployment
    ) -> tuple[ValidationIssue, ...]:
        issues = list(super().validate(selection, deployment))
        backend = str(selection.options.get("backend", "self-hosted"))
        if backend not in {"self-hosted", "public-service"}:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="invalid_backend",
                    message="backend must be self-hosted or public-service",
                )
            )
        if backend == "self-hosted" and not os.getenv("TOOLATHLON_SERVER_HOST"):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="missing_toolathlon_server",
                    message=(
                        "TOOLATHLON_SERVER_HOST is required for the self-hosted "
                        "Toolathlon backend"
                    ),
                )
            )
        if backend == "self-hosted":
            expected_options = {
                "server_revision": TOOLATHLON_REVISION,
                "task_image": TOOLATHLON_TASK_IMAGE,
            }
            for option, expected in expected_options.items():
                if selection.options.get(option) != expected:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            code=f"unpinned_toolathlon_{option}",
                            message=f"Toolathlon {option} must equal {expected}",
                        )
                    )
            attestations = {
                "TOOLATHLON_SERVER_REVISION": TOOLATHLON_REVISION,
                "TOOLATHLON_TASK_IMAGE_DIGEST": TOOLATHLON_TASK_IMAGE_DIGEST,
            }
            for variable, expected in attestations.items():
                if os.getenv(variable) != expected:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            code="invalid_toolathlon_attestation",
                            message=(
                                f"{variable} must attest the pinned value {expected}"
                            ),
                        )
                    )
        if backend == "public-service":
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="non_hermetic_backend",
                    message=(
                        "the public Toolathlon service is rate-limited and non-hermetic"
                    ),
                )
            )
        return tuple(issues)

    def supports_lane(
        self,
        lane: Lane,
        selection: BenchmarkSelection,
        deployment: ModelDeployment,
    ) -> tuple[bool, str | None]:
        if lane is Lane.STANDARDIZED:
            return True, None
        protocols = selection.options.get("official_protocols", {})
        if isinstance(protocols, dict) and deployment.alias in protocols:
            return True, None
        return (
            False,
            "no complete per-model Toolathlon official protocol is registered",
        )

    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        checkout = ensure_checkout()
        runtime = PROJECT_ROOT / "benchmark-envs" / "toolathlon"
        output_root = context.workspace / "upstream"
        output_root.mkdir()
        backend = str(context.selection.options.get("backend", "self-hosted"))
        server_host = (
            os.getenv("TOOLATHLON_SERVER_HOST", "")
            if backend == "self-hosted"
            else PUBLIC_SERVER_HOST
        )
        task_list_path: Path | None = None
        if context.selection.profile == "smoke":
            task_list_path = context.workspace / "task-list.txt"
            smoke_tasks = context.selection.options.get("tasks", ["find-alita-paper"])
            if not isinstance(smoke_tasks, list) or not all(
                isinstance(task, str) for task in smoke_tasks
            ):
                raise ValueError("Toolathlon smoke tasks must be a list of strings")
            task_list_path.write_text("\n".join(smoke_tasks) + "\n", encoding="utf-8")

        job_id = (
            f"{context.spec.run_id}-{context.deployment.alias}-"
            f"{context.spec.lane.value}-t{context.spec.trial}"
        )
        environment_values = {
            "SII_BENCH_BASE_URL": context.deployment.base_url or "",
            "SII_BENCH_API_KEY": context.deployment.api_key or "",
            "SII_BENCH_MODEL_ID": context.deployment.model_id,
            "TOOLATHLON_CLIENT_PATH": str(checkout / "eval_client.py"),
            "TOOLATHLON_OUTPUT_DIR": str(output_root),
            "TOOLATHLON_SERVER_HOST": server_host,
            "TOOLATHLON_SERVER_PORT": str(
                context.selection.options.get(
                    "server_port", os.getenv("TOOLATHLON_SERVER_PORT", "8080")
                )
            ),
            "TOOLATHLON_WORKERS": str(context.selection.options.get("workers", 10)),
            "TOOLATHLON_JOB_ID": job_id,
            "PYTHONUNBUFFERED": "1",
        }
        if task_list_path is not None:
            environment_values["TOOLATHLON_TASK_LIST_FILE"] = str(task_list_path)
        started_at = datetime.now(UTC)
        outcome = run_logged_command(
            [
                "uv",
                "run",
                "--project",
                str(runtime),
                "--frozen",
                "python",
                str(runtime / "run_client.py"),
            ],
            cwd=context.workspace,
            environment=isolated_environment(environment_values),
            timeout_seconds=float(
                context.selection.options.get("timeout_seconds", 21600)
            ),
        )
        finished_at = datetime.now(UTC)
        common_artifacts = (
            str(outcome.stdout_path.relative_to(context.workspace)),
            str(outcome.stderr_path.relative_to(context.workspace)),
        )
        upstream_results = _find_eval_results(output_root)
        if outcome.return_code != 0 and not upstream_results:
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
                error_detail=(
                    f"Toolathlon client exited with code {outcome.return_code}"
                ),
                artifact_paths=common_artifacts,
            )
            return
        if not upstream_results:
            yield result_from_spec(
                context.spec,
                task_id="__benchmark__",
                status=TaskStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                latency_seconds=outcome.wall_seconds,
                attempts=1,
                error_category=ErrorCategory.INFRASTRUCTURE,
                error_detail="Toolathlon produced no eval_res.json files",
                artifact_paths=common_artifacts,
            )
            return

        for path, evaluation in upstream_results:
            passed = evaluation.get("pass")
            detail: str | None
            if not isinstance(passed, bool):
                status = TaskStatus.ERROR
                score = None
                category = ErrorCategory.PROTOCOL
                detail = "eval_res.json does not contain a boolean 'pass'"
            else:
                status = TaskStatus.PASS if passed else TaskStatus.FAIL
                score = float(passed)
                category = ErrorCategory.NONE if passed else ErrorCategory.PLANNING
                detail = None if passed else "Toolathlon verifier rejected the result"
            yield result_from_spec(
                context.spec,
                task_id=path.parent.name,
                status=status,
                score=score,
                started_at=started_at,
                finished_at=finished_at,
                latency_seconds=float(evaluation.get("duration_seconds", 0)),
                response={"evaluation": evaluation},
                error_category=category,
                error_detail=detail,
                artifact_paths=(
                    *common_artifacts,
                    str(path.relative_to(context.workspace)),
                ),
            )
