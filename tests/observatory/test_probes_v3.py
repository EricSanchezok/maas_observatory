from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from maas_observatory.models import ErrorClass, ProbeKind, ProbeOutcome
from maas_observatory.probes import (
    ProbeRunner,
    ProbeScheduler,
    _observer_http_client,
    _output_from_delta,
    balanced_order,
    block_nonce,
    classify_error,
    fixture_hashes,
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
    status: int = 200,
) -> httpx.Response:
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}',
    ]
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
            'data: {"choices":[],"usage":{"prompt_tokens":32,"completion_tokens":8}}'
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


def test_suite_fixtures_nonce_and_balanced_order() -> None:
    catalog = configured_catalog()
    short_id, short = fixture_prompt(ProbeKind.EXPERIENCE_SHORT, 0, "abc")
    long_id, long_prompt = fixture_prompt(ProbeKind.EXPERIENCE_CONTEXT, 0, "abc")
    assert short_id == "response-01"
    assert "abc" in short[:40]
    assert long_id == "response-04"
    assert len(long_prompt.encode()) == 16 * 1024
    assert "abc" in long_prompt[:40]
    hashes = fixture_hashes()
    assert len(hashes) == 6
    assert all(len(value) == 64 for value in hashes.values())
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
    assert {item["kind"] for item in definitions} == {"balanced_response"}
    assert len(definitions[0]["fixtures"]) == 6
    assert {
        item["configured_max_output_tokens"] for item in definitions[0]["fixtures"]
    } == {8}


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
            result = await runner.generation(
                deployment,
                ProbeKind.EXPERIENCE_SHORT,
                fixture_id="response-01",
                prompt="test",
                block_id="block",
            )
            assert result.outcome == ProbeOutcome.SUCCESS
            assert result.measurements["first_response_seconds"] is not None
            assert float(result.measurements["first_response_seconds"]) >= float(
                result.measurements["stream_start_seconds"]
            )
            assert float(result.measurements["output_speed_tps"]) > 0
            assert result.measurements["reported_completion_tokens"] == 8
            payload = json.loads(
                (
                    await database.query(
                        "SELECT measurement_json FROM probe_runs "
                        "WHERE kind='experience_short'"
                    )
                )[0]["measurement_json"]
            )
            assert "hello" not in json.dumps(payload)
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
            result = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE_SHORT,
                fixture_id="response-01",
                prompt="test",
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


def test_empty_output_and_http_failure_are_service_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        responses = [_sse_response(visible=False), _sse_response(status=503)]

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
            empty = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE_SHORT,
                prompt="test",
            )
            failed = await runner.generation(
                catalog.deployments[1],
                ProbeKind.EXPERIENCE_SHORT,
                prompt="test",
            )
            assert (empty.error_class, empty.error_code) == (
                ErrorClass.SERVICE,
                "empty_visible_output",
            )
            assert (failed.error_class, failed.error_code) == (
                ErrorClass.SERVICE,
                "http_503",
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
            no_output = await runner.generation(
                catalog.deployments[0],
                ProbeKind.EXPERIENCE_SHORT,
                prompt="test",
            )
            stalled = await runner.generation(
                catalog.deployments[1],
                ProbeKind.EXPERIENCE_SHORT,
                prompt="test",
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
            await scheduler.run_block(ProbeKind.EXPERIENCE_SHORT, 1, 4, past)
            assert len(calls) == 9
            assert len({call["fixture_id"] for call in calls}) == 1
            assert len({call["prompt"] for call in calls}) == 1
            assert len({call["block_id"] for call in calls}) == 1
            assert len({call["deployment"] for call in calls}) == 9
            block = (await database.query("SELECT * FROM collection_blocks"))[0]
            assert block["status"] == "complete"
            assert block["scheduler_lag_seconds"] >= 19
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_standard_budget_blocks_but_rapid_has_no_automatic_limit(
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
            await database.write(
                """
                INSERT INTO budget_usage(
                    deployment_id, budget_date, short_requests,
                    context_requests, output_tokens
                    ) VALUES (?, ?, 3, 0, 0)
                """,
                (deployment.deployment_id, datetime.now(UTC).date().isoformat()),
            )
            result = await runner.generation(
                deployment, ProbeKind.EXPERIENCE_SHORT, prompt="test"
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
            allowed, reason = await rapid_runner._standard_budget_available(
                deployment, ProbeKind.EXPERIENCE_SHORT, 1
            )
            assert allowed is True
            assert reason is None
        finally:
            await close_database(rapid_database, rapid_writer)

    asyncio.run(scenario())
