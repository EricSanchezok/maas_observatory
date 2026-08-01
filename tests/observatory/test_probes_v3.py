from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from maas_observatory.fixtures import FIXTURE_IDS
from maas_observatory.models import ErrorClass, ProbeKind, ProbeOutcome
from maas_observatory.probes import (
    ProbeRunner,
    ProbeScheduler,
    _observer_http_client,
    _output_from_delta,
    balanced_order,
    block_nonce,
    classify_error,
    fixture_prompt,
    profile_definitions,
)
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    make_settings,
    open_database,
)


def _sse_response(
    *,
    usage: bool = True,
    visible: bool = True,
    tool_call: bool = False,
    status: int = 200,
    prompt_tokens: int = 1_000,
    reasoning_tokens: int | None = 5,
) -> httpx.Response:
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}',
    ]
    if tool_call:
        lines.append(
            'data: {"choices":[{"delta":{"tool_calls":'
            '[{"index":0,"function":{"name":"f"}}]}}]}'
        )
    if visible:
        lines.extend(
            [
                'data: {"choices":[{"delta":{"content":"hello "}}]}',
                'data: {"choices":[{"delta":{"content":"world"},'
                '"finish_reason":"stop"}]}',
            ]
        )
    if usage:
        lines.append(
            "data: "
            + json.dumps(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": 8,
                        "completion_tokens_details": (
                            {"reasoning_tokens": reasoning_tokens}
                            if reasoning_tokens is not None
                            else {}
                        ),
                    },
                },
                separators=(",", ":"),
            )
        )
    lines.append("data: [DONE]")
    return httpx.Response(
        status,
        headers={"content-type": "text/event-stream"},
        content=("\n\n".join(lines) + "\n\n").encode(),
    )


class _DelayedStream(httpx.AsyncByteStream):
    def __init__(self, first: bytes, *, delay: float = 1.1) -> None:
        self.first = first
        self.delay = delay

    async def __aiter__(self) -> Any:
        yield self.first
        await asyncio.sleep(self.delay)
        yield b"data: [DONE]\n\n"


def test_fixture_prompt_and_balanced_order() -> None:
    catalog = configured_catalog()
    fid, payload = fixture_prompt("agent-1k-a", "abc")
    assert fid == "agent-1k-a"
    assert "abc" in payload["messages"][-1]["content"]

    # Verify deterministic nonce
    assert block_nonce(1, 2) == block_nonce(1, 2)
    assert block_nonce(1, 2) != block_nonce(1, 3)

    orders = [
        [item.deployment_id for item in balanced_order(list(catalog.deployments), i)]
        for i in range(18)
    ]
    assert all(len(set(order)) == 9 for order in orders)
    assert {order[0] for order in orders[:9]} == {
        item.deployment_id for item in catalog.deployments
    }

    settings = make_settings(Path("/tmp"))
    definitions = profile_definitions(settings.experience, settings.probes)
    assert {item["kind"] for item in definitions} == {"agent_response"}
    assert len(definitions[0]["fixtures"]) == 6
    assert definitions[0]["fixtures"][0]["fixture_id"] == "agent-1k-a"


def test_error_classification() -> None:
    request = httpx.Request("GET", "https://models.test")
    assert classify_error(httpx.ReadTimeout("late", request=request))[0] == (
        ErrorClass.TRANSPORT
    )
    assert classify_error(httpx.ConnectError("no", request=request))[1] == "connect"
    response = httpx.Response(503, request=request)
    assert classify_error(
        httpx.HTTPStatusError("bad", request=request, response=response)
    ) == (ErrorClass.SERVICE, "http_503")
    assert classify_error(RuntimeError("bad"))[0] == ErrorClass.MEASUREMENT


def test_observer_client_ignores_environment_and_delta_variants() -> None:
    async def scenario() -> None:
        client = _observer_http_client(httpx.Timeout(5))
        try:
            assert client._trust_env is False
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert _output_from_delta(
        {
            "content": [{"type": "text", "text": "visible"}],
            "reasoning": "hidden",
        }
    ) == ("visible", "hidden")
    assert _output_from_delta(
        {"content": "answer", "reasoning_details": [{"text": "thought"}]}
    ) == ("answer", "thought")


def test_streaming_measurements_use_visible_content_and_reported_usage(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "model"}]},
                    request=request,
                )
            response = _sse_response()
            response.request = request
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
            settings.collection_mode,
            client=client,
        )
        try:
            deployment = catalog.deployments[0]
            route = await runner.route_liveness(deployment)
            assert route.outcome == ProbeOutcome.SUCCESS
            fid, payload = fixture_prompt("agent-1k-a", "test-nonce")
            result = await runner.generation(
                deployment,
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
                block_id="block",
            )
            assert result.outcome == ProbeOutcome.SUCCESS
            assert result.measurements["first_response_seconds"] is not None
            assert result.measurements["first_token_seconds"] is not None
            assert result.measurements["total_response_seconds"] is not None
            assert float(result.measurements["first_response_seconds"]) >= float(
                result.measurements["first_token_seconds"]
            )
            assert result.measurements["reported_completion_tokens"] == 8
            assert result.measurements["reasoning_chars"] == 8
            assert result.measurements["reasoning_tokens_estimated"] == 2
            assert result.measurements["ref_prompt_tokens"] is not None
            assert result.measurements["ref_prompt_tokens"] > 0
            rows = await database.query(
                "SELECT measurement_json FROM probe_runs WHERE kind='experience'"
            )
            payload_db = json.loads(rows[0]["measurement_json"])
            assert "hello" not in json.dumps(payload_db)
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_usage_missing_only_makes_output_speed_unavailable(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)

        def handler(request: httpx.Request) -> httpx.Response:
            response = _sse_response(usage=False)
            response.request = request
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
            settings.collection_mode,
            client=client,
        )
        try:
            fid, payload = fixture_prompt("agent-1k-a", "test")
            result = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            assert result.outcome == ProbeOutcome.SUCCESS
            assert result.error_class == ErrorClass.MEASUREMENT
            assert result.error_code == "streaming_usage_missing"
            assert result.measurements["output_speed_tps"] is None
            assert result.measurements["first_response_seconds"] is not None
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_empty_output_http_failure_and_tool_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)

        responses = [
            _sse_response(visible=False),
            _sse_response(status=503),
            _sse_response(tool_call=True),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            response = responses.pop(0)
            response.request = request
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
            settings.collection_mode,
            client=client,
        )
        try:
            fid, payload = fixture_prompt("agent-1k-a", "test")
            empty = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            failed = await runner.generation(
                catalog.deployments[1],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            tooled = await runner.generation(
                catalog.deployments[2],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            assert (empty.error_class, empty.error_code) == (
                ErrorClass.SERVICE,
                "empty_visible_output",
            )
            assert (failed.error_class, failed.error_code) == (
                ErrorClass.SERVICE,
                "http_503",
            )
            assert (tooled.error_class, tooled.error_code) == (
                ErrorClass.SERVICE,
                "unexpected_tool_call",
            )
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_first_output_timeout_is_distinct_from_stream_stall(tmp_path: Path) -> None:
    async def scenario() -> None:
        base_settings = make_settings(tmp_path)
        settings = base_settings.model_copy(
            update={
                "probes": base_settings.probes.model_copy(
                    update={
                        "response_start_timeout_seconds": 1,
                        "stream_stall_seconds": 1,
                    }
                )
            }
        )
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        streams = [
            _DelayedStream(b": heartbeat\n\n"),
            _DelayedStream(
                b'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}\n\n'
            ),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=streams.pop(0), request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runner = ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
            settings.collection_mode,
            client=client,
        )
        try:
            fid, payload = fixture_prompt("agent-16k-a", "test")
            no_output = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            stalled = await runner.generation(
                catalog.deployments[1],
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload,
            )
            assert (no_output.error_class, no_output.error_code) == (
                ErrorClass.SERVICE,
                "response_start_timeout",
            )
            assert (stalled.error_class, stalled.error_code) == (
                ErrorClass.SERVICE,
                "stream_stall",
            )
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_block_uses_same_fixture_and_nonce_and_records_lag(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        runner = ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
            settings.collection_mode,
        )
        calls: list[dict[str, Any]] = []

        async def fake_generation(
            deployment: Any,
            kind: ProbeKind,
            **kwargs: Any,
        ) -> None:
            calls.append(
                {
                    "deployment": deployment.deployment_id,
                    "kind": kind,
                    **kwargs,
                }
            )

        runner.generation = fake_generation  # type: ignore[method-assign]
        scheduler = ProbeScheduler(runner, database)
        try:
            past = datetime.now(UTC) - timedelta(seconds=20)
            await scheduler.run_block("agent-1k-a", 4, past)
            assert len(calls) == 9
            assert len({call["fixture_id"] for call in calls}) == 1
            assert (
                len({call["prompt_data"]["messages"][-1]["content"] for call in calls})
                == 1
            )
            assert len({call["block_id"] for call in calls}) == 1
            assert len({call["deployment"] for call in calls}) == 9
            block = (await database.query("SELECT * FROM collection_blocks"))[0]
            assert block["status"] == "complete"
            assert block["scheduler_lag_seconds"] >= 19
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_daily_budget_applies_to_standard_and_rapid_modes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        catalog = configured_catalog()
        standard = make_settings(tmp_path / "standard", mode="standard")
        database, writer = await open_database(standard, catalog)
        runner = ProbeRunner(
            catalog,
            standard.probes,
            standard.profiles,
            database,
            standard.experience,
            "standard",
        )
        deployment = catalog.deployments[0]
        try:
            today = datetime.now(UTC).date().isoformat()
            await database.write(
                """
                INSERT INTO budget_ledger(
                    deployment_id, budget_date, requests_settled
                ) VALUES (?, ?, 3)
                """,
                (deployment.deployment_id, today),
            )
            fid, payload_data = fixture_prompt("agent-1k-a", "test")
            result = await runner.generation(
                deployment,
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload_data,
            )
            assert result.outcome == ProbeOutcome.SKIPPED
            assert result.error_code == "daily_response_budget"
        finally:
            await close_database(database, writer)

        rapid = make_settings(tmp_path / "rapid", mode="rapid")
        rapid_database, rapid_writer = await open_database(rapid, catalog)
        rapid_runner = ProbeRunner(
            catalog,
            rapid.probes,
            rapid.profiles,
            rapid_database,
            rapid.experience,
            "rapid",
        )
        try:
            today = datetime.now(UTC).date().isoformat()
            await rapid_database.write(
                """
                INSERT INTO budget_ledger(
                    deployment_id, budget_date, requests_settled
                ) VALUES (?, ?, 3)
                """,
                (deployment.deployment_id, today),
            )
            fid, payload_data = fixture_prompt("agent-1k-a", "test")
            result = await rapid_runner.generation(
                deployment,
                ProbeKind.EXPERIENCE,
                fixture_id=fid,
                prompt_data=payload_data,
                force=True,
            )
            assert result.outcome == ProbeOutcome.SKIPPED
            assert result.error_code == "daily_response_budget"
        finally:
            await close_database(rapid_database, rapid_writer)

    asyncio.run(scenario())


def test_six_block_fixture_rotation_is_strict(tmp_path: Path) -> None:
    """Verify block 0→agent-1k-a, 1→agent-16k-a, ..., 5→agent-64k-b."""
    from maas_observatory.probes import _FIXTURE_ORDER

    assert _FIXTURE_ORDER == FIXTURE_IDS
    for i in range(12):
        expected = FIXTURE_IDS[i % 6]
        assert _FIXTURE_ORDER[i % 6] == expected
