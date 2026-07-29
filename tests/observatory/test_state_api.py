from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from maas_common.catalog import ModelCatalog, load_model_catalog
from maas_observatory.api import RuntimeHealth, create_app
from maas_observatory.database import Database
from maas_observatory.models import ServiceState, TelemetryState
from maas_observatory.settings import (
    StorageSettings,
    load_observability_settings,
)
from maas_observatory.state import StateEngine


def one_model_catalog() -> ModelCatalog:
    catalog = load_model_catalog()
    return catalog.model_copy(update={"deployments": [catalog.deployments[0]]})


async def prepare_public_database(
    tmp_path: Path,
) -> tuple[Database, ModelCatalog, asyncio.Task[None]]:
    catalog = one_model_catalog()
    database = Database(StorageSettings(root=tmp_path))
    await database.migrate()
    writer = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(catalog)
    deployment_id = catalog.deployments[0].deployment_id
    now = datetime.now(UTC)
    for offset in (30, 15, 0):
        observed = now - timedelta(seconds=offset)
        interval = {
            "deployment_id": deployment_id,
            "started_at": (observed - timedelta(seconds=15)).isoformat(),
            "ended_at": observed.isoformat(),
            "values": {
                "aggregate_output_tps": 2.5,
                "requests_running": 0,
                "requests_waiting": 1,
                "kv_cache_usage": 0.2,
                "request_success_delta": 0,
            },
            "histograms": {},
            "sample_count": 0,
            "quality": "exact",
            "reason": None,
        }
        await database.write(
            """
            INSERT INTO scrape_snapshots(
                deployment_id, observed_at, quality, error_class,
                counters_json, gauges_json, histograms_json, interval_json
            ) VALUES (?, ?, 'exact', 'none', '{}', ?, '{}', ?)
            """,
            (
                deployment_id,
                observed.isoformat(),
                json.dumps(
                    {
                        "requests_running": 0,
                        "requests_waiting": 1,
                        "kv_cache_usage": 0.2,
                    }
                ),
                json.dumps(interval),
            ),
        )
    return database, catalog, writer


def test_state_queue_rule_history_and_event_deduplication(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, catalog, writer = await prepare_public_database(tmp_path)
        try:
            engine = StateEngine(catalog, load_observability_settings().state, database)
            service, telemetry, reasons = await engine.evaluate(
                catalog.deployments[0].deployment_id
            )
            assert service == ServiceState.SLOW
            assert telemetry == TelemetryState.FRESH
            assert reasons == ["persistent_waiting_queue"]
            await engine.persist(
                catalog.deployments[0].deployment_id,
                service,
                telemetry,
                reasons,
            )
            await engine.persist(
                catalog.deployments[0].deployment_id,
                service,
                telemetry,
                reasons,
            )
            assert await database.scalar("SELECT COUNT(*) FROM events") == 1
            assert await database.scalar("SELECT COUNT(*) FROM state_history") == 1
        finally:
            await database.stop_writer()
            await writer

    asyncio.run(scenario())


def test_telemetry_age_thresholds(tmp_path: Path) -> None:
    database = Database(StorageSettings(root=tmp_path))
    engine = StateEngine(
        one_model_catalog(), load_observability_settings().state, database
    )
    now = datetime.now(UTC)
    assert engine.telemetry_state(now) == TelemetryState.FRESH
    assert engine.telemetry_state(now - timedelta(seconds=50)) == (
        TelemetryState.PARTIAL
    )
    assert engine.telemetry_state(now - timedelta(seconds=90)) == (TelemetryState.STALE)
    assert engine.telemetry_state(now - timedelta(minutes=6)) == (
        TelemetryState.UNAVAILABLE
    )
    assert engine.telemetry_state(None) == TelemetryState.UNAVAILABLE


def test_public_api_contract_etag_head_limits_and_secret_scan(
    tmp_path: Path,
) -> None:
    async def setup() -> tuple[Database, ModelCatalog]:
        database, catalog, writer = await prepare_public_database(tmp_path)
        engine = StateEngine(catalog, load_observability_settings().state, database)
        await engine.evaluate_all()
        now = datetime.now(UTC)
        deployment_id = catalog.deployments[0].deployment_id
        await database.write(
            """
            INSERT INTO rollups(
                deployment_id, resolution, bucket_at, payload_json,
                sample_count, quality, source_mix_json, histogram_delta_json
            ) VALUES (?, '1m', ?, ?, 2, 'exact', '{}', '{}')
            """,
            (
                deployment_id,
                now.isoformat(),
                json.dumps(
                    {
                        "values": {"aggregate_output_tps": 3.5},
                        "sample_count": 2,
                        "quality": "exact",
                    }
                ),
            ),
        )
        await database.write(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, profile_id, definition_version,
                measurement_json
                ) VALUES (?, 'experience_short', ?, ?, ?, 'success', 'none',
                          'interactive-short-v1', '1', ?)
            """,
            (
                deployment_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                json.dumps(
                    {
                        "steady_state_output_tps": 12.5,
                        "client_ttft_seconds": 0.4,
                        "client_e2e_seconds": 5.0,
                        "reported_prompt_tokens": 128,
                        "reported_completion_tokens": 64,
                    }
                ),
            ),
        )
        await database.write(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, error_code, definition_version,
                measurement_json
                ) VALUES (?, 'experience_short', ?, ?, ?, 'skipped', 'none',
                      'requests_running', '1', '{}')
            """,
            (
                deployment_id,
                (now + timedelta(seconds=1)).isoformat(),
                (now + timedelta(seconds=1)).isoformat(),
                (now + timedelta(seconds=1)).isoformat(),
            ),
        )
        await database.stop_writer()
        await writer
        return database, catalog

    database, catalog = asyncio.run(setup())
    health = RuntimeHealth()
    health.ready = True
    health.detail = "ready"
    app = create_app(database, catalog, load_observability_settings(), health)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        catalog_response = client.get("/api/v1/catalog")
        assert catalog_response.status_code == 200
        assert catalog_response.json()["sample_count"] == 1
        etag = catalog_response.headers["etag"]
        assert (
            client.get("/api/v1/catalog", headers={"If-None-Match": etag}).status_code
            == 304
        )
        head = client.head("/api/v1/catalog")
        assert head.status_code == 200
        assert head.content == b""

        overview = client.get("/api/v1/overview?window=24h")
        assert overview.json()["data"][0]["service_state"] == "slow"
        series = client.get(
            f"/api/v1/deployments/{catalog.deployments[0].deployment_id}/series",
            params={
                "metric": "aggregate_output_tps",
                "window": "24h",
                "resolution": "15s",
            },
        )
        assert len(series.json()["data"]["points"]) == 3
        invalid = client.get(
            f"/api/v1/deployments/{catalog.deployments[0].deployment_id}/series",
            params={"window": "7d", "resolution": "15s"},
        )
        assert invalid.status_code == 400
        rolled_up = client.get(
            f"/api/v1/deployments/{catalog.deployments[0].deployment_id}/series",
            params={"resolution": "1m"},
        )
        assert rolled_up.json()["data"]["points"][0]["value"] == 3.5
        invalid_metric = client.get(
            f"/api/v1/deployments/{catalog.deployments[0].deployment_id}/series",
            params={"metric": "secret_metric"},
        )
        assert invalid_metric.status_code == 400
        compare = client.get("/api/v1/compare")
        assert compare.status_code == 200
        assert compare.json()["data"][0]["value"] == 12.5
        assert compare.json()["data"][0]["latest_attempt_outcome"] == "skipped"
        assert compare.json()["data"][0]["latest_attempt_reason"] == "busy"
        assert compare.json()["data"][0]["sample_count"] == 1
        assert client.get("/api/v1/events").status_code == 200
        assert client.get("/api/v1/meta").status_code == 200
        assert client.head("/healthz").status_code == 200
        assert client.head("/readyz").status_code == 200
        serialized = json.dumps(
            {
                "catalog": catalog_response.json(),
                "overview": overview.json(),
                "series": series.json(),
            }
        )
        assert "api_key" not in serialized.lower()
        assert "base_url" not in serialized.lower()
        assert "https://" not in serialized


def test_not_ready_and_unknown_deployment(tmp_path: Path) -> None:
    async def setup() -> tuple[Database, ModelCatalog]:
        database = Database(StorageSettings(root=tmp_path))
        await database.migrate()
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        catalog = one_model_catalog()
        await database.synchronize_catalog(catalog)
        await database.stop_writer()
        await writer
        return database, catalog

    database, catalog = asyncio.run(setup())
    health = RuntimeHealth()
    app = create_app(
        database,
        catalog,
        load_observability_settings(),
        health,
        frontend_dir=tmp_path / "not-built",
    )
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 503
        assert client.get("/").json()["frontend"] == "not_built"
        assert client.head("/").status_code == 200
        pending = client.get("/api/v1/compare").json()["data"][0]
        assert pending["value"] is None
        assert pending["latest_attempt_outcome"] is None
        assert pending["latest_attempt_reason"] == "awaiting_turn"
        response = client.get(
            "/api/v1/deployments/not-real/series",
            params={"metric": "aggregate_output_tps"},
        )
        assert response.status_code == 404


def test_built_frontend_is_served_without_changing_api_routes(tmp_path: Path) -> None:
    async def setup() -> tuple[Database, ModelCatalog]:
        database = Database(StorageSettings(root=tmp_path / "database"))
        await database.migrate()
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        catalog = one_model_catalog()
        await database.synchronize_catalog(catalog)
        await database.stop_writer()
        await writer
        return database, catalog

    frontend = tmp_path / "frontend"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        "<!doctype html><title>MaaS Observatory</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    database, catalog = asyncio.run(setup())
    app = create_app(
        database,
        catalog,
        load_observability_settings(),
        RuntimeHealth(),
        frontend_dir=frontend,
    )
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "MaaS Observatory" in index.text
        assert index.headers["cache-control"] == "no-cache"
        assert client.head("/").content == b""
        assert client.get("/assets/app.js").text == "export {};"
        assert client.get("/api/v1/meta").status_code == 200
