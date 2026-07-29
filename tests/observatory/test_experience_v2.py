from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from maas_common.catalog import ModelCatalog, load_model_catalog
from maas_observatory.api import RuntimeHealth, create_app
from maas_observatory.collector import RollupEngine, floor_bucket
from maas_observatory.database import Database, isoformat
from maas_observatory.metrics import derive_interval
from maas_observatory.models import (
    IntervalMetrics,
    MetricSnapshot,
    ProbeKind,
    ProbeOutcome,
    Quality,
)
from maas_observatory.probes import ProbeRunner, profile_definitions
from maas_observatory.settings import (
    ExperienceSettings,
    MetricsSourceSettings,
    ProbeSettings,
    StorageSettings,
    load_observability_settings,
)


def one_model_catalog() -> ModelCatalog:
    catalog = load_model_catalog()
    return catalog.model_copy(update={"deployments": [catalog.deployments[0]]})


async def open_database(
    path: Path, catalog: ModelCatalog
) -> tuple[Database, asyncio.Task[None]]:
    database = Database(StorageSettings(root=path))
    await database.migrate()
    writer = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(catalog)
    return database, writer


async def close_database(database: Database, writer: asyncio.Task[None]) -> None:
    await database.stop_writer()
    await writer


def test_counter_deltas_are_source_scoped() -> None:
    start = datetime.now(UTC)
    source_a_1 = MetricSnapshot(
        deployment_id="d",
        source_id="a",
        observed_at=start,
        counters={"generation_tokens": 100},
    )
    source_a_2 = source_a_1.model_copy(
        update={
            "observed_at": start + timedelta(seconds=10),
            "counters": {"generation_tokens": 200},
        }
    )
    source_b = source_a_2.model_copy(update={"source_id": "b"})
    interval = derive_interval(source_a_2, source_a_1, p95_min_samples=20)
    assert interval.values["aggregate_output_tps"] == 10
    try:
        derive_interval(source_b, source_a_1, p95_min_samples=20)
    except ValueError as exc:
        assert "sources" in str(exc)
    else:
        raise AssertionError("cross-source delta must be rejected")


def test_rollup_sums_instance_rates_and_reports_coverage(tmp_path: Path) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        try:
            now = floor_bucket(datetime.now(UTC), 60)
            await database.synchronize_metrics_sources(
                catalog,
                {
                    deployment.alias: [
                        MetricsSourceSettings(
                            source_id="a",
                            url_env="TEST_SOURCE_A_URL",
                            api_key_env="TEST_SOURCE_API_KEY",
                        ),
                        MetricsSourceSettings(
                            source_id="b",
                            url_env="TEST_SOURCE_B_URL",
                            api_key_env="TEST_SOURCE_API_KEY",
                        ),
                    ]
                },
            )
            await database.synchronize_metrics_sources(
                catalog,
                {
                    deployment.alias: [
                        MetricsSourceSettings(
                            source_id="a",
                            url_env="TEST_SOURCE_A_URL",
                            api_key_env="TEST_SOURCE_API_KEY",
                        ),
                        MetricsSourceSettings(
                            source_id="b",
                            url_env="TEST_SOURCE_B_URL",
                            api_key_env="TEST_SOURCE_API_KEY",
                        ),
                    ]
                },
            )
            for source_id, rate, running, kv in (
                ("a", 10.0, 2.0, 0.2),
                ("b", 20.0, 3.0, 0.6),
            ):
                ended = now + timedelta(seconds=30)
                interval = IntervalMetrics(
                    deployment_id=deployment.deployment_id,
                    source_id=source_id,
                    started_at=ended - timedelta(seconds=15),
                    ended_at=ended,
                    values={
                        "aggregate_output_tps": rate,
                        "requests_running": running,
                        "requests_waiting": 0,
                        "kv_cache_usage": kv,
                    },
                    quality=Quality.EXACT,
                )
                await database.write(
                    """
                    INSERT INTO scrape_snapshots(
                        deployment_id, source_id, observed_at, quality,
                        error_class, counters_json, gauges_json,
                        histograms_json, interval_json
                    ) VALUES (?, ?, ?, 'exact', 'none', '{}', '{}', '{}', ?)
                    """,
                    (
                        deployment.deployment_id,
                        source_id,
                        isoformat(ended),
                        interval.model_dump_json(),
                    ),
                )
            engine = RollupEngine(database, p95_min_samples=20)
            assert await engine.aggregate(deployment.deployment_id, "1m", now)
            row = (
                await database.query(
                    """
                    SELECT payload_json, quality, expected_source_count,
                           observed_source_count, source_seconds_coverage
                    FROM rollups WHERE deployment_id=?
                    """,
                    (deployment.deployment_id,),
                )
            )[0]
            payload = json.loads(row["payload_json"])
            assert payload["values"]["aggregate_output_tps"] == 30
            assert payload["values"]["requests_running"] == 5
            assert payload["values"]["kv_cache_usage"] == 0.6
            assert row["quality"] == "exact"
            assert row["expected_source_count"] == row["observed_source_count"] == 2
            assert row["source_seconds_coverage"] == 1
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_running_load_is_allowed_but_waiting_and_context_kv_are_gated(
    tmp_path: Path,
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    settings = load_observability_settings()

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        )
        try:
            snapshot_id = await database.scalar(
                "SELECT config_snapshot_id FROM deployments WHERE deployment_id=?",
                (deployment.deployment_id,),
            )
            await database.write(
                """
                INSERT INTO metrics_sources(
                    deployment_id, source_id, config_snapshot_id, updated_at
                ) VALUES (?, 'a', ?, ?)
                """,
                (deployment.deployment_id, snapshot_id, isoformat()),
            )

            async def snapshot(waiting: int, kv: float) -> None:
                await database.write(
                    """
                    INSERT INTO scrape_snapshots(
                        deployment_id, source_id, observed_at, quality,
                        error_class, counters_json, gauges_json, histograms_json
                    ) VALUES (?, 'a', ?, 'exact', 'none', '{}', ?, '{}')
                    """,
                    (
                        deployment.deployment_id,
                        isoformat(),
                        json.dumps(
                            {
                                "requests_running": 7,
                                "requests_waiting": waiting,
                                "kv_cache_usage": kv,
                            }
                        ),
                    ),
                )

            runner = ProbeRunner(
                catalog,
                ProbeSettings(),
                settings.profiles,
                database,
                settings.experience,
                client=client,
            )
            await snapshot(0, 0.4)
            assert (await runner.load_gate(deployment)).allowed
            assert (await runner.load_gate(deployment, context=True)).allowed
            await snapshot(1, 0.4)
            assert (await runner.load_gate(deployment)).reason == "requests_waiting"
            await snapshot(0, 0.6)
            assert (await runner.load_gate(deployment)).allowed
            assert (await runner.load_gate(deployment, context=True)).reason == (
                "kv_cache"
            )
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_experience_probe_records_observer_path_measurements(
    tmp_path: Path, monkeypatch: object
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    monkeypatch.setenv(deployment.endpoint.base_url_env, "https://model.test/v1")
    monkeypatch.setenv(deployment.endpoint.api_key_env, "secret")
    body = "\n\n".join(
        (
            'data: {"choices":[{"delta":{"reasoning_content":"r"}}]}',
            'data: {"choices":[{"delta":{"content":"visible"}}]}',
            (
                'data: {"choices":[{"finish_reason":"length","delta":{}}],'
                '"usage":{"prompt_tokens":72,"completion_tokens":3}}'
            ),
            "data: [DONE]",
        )
    )

    async def scenario() -> None:
        database, writer = await open_database(tmp_path, catalog)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, text=body, headers={"content-type": "text/event-stream"}
                )
            )
        )
        try:
            settings = load_observability_settings()
            async with ProbeRunner(
                catalog,
                ProbeSettings(),
                settings.profiles,
                database,
                settings.experience,
                client=client,
            ) as runner:
                result = await runner.generation(
                    deployment, ProbeKind.EXPERIENCE_SHORT, force=True
                )
            assert result.outcome == ProbeOutcome.SUCCESS
            assert result.vantage_id == "observatory-primary"
            assert result.measurements["reported_prompt_tokens"] == 72
            assert result.measurements["client_ttft_seconds"] is not None
            assert result.measurements["first_visible_content_seconds"] is not None
            assert result.measurements["client_e2e_seconds"] is not None
            assert result.measurements["steady_state_output_tps"] is not None
            persisted = await database.scalar(
                "SELECT measurement_json FROM probe_runs LIMIT 1"
            )
            assert '"visible"' not in persisted
            assert "secret" not in persisted
        finally:
            await client.aclose()
            await close_database(database, writer)

    asyncio.run(scenario())


def test_profile_definitions_are_versioned_and_hashed() -> None:
    definitions = profile_definitions(ExperienceSettings())
    assert {item["profile_id"] for item in definitions} == {
        "interactive-short-v1",
        "context-16k-v1",
    }
    assert all(len(item["fixture_sha256"]) == 64 for item in definitions)
    assert all("prompt" not in item for item in definitions)


def test_experience_api_quantiles_latest_series_profiles_and_compare(
    tmp_path: Path,
) -> None:
    catalog = one_model_catalog()
    deployment = catalog.deployments[0]
    settings = load_observability_settings()

    async def setup() -> Database:
        database, writer = await open_database(tmp_path, catalog)
        definitions = profile_definitions(settings.experience)
        for definition in definitions:
            await database.write(
                """
                INSERT INTO experience_profiles(
                    profile_id, definition_version, fixture_sha256,
                    definition_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    definition["profile_id"],
                    definition["definition_version"],
                    definition["fixture_sha256"],
                    json.dumps(definition),
                    isoformat(),
                ),
            )
        now = datetime.now(UTC)
        for index, (ttft, tps, e2e) in enumerate(
            ((0.4, 10.0, 5.0), (0.6, 12.0, 6.0), (0.8, 14.0, 7.0))
        ):
            timestamp = isoformat(now - timedelta(minutes=3 - index))
            await database.write(
                """
                INSERT INTO probe_runs(
                    deployment_id, kind, scheduled_at, started_at, finished_at,
                    outcome, error_class, profile_id, definition_version,
                    vantage_id, measurement_json
                ) VALUES (?, 'experience_short', ?, ?, ?, 'success', 'none',
                          'interactive-short-v1', '1',
                          'observatory-primary', ?)
                """,
                (
                    deployment.deployment_id,
                    timestamp,
                    timestamp,
                    timestamp,
                    json.dumps(
                        {
                            "client_ttft_seconds": ttft,
                            "first_visible_content_seconds": ttft + 0.1,
                            "steady_state_output_tps": tps,
                            "client_e2e_seconds": e2e,
                            "stream_event_gap_p95_seconds": 0.2,
                            "reported_prompt_tokens": 72,
                            "reported_completion_tokens": 64,
                        }
                    ),
                ),
            )
        context_time = isoformat(now - timedelta(hours=1))
        await database.write(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, profile_id, definition_version,
                vantage_id, measurement_json
            ) VALUES (?, 'experience_context', ?, ?, ?, 'success', 'none',
                      'context-16k-v1', '1', 'observatory-primary', ?)
            """,
            (
                deployment.deployment_id,
                context_time,
                context_time,
                context_time,
                json.dumps(
                    {
                        "client_ttft_seconds": 2.0,
                        "steady_state_output_tps": 9.0,
                        "client_e2e_seconds": 20.0,
                        "reported_prompt_tokens": 4096,
                        "reported_completion_tokens": 128,
                    }
                ),
            ),
        )
        skipped_at = isoformat(now + timedelta(seconds=1))
        await database.write(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, error_code, profile_id,
                definition_version, vantage_id, measurement_json
            ) VALUES (?, 'experience_short', ?, ?, ?, 'skipped', 'none',
                      'requests_waiting', 'interactive-short-v1', '1',
                      'observatory-primary', '{}')
            """,
            (
                deployment.deployment_id,
                skipped_at,
                skipped_at,
                skipped_at,
            ),
        )
        await close_database(database, writer)
        return database

    database = asyncio.run(setup())
    health = RuntimeHealth()
    health.ready = True
    app = create_app(
        database,
        catalog,
        settings,
        health,
        frontend_dir=tmp_path / "not-built",
    )
    with TestClient(app) as client:
        overview = client.get(
            "/api/v1/experience/overview",
            params={"profile": "interactive-short-v1", "window": "24h"},
        )
        assert overview.status_code == 200
        item = overview.json()["data"][0]
        assert item["quality"] == "exact"
        assert item["ttft_p50"] == 0.6
        assert item["streaming_tps_p50"] == 12
        assert item["e2e_p90"] > 6
        assert item["path_success_rate"] == 1
        assert item["latest_attempt_reason"] == "busy"

        latest = client.get(
            f"/api/v1/deployments/{deployment.deployment_id}/experience/latest"
        )
        assert latest.status_code == 200
        assert latest.json()["data"]["latest"]["steady_state_output_tps"] == 14

        series = client.get(
            f"/api/v1/deployments/{deployment.deployment_id}/experience/series",
            params={"profile": "interactive-short-v1", "window": "24h"},
        )
        assert series.status_code == 200
        assert len(series.json()["data"]["points"]) == 4
        assert series.json()["data"]["points"][-1]["reason"] == "busy"

        profiles = client.get("/api/v1/experience/profiles")
        assert profiles.status_code == 200
        serialized_profiles = json.dumps(profiles.json())
        assert "fixture_sha256" in serialized_profiles
        assert "MaaS Observatory deterministic" not in serialized_profiles

        compare = client.get("/api/v1/compare?window=24h")
        assert compare.status_code == 200
        assert compare.json()["data"][0]["value"] == 12
        assert compare.json()["data"][0]["vantage_id"] == "observatory-primary"

        context = client.get(
            "/api/v1/experience/overview",
            params={"profile": "context-16k-v1", "window": "24h"},
        )
        assert context.status_code == 200
        assert context.json()["data"][0]["quality"] == "incomplete"

        assert (
            client.get(
                "/api/v1/experience/overview",
                params={"profile": "not-real"},
            ).status_code
            == 400
        )
        assert (
            client.get("/api/v1/deployments/not-real/experience/latest").status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/deployments/{deployment.deployment_id}/experience/series",
                params={"profile": "not-real"},
            ).status_code
            == 400
        )
        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["api_schema_version"] == "2"
        assert meta["observer_vantage"] == "observatory-primary"
        assert meta["deprecated_fields"]["observed_decode_tps"] is None
