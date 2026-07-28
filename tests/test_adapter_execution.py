from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tooluse_bench.benchmarks.base import AdapterContext
from tooluse_bench.benchmarks.bfcl import BFCLAdapter
from tooluse_bench.benchmarks.external import CommandOutcome, run_logged_command
from tooluse_bench.benchmarks.probe import ProbeAdapter
from tooluse_bench.benchmarks.toolathlon import (
    TOOLATHLON_REVISION,
    ToolathlonAdapter,
    ensure_checkout,
)
from tooluse_bench.config import load_catalog
from tooluse_bench.domain import BenchmarkSelection, Lane
from tooluse_bench.records import ErrorCategory, RunSpec, TaskStatus
from tooluse_bench.transport import TransportFailure, TransportResponse


def context(
    tmp_path: Path,
    *,
    benchmark_id: str,
    profile: str,
    options: dict | None = None,
    transport=None,
    lane: Lane = Lane.STANDARDIZED,
) -> AdapterContext:
    workspace = tmp_path / f"{benchmark_id}-{profile}"
    workspace.mkdir(parents=True)
    selection = BenchmarkSelection(
        benchmark_id=benchmark_id,
        profile=profile,
        trials=1,
        options=options or {},
    )
    return AdapterContext(
        spec=RunSpec(
            run_id="run-adapter",
            experiment_id="adapter-test",
            benchmark_id=benchmark_id,
            benchmark_version="test",
            profile=profile,
            lane=lane,
            deployment_id=load_catalog()[0].deployment_id,
            model_alias=load_catalog()[0].alias,
            trial=1,
            seed=7,
        ),
        deployment=load_catalog()[0],
        selection=selection,
        workspace=workspace,
        transport=transport,
    )


def command_outcome(workspace: Path, *, return_code: int = 0) -> CommandOutcome:
    stdout = workspace / "adapter.stdout.log"
    stderr = workspace / "adapter.stderr.log"
    stdout.write_text("stdout", encoding="utf-8")
    stderr.write_text("stderr", encoding="utf-8")
    return CommandOutcome(
        return_code=return_code,
        wall_seconds=0.25,
        stdout_path=stdout,
        stderr_path=stderr,
    )


class SequenceTransport:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = iter(messages)

    def chat_completion(self, payload: dict) -> TransportResponse:
        del payload
        return TransportResponse(
            payload={
                "choices": [{"message": next(self.messages)}],
                "usage": {"total_tokens": 10},
            },
            attempts=1,
            latency_seconds=0.1,
            status_code=200,
        )


def tool_message(*calls: tuple[str, dict]) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for name, arguments in calls
        ],
    }


def test_probe_adapter_executes_all_cases(tmp_path: Path) -> None:
    transport = SequenceTransport(
        [
            tool_message(("add", {"x": 17, "y": 25})),
            {"content": "Leaves reflect green light."},
            tool_message(("get_exchange_rate", {"base": "USD", "quote": "CNY"})),
            tool_message(
                ("get_weather", {"location": "Shanghai", "unit": "celsius"}),
                ("get_weather", {"location": "北京", "unit": "celsius"}),
            ),
            {"content": "What is your origin?"},
        ]
    )
    adapter = ProbeAdapter()
    results = list(
        adapter.run(
            context(
                tmp_path / "probe-success",
                benchmark_id="probe",
                profile="full",
                transport=transport,
            )
        )
    )
    assert adapter.needs_native_transport()
    assert [result.status for result in results] == [TaskStatus.PASS] * 5
    assert all(result.usage == {"total_tokens": 10} for result in results)
    assert all(
        result.request and result.request["max_tokens"] == 4096 for result in results
    )

    for invalid_value in (0, "many", True, load_catalog()[0].output_limit + 1):
        invalid_selection = BenchmarkSelection(
            benchmark_id="probe",
            profile="full",
            trials=1,
            options={"max_tokens": invalid_value},
        )
        assert [
            issue.code
            for issue in adapter.validate(invalid_selection, load_catalog()[0])
        ] == ["invalid_max_tokens"]


def test_probe_adapter_records_transport_and_protocol_errors(tmp_path: Path) -> None:
    class FailureTransport:
        def chat_completion(self, payload: dict) -> TransportResponse:
            del payload
            raise TransportFailure(
                "timed out",
                category=ErrorCategory.TIMEOUT,
                attempts=3,
            )

    transport_results = list(
        ProbeAdapter().run(
            context(
                tmp_path / "probe-transport",
                benchmark_id="probe",
                profile="full",
                transport=FailureTransport(),
            )
        )
    )
    assert {result.error_category for result in transport_results} == {
        ErrorCategory.TIMEOUT
    }
    assert {result.attempts for result in transport_results} == {3}

    malformed = SequenceTransport([{"content": 3}] * 5)
    protocol_results = list(
        ProbeAdapter().run(
            context(
                tmp_path / "probe-protocol",
                benchmark_id="probe",
                profile="full",
                transport=malformed,
            )
        )
    )
    assert {result.status for result in protocol_results} == {TaskStatus.FAIL}

    missing_message = SequenceTransport([{}] * 5)
    missing_results = list(
        ProbeAdapter().run(
            context(
                tmp_path / "probe-missing",
                benchmark_id="probe",
                profile="full",
                transport=missing_message,
            )
        )
    )
    assert {result.status for result in missing_results} == {TaskStatus.FAIL}

    with pytest.raises(RuntimeError, match="native OpenAI transport"):
        list(
            ProbeAdapter().run(
                context(
                    tmp_path / "probe-none",
                    benchmark_id="probe",
                    profile="full",
                )
            )
        )


def test_bfcl_adapter_normalizes_results_and_failures(tmp_path: Path) -> None:
    adapter_context = context(
        tmp_path,
        benchmark_id="bfcl-v4",
        profile="smoke",
    )

    def successful_command(*args, **kwargs) -> CommandOutcome:
        del args, kwargs
        output = adapter_context.workspace / "upstream" / "adapter-results.jsonl"
        output.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "task_id": "pass-task",
                            "score": 1,
                            "latency_seconds": 0.5,
                            "record": {"ok": True},
                            "source_path": "source-a",
                        }
                    ),
                    json.dumps({"score": 0, "record": {}}),
                ]
            ),
            encoding="utf-8",
        )
        errors = adapter_context.workspace / "upstream" / "adapter-errors.jsonl"
        errors.write_text(
            json.dumps(
                {
                    "subset": "web_search_base",
                    "error_type": "MissingCredential",
                    "error_detail": "SERPAPI_API_KEY is required",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return command_outcome(adapter_context.workspace)

    with patch(
        "tooluse_bench.benchmarks.bfcl.run_logged_command",
        side_effect=successful_command,
    ):
        results = list(BFCLAdapter().run(adapter_context))
    assert [result.status for result in results] == [
        TaskStatus.PASS,
        TaskStatus.FAIL,
        TaskStatus.ERROR,
    ]
    assert [result.latency_seconds for result in results] == [0.5, None, None]
    assert results[-1].task_id == "__subset__/web_search_base"
    spec = json.loads(
        (adapter_context.workspace / "bfcl-spec.json").read_text(encoding="utf-8")
    )
    assert spec["limit"] == 10
    assert spec["subsets"] == ["simple_python", "parallel", "irrelevance"]
    assert spec["batch_size"] == 1
    assert spec["sdk_max_retries"] == 2
    assert spec["request_timeout_seconds"] == 180

    timeout_context = context(
        tmp_path,
        benchmark_id="bfcl-v4",
        profile="core",
    )
    with patch(
        "tooluse_bench.benchmarks.bfcl.run_logged_command",
        return_value=command_outcome(timeout_context.workspace, return_code=124),
    ):
        [failure] = list(BFCLAdapter().run(timeout_context))
    assert failure.error_category is ErrorCategory.TIMEOUT

    partial_context = context(
        tmp_path / "partial-timeout",
        benchmark_id="bfcl-v4",
        profile="core",
    )

    def partial_timeout(*args, **kwargs) -> CommandOutcome:
        del args, kwargs
        output = partial_context.workspace / "upstream" / "adapter-results.jsonl"
        output.write_text(
            json.dumps(
                {
                    "task_id": "completed-before-timeout",
                    "score": 1,
                    "record": {},
                    "source_path": "reviews/completed.jsonl",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return command_outcome(partial_context.workspace, return_code=124)

    with patch(
        "tooluse_bench.benchmarks.bfcl.run_logged_command",
        side_effect=partial_timeout,
    ):
        partial_results = list(BFCLAdapter().run(partial_context))
    assert [result.status for result in partial_results] == [
        TaskStatus.PASS,
        TaskStatus.ERROR,
    ]
    assert partial_results[-1].error_category is ErrorCategory.TIMEOUT

    crashed_context = context(
        tmp_path / "unexpected-exit",
        benchmark_id="bfcl-v4",
        profile="core",
    )

    def unexpected_exit(*args, **kwargs) -> CommandOutcome:
        del args, kwargs
        output = crashed_context.workspace / "upstream" / "adapter-results.jsonl"
        output.write_text("", encoding="utf-8")
        return command_outcome(crashed_context.workspace, return_code=9)

    with patch(
        "tooluse_bench.benchmarks.bfcl.run_logged_command",
        side_effect=unexpected_exit,
    ):
        [crash] = list(BFCLAdapter().run(crashed_context))
    assert crash.error_category is ErrorCategory.INFRASTRUCTURE


def test_toolathlon_adapter_normalizes_results_and_validates_options(
    tmp_path: Path,
) -> None:
    adapter = ToolathlonAdapter()
    deployment = load_catalog()[0]
    invalid = BenchmarkSelection(
        benchmark_id="toolathlon-verified",
        profile="official",
        trials=1,
        options={"backend": "invalid"},
    )
    assert {issue.code for issue in adapter.validate(invalid, deployment)} == {
        "invalid_backend"
    }
    public = invalid.model_copy(update={"options": {"backend": "public-service"}})
    assert [issue.code for issue in adapter.validate(public, deployment)] == [
        "non_hermetic_backend"
    ]
    assert adapter.supports_lane(Lane.STANDARDIZED, public, deployment) == (
        True,
        None,
    )
    official = public.model_copy(
        update={"options": {"official_protocols": {deployment.alias: {}}}}
    )
    assert adapter.supports_lane(Lane.OFFICIAL_REPRODUCTION, official, deployment) == (
        True,
        None,
    )

    adapter_context = context(
        tmp_path,
        benchmark_id="toolathlon-verified",
        profile="smoke",
        options={
            "backend": "public-service",
            "tasks": ["task-pass", "task-fail", "task-invalid"],
        },
    )

    def successful_command(*args, **kwargs) -> CommandOutcome:
        del args, kwargs
        for task, payload in {
            "task-pass": {"pass": True, "duration_seconds": 1},
            "task-fail": {"pass": False},
            "task-invalid": {"pass": "yes"},
        }.items():
            path = adapter_context.workspace / "upstream" / task / "eval_res.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        return command_outcome(adapter_context.workspace)

    with (
        patch(
            "tooluse_bench.benchmarks.toolathlon.ensure_checkout",
            return_value=tmp_path / "checkout",
        ),
        patch(
            "tooluse_bench.benchmarks.toolathlon.run_logged_command",
            side_effect=successful_command,
        ),
    ):
        results = list(adapter.run(adapter_context))
    assert {result.status for result in results} == {
        TaskStatus.PASS,
        TaskStatus.FAIL,
        TaskStatus.ERROR,
    }
    assert (
        adapter_context.workspace / "task-list.txt"
    ).read_text() == "task-pass\ntask-fail\ntask-invalid\n"


def test_toolathlon_adapter_records_no_output_and_timeout(tmp_path: Path) -> None:
    for return_code, expected in (
        (0, ErrorCategory.INFRASTRUCTURE),
        (124, ErrorCategory.TIMEOUT),
    ):
        adapter_context = context(
            tmp_path / f"outcome-{return_code}",
            benchmark_id="toolathlon-verified",
            profile="official",
            options={"backend": "public-service"},
        )
        with (
            patch(
                "tooluse_bench.benchmarks.toolathlon.ensure_checkout",
                return_value=tmp_path / "checkout",
            ),
            patch(
                "tooluse_bench.benchmarks.toolathlon.run_logged_command",
                return_value=command_outcome(
                    adapter_context.workspace, return_code=return_code
                ),
            ),
        ):
            [result] = list(ToolathlonAdapter().run(adapter_context))
        assert result.error_category is expected

    invalid_context = context(
        tmp_path,
        benchmark_id="toolathlon-verified",
        profile="smoke",
        options={"backend": "public-service", "tasks": "not-a-list"},
    )
    with (
        patch(
            "tooluse_bench.benchmarks.toolathlon.ensure_checkout",
            return_value=tmp_path / "checkout",
        ),
        pytest.raises(ValueError, match="list of strings"),
    ):
        list(ToolathlonAdapter().run(invalid_context))


def test_toolathlon_checkout_validation(tmp_path: Path) -> None:
    root = tmp_path / "benchmark-worktrees"
    checkout = root / f"toolathlon-{TOOLATHLON_REVISION[:12]}"

    def fake_git(arguments: list[str], *, cwd: Path) -> str:
        if arguments[0] == "clone":
            checkout.mkdir(parents=True)
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return TOOLATHLON_REVISION
        return ""

    with (
        patch("tooluse_bench.benchmarks.toolathlon.PROJECT_ROOT", tmp_path),
        patch(
            "tooluse_bench.benchmarks.toolathlon._run_git",
            side_effect=fake_git,
        ),
    ):
        assert ensure_checkout() == checkout

    with (
        patch("tooluse_bench.benchmarks.toolathlon.PROJECT_ROOT", tmp_path),
        patch(
            "tooluse_bench.benchmarks.toolathlon._run_git",
            side_effect=["wrong-revision"],
        ),
        pytest.raises(RuntimeError, match="expected"),
    ):
        ensure_checkout()


def test_logged_command_records_success_and_timeout(tmp_path: Path) -> None:
    success = run_logged_command(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        environment={},
        timeout_seconds=5,
    )
    assert success.return_code == 0
    assert success.stdout_path.read_text().strip() == "ok"

    timeout = run_logged_command(
        [sys.executable, "-c", "import time; time.sleep(1)"],
        cwd=tmp_path,
        environment={},
        timeout_seconds=0.01,
    )
    assert timeout.return_code == 124
