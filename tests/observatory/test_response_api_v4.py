from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from maas_observatory.api import RuntimeHealth, create_app
from maas_observatory.database import isoformat
from maas_observatory.models import ResponseState
from maas_observatory.state import StateEngine
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    insert_probe,
    make_settings,
    open_database,
)


def test_response_state_transitions_and_measurement_errors(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        engine = StateEngine(catalog, settings, database)
        deployment_id = catalog.deployments[0].deployment_id
        try:
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert (state, reasons) == (
                ResponseState.COLLECTING,
                ["first_check_scheduled"],
            )
            old = isoformat(datetime.now(UTC) - timedelta(hours=1))
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=old,
            )
            await insert_probe(database, deployment_id)
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.DELAYED
            assert reasons == ["route_check_delayed"]

            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
            )
            state, _, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.CURRENT

            # Generation failures no longer flip the pill: a fresh route keeps CURRENT.
            await insert_probe(
                database,
                deployment_id,
                outcome="failed",
                error_class="measurement_error",
                error_code="streaming_usage_missing",
            )
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.CURRENT
            for _ in range(2):
                await insert_probe(
                    database,
                    deployment_id,
                    outcome="failed",
                    error_class="service_error",
                    error_code="http_503",
                )
            state, _, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.CURRENT

            # A failed route liveness check marks UNAVAILABLE.
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="failed",
                error_class="service_error",
                error_code="http_503",
            )
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.UNAVAILABLE
            assert reasons == ["http_503"]
            await engine.persist(deployment_id, state, ["confirmed"], None, None)
            assert await database.scalar("SELECT COUNT(*) FROM events") == 1
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_stale_response_is_delayed_and_maintenance_wins(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        engine = StateEngine(catalog, settings, database)
        deployment_id = catalog.deployments[0].deployment_id
        old = isoformat(datetime.now(UTC) - timedelta(hours=1))
        try:
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=old,
            )
            await insert_probe(database, deployment_id, finished_at=old)
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.DELAYED
            assert reasons == ["route_check_delayed"]
            await engine.persist(
                deployment_id,
                ResponseState.MAINTENANCE,
                ["maintenance"],
                None,
                None,
            )
            state, _, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.MAINTENANCE
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def _seed_api_database(tmp_path: Path) -> tuple[object, object, object, object]:
    async def scenario() -> tuple[object, object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await database.write(
            """
            INSERT INTO current_states(
                deployment_id, response_state, reasons_json, evaluated_at
            ) VALUES (?, 'current', '[]', ?)
            """,
            (deployment_id, isoformat()),
        )
        fixtures = (
            ("experience_short", "response-01"),
            ("experience_context", "response-04"),
            ("experience_short", "response-02"),
            ("experience_context", "response-05"),
            ("experience_short", "response-03"),
            ("experience_context", "response-06"),
        )
        for index, (kind, fixture) in enumerate(fixtures):
            await insert_probe(
                database,
                deployment_id,
                kind=kind,
                fixture_id=fixture,
                measurement={
                    "first_response_seconds": 0.4 + index / 10,
                    "output_speed_tps": 12.0 + index,
                    "reported_prompt_tokens": 32,
                    "reported_completion_tokens": 8,
                },
            )
        await close_database(database, writer)
        return settings, catalog, database, writer

    return asyncio.run(scenario())


def test_api_schema_v4_fixture_gate_etag_and_removed_routes(tmp_path: Path) -> None:
    settings, catalog, database, _ = _seed_api_database(tmp_path)
    health = RuntimeHealth()
    health.ready = True
    health.detail = "ready"
    app = create_app(database, catalog, settings, health, frontend_dir=tmp_path)
    deployment_id = catalog.deployments[0].deployment_id
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 200
        response = client.get("/api/v1/experience/overview")
        assert response.status_code == 200
        assert response.json()["schema_version"] == "5"
        item = next(
            row
            for row in response.json()["data"]
            if row["deployment_id"] == deployment_id
        )
        assert item["sample_count"] == 6
        assert item["fixture_count"] == 6
        assert item["complete_fixture_set"] is True
        assert item["first_response_p50"] == 0.65
        assert item["first_response_p95"] is None  # n=6 < 10
        assert item["output_speed_p50"] == 14.5
        assert item["output_speed_p95"] is None  # n=6 < 10
        assert item["uptime_24h"] is None  # no route probes seeded
        assert item["uptime_7d"] is None
        assert item["latest"] is not None
        etag = response.headers["etag"]
        assert (
            client.get(
                "/api/v1/experience/overview",
                headers={"If-None-Match": etag},
            ).status_code
            == 304
        )
        comparison = client.get("/api/v1/compare").json()["data"]
        measured = next(
            row for row in comparison if row["deployment_id"] == deployment_id
        )
        assert measured["value"] == 14.5
        assert measured["suite_version"] == "response-suite-v4"
        assert (
            client.get(
                f"/api/v1/deployments/{deployment_id}/experience/series"
            ).status_code
            == 200
        )
        assert client.get("/api/v1/experience/profiles").status_code == 200
        assert client.get("/api/v1/catalog").status_code == 200
        assert client.get("/api/v1/events").status_code == 200
        assert client.get("/api/v1/overview").status_code == 404
        assert (
            client.get(f"/api/v1/deployments/{deployment_id}/series").status_code == 404
        )


def test_api_latest_sample_before_summary_and_meta_is_secret_safe(
    tmp_path: Path,
) -> None:
    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await insert_probe(database, deployment_id)
        await close_database(database, writer)
        return settings, catalog, database

    settings, catalog, database = asyncio.run(seed())
    app = create_app(
        database, catalog, settings, RuntimeHealth(), frontend_dir=tmp_path
    )
    deployment_id = catalog.deployments[0].deployment_id
    with TestClient(app) as client:
        data = client.get(
            f"/api/v1/deployments/{deployment_id}/experience/latest"
        ).json()["data"]
        assert data["sample_count"] == 1
        assert data["latest"]["first_response_seconds"] == 0.5
        assert data["first_response_p50"] == 0.5
        assert data["first_response_p95"] is None  # n=1 < 10
        assert data["latest_attempt_outcome"] == "success"
        meta_text = client.get("/api/v1/meta").text.lower()
        assert "response-suite-v4" in meta_text
        assert "aggregate_output" not in meta_text
        assert "/metrics" not in meta_text
        assert "test-secret" not in meta_text
        assert "models.test" not in meta_text
        assert (
            client.get("/api/v1/experience/overview?profile=unknown").status_code == 400
        )
        assert (
            client.get("/api/v1/deployments/unknown/experience/latest").status_code
            == 404
        )


def test_generation_failure_keeps_pill_current_but_reports_attempt(
    tmp_path: Path,
) -> None:
    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await database.write(
            """
            INSERT INTO current_states(
                deployment_id, response_state, reasons_json, evaluated_at
            ) VALUES (?, 'current', '[]', ?)
            """,
            (deployment_id, isoformat()),
        )
        await insert_probe(
            database,
            deployment_id,
            kind="route",
            profile_id="",
            outcome="success",
        )
        await insert_probe(
            database,
            deployment_id,
            outcome="failed",
            error_class="service_error",
            error_code="http_503",
        )
        await close_database(database, writer)
        return settings, catalog, database

    settings, catalog, database = asyncio.run(seed())
    app = create_app(
        database, catalog, settings, RuntimeHealth(), frontend_dir=tmp_path
    )
    deployment_id = catalog.deployments[0].deployment_id
    with TestClient(app) as client:
        item = next(
            row
            for row in client.get("/api/v1/experience/overview").json()["data"]
            if row["deployment_id"] == deployment_id
        )
        # R1: a failed generation attempt must not flip the availability pill.
        assert item["response_state"] == "current"
        assert item["latest_attempt_outcome"] == "failed"
        assert item["latest_attempt_error_code"] == "http_503"
        assert item["latest_attempt_reason"] == "request_failed"
