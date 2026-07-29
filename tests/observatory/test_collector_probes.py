from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from maas_common.catalog import ModelCatalog, load_model_catalog
from maas_observatory.collector import (
    VLLMMetricsCollector,
    classify_transport_error,
    metrics_url,
)
from maas_observatory.database import Database
from maas_observatory.models import ErrorClass, ProbeKind, ProbeOutcome, Quality
from maas_observatory.probes import ProbeRunner
from maas_observatory.settings import (
    ProbeSettings,
    ScrapeSettings,
    StorageSettings,
    load_observability_settings,
)
from tests.observatory.test_metrics import prometheus_fixture


def one_model_catalog() -> ModelCatalog:
    catalog = load_model_catalog()
    return catalog.model_copy(update={"deployments": [catalog.deployments[0]]})


def test_metrics_url_uses_origin_and_error_classification() -> None:
    assert metrics_url("https://example.test/v1", "/metrics") == (
        "https://example.test/metrics"
    )
    assert classify_transport_error(httpx.ReadTimeout("late")) == (
        ErrorClass.TRANSPORT,
        "timeout",
    )
    response = httpx.Response(503, request=httpx.Request("GET", "https://x.test"))
    error = httpx.HTTPStatusError("bad", request=response.request, response=response)
    assert classify_transport_error(error) == (ErrorClass.SERVICE, "http_503")
    assert classify_transport_error(ValueError("bad")) == (
        ErrorClass.MEASUREMENT,
        "parse",
    )


def test_collector_probe_budget_gate_and_streaming_usage(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    monkeypatch.setenv(deployment.endpoint.base_url_env, "https://model.test/v1")
    monkeypatch.setenv(deployment.endpoint.api_key_env, "private-key")
    calls = {"metrics": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            calls["metrics"] += 1
            step = calls["metrics"]
            return httpx.Response(
                200,
                text=prometheus_fixture(
                    generation=100 + 40 * step,
                    prompt=200 + 60 * step,
                    requests=20 + 20 * step,
                    bucket_count=20 + 20 * step,
                ),
            )
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": deployment.model_id}]})
        if request.url.path == "/v1/chat/completions":
            body = "\n\n".join(
                [
                    'data: {"choices":[{"delta":{"reasoning_content":"first"}}]}',
                    'data: {"choices":[{"delta":{"content":"second"}}]}',
                    (
                        'data: {"choices":[],"usage":{"prompt_tokens":128,'
                        '"completion_tokens":3}}'
                    ),
                    "data: [DONE]",
                ]
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(404)

    async def scenario() -> None:
        database = Database(StorageSettings(root=tmp_path))
        await database.migrate()
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        await database.synchronize_catalog(catalog)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = load_observability_settings()
        try:
            async with VLLMMetricsCollector(
                catalog,
                ScrapeSettings(),
                database,
                client=client,
            ) as collector:
                first = await collector.collect_one(deployment)
                second = await collector.collect_one(deployment)
                assert first.quality == Quality.EXACT
                assert second.elapsed_seconds is not None
                interval = await database.scalar(
                    """
                    SELECT interval_json FROM scrape_snapshots
                    WHERE interval_json IS NOT NULL LIMIT 1
                    """
                )
                assert json.loads(interval)["values"]["system_output_tps"] > 0

            async with ProbeRunner(
                catalog,
                ProbeSettings(),
                settings.profiles,
                database,
                client=client,
            ) as runner:
                route = await runner.route_liveness(deployment)
                assert route.outcome == ProbeOutcome.SUCCESS
                gate = await runner.load_gate(deployment)
                assert not gate.allowed
                assert gate.reason == "telemetry_unavailable"
                speed = await runner.generation(deployment, ProbeKind.SPEED, force=True)
                assert speed.outcome == ProbeOutcome.SUCCESS
                assert speed.measurements["probe_decode_tps"]
                assert speed.measurements["reported_prompt_tokens"] == 128
                usage = await database.query("SELECT * FROM budget_usage")
                assert usage[0]["short_requests"] == 1
                assert usage[0]["experience_requests"] == 1
                persisted = await database.scalar(
                    "SELECT measurement_json FROM probe_runs WHERE kind='speed'"
                )
                assert "private-key" not in persisted
                assert "bounded monitoring" not in persisted
        finally:
            await client.aclose()
            await database.stop_writer()
            await writer

    asyncio.run(scenario())


def test_missing_stream_usage_is_unavailable(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    monkeypatch.setenv(deployment.endpoint.base_url_env, "https://model.test/v1")
    monkeypatch.setenv(deployment.endpoint.api_key_env, "key")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    async def scenario() -> None:
        database = Database(StorageSettings(root=tmp_path))
        await database.migrate()
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        await database.synchronize_catalog(catalog)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = load_observability_settings()
        try:
            async with ProbeRunner(
                catalog,
                ProbeSettings(),
                settings.profiles,
                database,
                client=client,
            ) as runner:
                result = await runner.generation(
                    deployment, ProbeKind.SPEED, force=True
                )
                assert result.outcome == ProbeOutcome.UNAVAILABLE
                assert result.error_class == ErrorClass.MEASUREMENT
                assert result.error_code == "streaming_usage_missing"
        finally:
            await client.aclose()
            await database.stop_writer()
            await writer

    asyncio.run(scenario())
