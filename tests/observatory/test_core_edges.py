from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from maas_common.catalog import ModelCatalog, load_model_catalog
from maas_observatory.api import _freshness, _metric_unit, _quality
from maas_observatory.collector import (
    RollupEngine,
    VLLMMetricsCollector,
    floor_bucket,
)
from maas_observatory.database import Database, isoformat
from maas_observatory.models import (
    ErrorClass,
    Histogram,
    IntervalMetrics,
    ProbeKind,
    ProbeOutcome,
    ProbeResult,
    Quality,
    ServiceState,
    TelemetryState,
)
from maas_observatory.probes import (
    GateDecision,
    ProbeRunner,
    ProbeScheduler,
    _content_from_delta,
    _request_payload,
)
from maas_observatory.settings import (
    DailyBudget,
    ProbeSettings,
    ScrapeSettings,
    StorageSettings,
    load_observability_settings,
)
from maas_observatory.state import StateEngine
from tests.observatory.test_metrics import prometheus_fixture


def one_model_catalog(index: int = 0) -> ModelCatalog:
    catalog = load_model_catalog()
    return catalog.model_copy(update={"deployments": [catalog.deployments[index]]})


async def open_database(
    tmp_path: Path, catalog: ModelCatalog
) -> tuple[Database, asyncio.Task[None]]:
    database = Database(StorageSettings(root=tmp_path))
    await database.migrate()
    writer = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(catalog)
    return database, writer


async def close_database(database: Database, writer: asyncio.Task[None]) -> None:
    await database.stop_writer()
    await writer


def test_request_profiles_and_delta_content() -> None:
    catalog = load_model_catalog()
    thinking = catalog.deployments[0]
    payload = _request_payload(
        thinking,
        prompt="private",
        profile_id="thinking",
        max_tokens=8,
        stream=True,
    )
    assert payload["chat_template_kwargs"] == {"thinking": True}
    default_only = next(item for item in catalog.deployments if not item.profiles)
    payload = _request_payload(
        default_only,
        prompt="private",
        profile_id="default-only",
        max_tokens=8,
        stream=True,
    )
    assert "chat_template_kwargs" not in payload
    with pytest.raises(ValueError, match="undefined profile"):
        _request_payload(
            thinking,
            prompt="x",
            profile_id="missing",
            max_tokens=8,
            stream=True,
        )
    assert _content_from_delta({"content": "a", "reasoning_content": "b"}) == "ab"
    assert _content_from_delta({"content": None}) == ""
    assert _quality(0, None) == "unavailable"
    assert _quality(1, None) == "incomplete"
    assert _metric_unit("ttft_p95") == "s"
    assert _metric_unit("kv_cache_usage") == "ratio"
    assert _metric_unit("request_success_rate") == "ratio"
    assert _metric_unit("requests_running") == "requests"
    assert _freshness(None) is None


def test_collector_failures_size_limit_restore_and_cycle(
    tmp_path: Path, monkeypatch: Any
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            missing_client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200))
            )
            async with VLLMMetricsCollector(
                catalog, ScrapeSettings(), database, client=missing_client
            ) as collector:
                missing = await collector.fetch(deployment)
                assert missing.error_code == "configuration"
            await missing_client.aclose()

            monkeypatch.setenv(
                deployment.endpoint.base_url_env, "https://model.test/v1"
            )
            monkeypatch.setenv(deployment.endpoint.api_key_env, "key")
            responses = iter(
                [
                    httpx.Response(404),
                    httpx.Response(200, text="not prometheus"),
                    httpx.Response(200, content=b"x" * 3001),
                    httpx.Response(200, text=prometheus_fixture()),
                ]
            )
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: next(responses))
            )
            settings = ScrapeSettings(max_response_bytes=3000)
            async with VLLMMetricsCollector(
                catalog, settings, database, client=client
            ) as collector:
                assert (await collector.fetch(deployment)).error_code == "http_404"
                assert (await collector.fetch(deployment)).error_code == "parse"
                assert (await collector.fetch(deployment)).error_code == "parse"
                snapshots = await collector.collect_cycle()
                assert snapshots[0].quality == Quality.EXACT
            await client.aclose()

            restored_client = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, text=prometheus_fixture(120))
                )
            )
            async with VLLMMetricsCollector(
                catalog, ScrapeSettings(), database, client=restored_client
            ) as restored:
                assert (
                    deployment.deployment_id,
                    "legacy-primary",
                ) in restored._previous
            await restored_client.aclose()
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_rollup_merges_histograms_and_handles_empty_bucket(tmp_path: Path) -> None:
    catalog = one_model_catalog()
    deployment_id = catalog.deployments[0].deployment_id

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            engine = RollupEngine(database, p95_min_samples=2)
            now = floor_bucket(datetime.now(UTC), 60)
            assert not await engine.aggregate(
                deployment_id, "1m", now - timedelta(minutes=2)
            )
            for second in (5, 20):
                ended = now + timedelta(seconds=second)
                interval = IntervalMetrics(
                    deployment_id=deployment_id,
                    started_at=ended - timedelta(seconds=15),
                    ended_at=ended,
                    values={"system_output_tps": float(second)},
                    histograms={
                        "ttft": Histogram(
                            buckets={0.1: 1, 1.0: 2},
                            count=2,
                            total=1.1,
                        )
                    },
                    sample_count=2,
                    quality=Quality.EXACT,
                )
                await database.write(
                    """
                    INSERT INTO scrape_snapshots(
                        deployment_id, observed_at, quality, error_class,
                        counters_json, gauges_json, histograms_json, interval_json
                    ) VALUES (?, ?, 'exact', 'none', '{}', '{}', '{}', ?)
                    """,
                    (
                        deployment_id,
                        isoformat(ended),
                        interval.model_dump_json(),
                    ),
                )
            assert await engine.aggregate(deployment_id, "1m", now)
            payload = json.loads(
                await database.scalar("SELECT payload_json FROM rollups")
            )
            assert payload["sample_count"] == 4
            assert payload["values"]["ttft_p95"] is not None
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_load_gate_reasons_budget_and_profiles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    settings = load_observability_settings()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        )
        runner = ProbeRunner(
            catalog, ProbeSettings(), settings.profiles, database, client=client
        )
        try:
            assert (await runner.load_gate(deployment)).reason == (
                "telemetry_unavailable"
            )
            await database.write(
                """
                INSERT INTO metrics_sources(
                    deployment_id, source_id, active,
                    config_snapshot_id, updated_at
                )
                SELECT deployment_id, 'legacy-primary', 1,
                       config_snapshot_id, updated_at
                FROM deployments WHERE deployment_id=?
                """,
                (deployment.deployment_id,),
            )
            observed = datetime.now(UTC)

            async def insert(gauges: dict[str, float], *, age: int = 0) -> None:
                await database.write(
                    """
                    INSERT INTO scrape_snapshots(
                        deployment_id, observed_at, quality, error_class,
                        counters_json, gauges_json, histograms_json, interval_json
                    ) VALUES (?, ?, 'exact', 'none', '{}', ?, '{}', ?)
                    """,
                    (
                        deployment.deployment_id,
                        isoformat(observed + timedelta(microseconds=age)),
                        json.dumps(gauges),
                        json.dumps(
                            {
                                "values": {"request_success_delta": 0},
                                "sample_count": 0,
                            }
                        ),
                    ),
                )

            await insert(
                {
                    "requests_running": 1,
                    "requests_waiting": 0,
                    "kv_cache_usage": 0.1,
                },
                age=1,
            )
            assert (await runner.load_gate(deployment)).allowed
            await insert(
                {
                    "requests_running": 0,
                    "requests_waiting": 1,
                    "kv_cache_usage": 0.1,
                },
                age=2,
            )
            assert (await runner.load_gate(deployment)).reason == "requests_waiting"
            await insert(
                {
                    "requests_running": 0,
                    "requests_waiting": 0,
                    "kv_cache_usage": 0.8,
                },
                age=3,
            )
            assert (await runner.load_gate(deployment)).reason == "kv_cache"
            await insert(
                {
                    "requests_running": 0,
                    "requests_waiting": 0,
                    "kv_cache_usage": 0.1,
                },
                age=4,
            )
            assert (await runner.load_gate(deployment)).allowed
            assert (await runner.canary_eligible(deployment)).allowed
            await database.write(
                """
                INSERT INTO scrape_snapshots(
                    deployment_id, observed_at, quality, error_class,
                    counters_json, gauges_json, histograms_json, interval_json
                ) VALUES (?, ?, 'exact', 'none', '{}', ?, '{}', ?)
                """,
                (
                    deployment.deployment_id,
                    isoformat(datetime.now(UTC) - timedelta(minutes=6)),
                    json.dumps(
                        {
                            "requests_running": 0,
                            "requests_waiting": 0,
                            "kv_cache_usage": 0.1,
                        }
                    ),
                    json.dumps(
                        {
                            "values": {"request_success_delta": 0},
                            "sample_count": 0,
                        }
                    ),
                ),
            )
            assert (await runner.canary_eligible(deployment)).allowed
            assert (await runner.speed_eligible(deployment)).allowed
            now_text = isoformat()
            await database.write(
                """
                INSERT INTO probe_runs(
                    deployment_id, kind, scheduled_at, started_at, finished_at,
                    outcome, error_class, definition_version, measurement_json
                ) VALUES (?, 'speed', ?, ?, ?, 'success', 'none', '1', '{}')
                """,
                (deployment.deployment_id, now_text, now_text, now_text),
            )
            skipped = await runner.generation(deployment, ProbeKind.SPEED)
            assert skipped.outcome == ProbeOutcome.SKIPPED
            assert skipped.error_code == "minimum_interval"
            assert runner.deployment(deployment.alias) == deployment
            with pytest.raises(ValueError, match="unknown deployment"):
                runner.deployment("not-real")

            budget = ProbeSettings(
                daily_budget=DailyBudget(
                    short_requests=0,
                    context_requests=0,
                    canary_requests=0,
                    experience_requests=0,
                    output_tokens=0,
                    input_tokens=0,
                )
            )
            limited = ProbeRunner(
                catalog, budget, settings.profiles, database, client=client
            )
            assert (
                await limited.budget_available(deployment, ProbeKind.SPEED)
            ).reason == ("daily_experience_budget")
            await database.write(
                """
                INSERT INTO budget_usage(
                    deployment_id, budget_date, short_requests,
                    context_requests, canary_requests, experience_requests,
                    input_tokens, output_tokens
                ) VALUES (?, ?, 48, 4, 96, 1, 25000, 3584)
                ON CONFLICT(deployment_id, budget_date) DO UPDATE SET
                    short_requests=48, context_requests=4,
                    canary_requests=96, experience_requests=1,
                    input_tokens=25000, output_tokens=3584
                """,
                (
                    deployment.deployment_id,
                    datetime.now(UTC).date().isoformat(),
                ),
            )
            normal_budget = ProbeRunner(
                catalog, ProbeSettings(), settings.profiles, database, client=client
            )
            assert (
                await normal_budget.budget_available(deployment, ProbeKind.SPEED)
            ).reason == "daily_output_token_budget"

            missing_profile = ProbeRunner(
                catalog, ProbeSettings(), {}, database, client=client
            )
            with pytest.raises(ValueError, match="no operational profile"):
                missing_profile.profile_for(deployment)
            result = await missing_profile.generation(
                deployment, ProbeKind.SPEED, force=True
            )
            assert result.error_code == "profile_undefined"
            with pytest.raises(ValueError, match="unsupported"):
                await runner.generation(deployment, ProbeKind.ROUTE)
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_route_and_generation_failure_classification(
    tmp_path: Path, monkeypatch: Any
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    monkeypatch.setenv(deployment.endpoint.base_url_env, "https://model.test/v1")
    monkeypatch.setenv(deployment.endpoint.api_key_env, "key")
    settings = load_observability_settings()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        responses = iter(
            [
                httpx.Response(200, json={"wrong": []}),
                httpx.Response(200, text="data: [DONE]\n\n"),
                httpx.Response(200, text="data: not-json\n\n"),
            ]
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: next(responses))
        )
        try:
            async with ProbeRunner(
                catalog,
                ProbeSettings(),
                settings.profiles,
                database,
                client=client,
            ) as runner:
                route = await runner.route_liveness(deployment)
                assert route.error_code == "protocol_invalid"
                empty = await runner.generation(
                    deployment, ProbeKind.CANARY, force=True
                )
                assert empty.error_code == "empty_output"
                malformed = await runner.generation(
                    deployment, ProbeKind.CANARY, force=True
                )
                assert malformed.error_code == "protocol_invalid"
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_missing_probe_configuration_is_measurement_error(tmp_path: Path) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    settings = load_observability_settings()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            async with ProbeRunner(
                catalog, ProbeSettings(), settings.profiles, database
            ) as runner:
                route = await runner.route_liveness(deployment)
                assert route.error_class == ErrorClass.MEASUREMENT
                assert route.error_code == "configuration"
                generation = await runner.generation(
                    deployment, ProbeKind.CANARY, force=True
                )
                assert generation.error_class == ErrorClass.MEASUREMENT
                assert generation.error_code == "configuration"
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_database_writer_error_schema_guard_scalar_and_backup_pruning(
    tmp_path: Path,
) -> None:
    catalog = one_model_catalog()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            with pytest.raises(sqlite3.OperationalError):
                await database.write("INSERT INTO missing_table VALUES (1)")
            assert await database.scalar("SELECT 1 WHERE 0") is None
            now = datetime.now(UTC)
            await database.backup(now=now)
            await database.backup(now=now - timedelta(days=40))
            await database.prune_backups(now=now)
            assert len(list(database.backup_dir.glob("*.sqlite3"))) == 1
        finally:
            await close_database(database, writer)

        connection = sqlite3.connect(database.path)
        connection.execute("PRAGMA user_version=999")
        connection.close()
        with pytest.raises(RuntimeError, match="newer"):
            await database.migrate()

    asyncio.run(scenario())


def test_collector_and_rollup_run_loops_stop_cleanly(tmp_path: Path) -> None:
    catalog = one_model_catalog()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            async with VLLMMetricsCollector(
                catalog, ScrapeSettings(), database
            ) as collector:
                stop = asyncio.Event()

                async def collect_once() -> list[Any]:
                    stop.set()
                    return []

                collector.collect_cycle = collect_once  # type: ignore[method-assign]
                await collector.run(stop)

            engine = RollupEngine(database, p95_min_samples=20)
            stop = asyncio.Event()

            async def aggregate_once(*args: Any) -> bool:
                stop.set()
                return False

            engine.aggregate = aggregate_once  # type: ignore[method-assign]
            await engine.run(stop, [catalog.deployments[0].deployment_id])
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_state_unavailable_degraded_and_recovery_paths(tmp_path: Path) -> None:
    catalog = one_model_catalog()
    deployment_id = catalog.deployments[0].deployment_id

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        engine = StateEngine(catalog, load_observability_settings().state, database)
        try:
            now = datetime.now(UTC)
            await database.write(
                """
                INSERT INTO scrape_snapshots(
                    deployment_id, observed_at, quality, error_class,
                    counters_json, gauges_json, histograms_json
                ) VALUES (?, ?, 'exact', 'none', '{}', ?, '{}')
                """,
                (
                    deployment_id,
                    isoformat(now),
                    json.dumps(
                        {
                            "requests_running": 0,
                            "requests_waiting": 0,
                            "kv_cache_usage": 0.1,
                        }
                    ),
                ),
            )
            for offset in (2, 1):
                await database.write(
                    """
                    INSERT INTO probe_runs(
                        deployment_id, kind, scheduled_at, started_at, finished_at,
                        outcome, error_class, error_code, definition_version,
                        measurement_json
                    ) VALUES (?, 'canary', ?, ?, ?, 'failed', 'service_error',
                              'http_500', '1', '{}')
                    """,
                    (
                        deployment_id,
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                    ),
                )
            service, _, reasons = await engine.evaluate(deployment_id)
            assert service == ServiceState.UNAVAILABLE
            assert reasons == ["consecutive_generation_service_failures"]

            await database.write("DELETE FROM probe_runs")
            for offset in (2, 1):
                await database.write(
                    """
                    INSERT INTO probe_runs(
                        deployment_id, kind, scheduled_at, started_at, finished_at,
                        outcome, error_class, error_code, definition_version,
                        measurement_json
                    ) VALUES (?, 'speed', ?, ?, ?, 'failed', 'service_error',
                              'empty_output', '1', '{}')
                    """,
                    (
                        deployment_id,
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                    ),
                )
            service, _, reasons = await engine.evaluate(deployment_id)
            assert service == ServiceState.DEGRADED
            assert reasons == ["consecutive_invalid_generation"]
            await database.write("DELETE FROM probe_runs")
            service, telemetry, _ = await engine.evaluate(deployment_id)
            assert service == ServiceState.OPERATIONAL
            assert telemetry == TelemetryState.FRESH

            await engine.persist(
                deployment_id,
                ServiceState.MAINTENANCE,
                TelemetryState.FRESH,
                ["operator"],
            )
            service, _, _ = await engine.evaluate(deployment_id)
            assert service == ServiceState.MAINTENANCE
            await database.write("DELETE FROM current_states")

            for offset in (3, 2, 1):
                await database.write(
                    """
                    INSERT INTO probe_runs(
                        deployment_id, kind, scheduled_at, started_at, finished_at,
                        outcome, error_class, error_code, definition_version,
                        measurement_json
                    ) VALUES (?, 'route', ?, ?, ?, 'failed', 'transport_error',
                              'timeout', '1', '{}')
                    """,
                    (
                        deployment_id,
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                        isoformat(now - timedelta(seconds=offset)),
                    ),
                )
            latest_route = await database.scalar(
                "SELECT id FROM probe_runs ORDER BY finished_at DESC LIMIT 1"
            )
            await database.write(
                """
                INSERT INTO probe_runs(
                    deployment_id, kind, scheduled_at, started_at, finished_at,
                    outcome, error_class, error_code, definition_version,
                    confirmation_of, measurement_json
                ) VALUES (?, 'confirmation', ?, ?, ?, 'failed', 'transport_error',
                          'timeout', '1', ?, '{}')
                """,
                (
                    deployment_id,
                    isoformat(now),
                    isoformat(now),
                    isoformat(now),
                    latest_route,
                ),
            )
            assert await engine._unavailable(deployment_id) == (
                "route_and_generation_confirmation_failed"
            )

            await database.write("DELETE FROM probe_runs")
            for index in range(20):
                failed = index < 2
                await database.write(
                    """
                    INSERT INTO probe_runs(
                        deployment_id, kind, scheduled_at, started_at, finished_at,
                        outcome, error_class, error_code, definition_version,
                        measurement_json
                    ) VALUES (?, 'route', ?, ?, ?, ?, ?, ?, '1', '{}')
                    """,
                    (
                        deployment_id,
                        isoformat(now + timedelta(microseconds=index)),
                        isoformat(now + timedelta(microseconds=index)),
                        isoformat(now + timedelta(microseconds=index)),
                        "failed" if failed else "success",
                        "service_error" if failed else "none",
                        "http_500" if failed else None,
                    ),
                )
            assert await engine._degraded(deployment_id) == "service_error_rate"

            await database.write("DELETE FROM probe_runs")
            await database.write("DELETE FROM scrape_snapshots")
            service, telemetry, _ = await engine.evaluate(deployment_id)
            assert service == ServiceState.UNKNOWN
            assert telemetry == TelemetryState.UNAVAILABLE
            await database.write(
                """
                INSERT INTO scrape_snapshots(
                    deployment_id, observed_at, quality, error_class, error_code,
                    counters_json, gauges_json, histograms_json
                ) VALUES (?, ?, 'unavailable', 'transport_error', 'timeout',
                          '{}', '{}', '{}')
                """,
                (deployment_id, isoformat()),
            )
            service, telemetry, _ = await engine.evaluate(deployment_id)
            assert service == ServiceState.UNKNOWN
            assert telemetry == TelemetryState.PARTIAL
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


class FakeSchedulerDatabase:
    def __init__(self) -> None:
        self.index: int | None = None

    async def query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        if "scheduler_state" in sql and self.index is not None:
            return [{"value_json": json.dumps(self.index)}]
        return []

    async def write(self, sql: str, params: Any = ()) -> int:
        self.index = int(json.loads(params[0]))
        return 1


class FakeRunner:
    def __init__(self, stop: asyncio.Event) -> None:
        self.catalog = one_model_catalog()
        self.settings = ProbeSettings(
            route_interval_seconds=10,
            speed_dispatch_interval_seconds=10,
        )
        self.stop = stop
        self.calls: list[ProbeKind] = []

    async def route_liveness(self, deployment: Any) -> None:
        self.stop.set()

    async def canary_eligible(self, deployment: Any) -> GateDecision:
        return GateDecision(True)

    async def generation(self, deployment: Any, kind: ProbeKind) -> None:
        self.calls.append(kind)
        self.stop.set()


def test_scheduler_round_robin_and_single_iterations() -> None:
    async def scenario() -> None:
        database = FakeSchedulerDatabase()
        stop = asyncio.Event()
        runner = FakeRunner(stop)
        scheduler = ProbeScheduler(runner, database)  # type: ignore[arg-type]
        assert await scheduler._round_robin_index() == 0
        await scheduler._save_round_robin_index(3)
        assert await scheduler._round_robin_index() == 3
        await scheduler.route_loop(stop)
        stop.clear()
        await scheduler.canary_loop(stop)
        assert runner.calls == [ProbeKind.CANARY]
        stop.clear()
        await scheduler.speed_loop(stop)
        assert runner.calls[-1] == ProbeKind.EXPERIENCE_SHORT

    asyncio.run(scenario())


def test_isoformat_default_and_probe_result_schema() -> None:
    assert isoformat().endswith("+00:00")
    now = datetime.now(UTC)
    result = ProbeResult(
        deployment_id="d",
        kind=ProbeKind.ROUTE,
        scheduled_at=now,
        started_at=now,
        finished_at=now,
        outcome=ProbeOutcome.SUCCESS,
        error_class=ErrorClass.NONE,
    )
    assert result.definition_version == "1"
    assert floor_bucket(now, 60).second == 0
