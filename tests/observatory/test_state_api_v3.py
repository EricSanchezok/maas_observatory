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
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
            )
            await insert_probe(database, deployment_id)
            state, _, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.CURRENT

            await database.write(
                """
                UPDATE probe_runs SET scheduler_lag_seconds=999
                WHERE id=(SELECT MAX(id) FROM probe_runs
                          WHERE deployment_id=? AND kind='experience_short')
                """,
                (deployment_id,),
            )
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.DELAYED
            assert reasons == ["scheduler_delayed"]
            await database.write(
                """
                UPDATE probe_runs SET scheduler_lag_seconds=0
                WHERE id=(SELECT MAX(id) FROM probe_runs
                          WHERE deployment_id=? AND kind='experience_short')
                """,
                (deployment_id,),
            )

            await insert_probe(
                database,
                deployment_id,
                outcome="failed",
                error_class="measurement_error",
                error_code="streaming_usage_missing",
            )
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.DELAYED
            assert reasons == ["latest_request_failed"]

            for _ in range(2):
                await insert_probe(
                    database,
                    deployment_id,
                    outcome="failed",
                    error_class="service_error",
                    error_code="http_503",
                )
            state, _, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.UNAVAILABLE
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
            assert {"route_check_delayed", "response_check_delayed"} <= set(reasons)
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
        for index, fixture in enumerate(("short-01", "short-02", "short-03")):
            await insert_probe(
                database,
                deployment_id,
                fixture_id=fixture,
                measurement={
                    "first_response_seconds": 0.4 + index / 10,
                    "output_speed_tps": 12.0 + index,
                    "total_time_seconds": 1.7 + index / 10,
                    "reported_prompt_tokens": 32,
                    "reported_completion_tokens": 8,
                },
            )
        await close_database(database, writer)
        return settings, catalog, database, writer

    return asyncio.run(scenario())


def test_api_schema_v3_fixture_gate_etag_and_removed_routes(tmp_path: Path) -> None:
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
        assert response.json()["schema_version"] == "3"
        item = next(
            row
            for row in response.json()["data"]
            if row["deployment_id"] == deployment_id
        )
        assert item["sample_count"] == 3
        assert item["fixture_count"] == 3
        assert item["complete_fixture_set"] is True
        assert item["first_response_p50"] == 0.5
        assert item["output_speed_p50"] == 13.0
        assert item["first_response_p90"] is None
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
        assert measured["value"] == 13.0
        assert measured["suite_version"] == "response-suite-v2"
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
        assert data["first_response_p50"] is None
        meta_text = client.get("/api/v1/meta").text.lower()
        assert "response-suite-v2" in meta_text
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
