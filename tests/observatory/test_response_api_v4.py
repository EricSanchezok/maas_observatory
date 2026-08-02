from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from maas_observatory.api import ALL_TIERS, RuntimeHealth, create_app
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

ALL_TIERS_LIST = list(ALL_TIERS)


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
        # Seed 2 probes per tier (A+B) so all tiers are complete
        base = datetime.now(UTC) - timedelta(hours=2)
        for idx, tier in enumerate(ALL_TIERS_LIST):
            for v_idx, variant in enumerate(("a", "b")):
                fid = f"agent-{tier}-{variant}"
                await insert_probe(
                    database,
                    deployment_id,
                    kind="experience",
                    fixture_id=fid,
                    context_tier=tier,
                    finished_at=isoformat(base + timedelta(minutes=idx * 10 + v_idx)),
                    measurement={
                        "first_response_seconds": 0.4 + (idx * 2 + v_idx) / 10,
                        "first_token_seconds": 0.3 + (idx * 2 + v_idx) / 10,
                        "total_response_seconds": 2.1 + idx + v_idx,
                        "output_speed_tps": 12.0 + idx * 3 + v_idx,
                        "reported_prompt_tokens": 1300 + idx * 5000,
                        "ref_prompt_tokens": 1300 + idx * 5000,
                        "reported_completion_tokens": 8,
                        "reported_reasoning_tokens": 120 + idx * 20 + v_idx,
                        "reasoning_tokens_estimated": 999,
                    },
                )
        await close_database(database, writer)
        return settings, catalog, database, writer

    return asyncio.run(scenario())


def test_api_v7_schema_tier_structure_etag_and_endpoints(
    tmp_path: Path,
) -> None:
    settings, catalog, database, _ = _seed_api_database(tmp_path)
    health = RuntimeHealth()
    health.ready = True
    health.detail = "ready"
    app = create_app(database, catalog, settings, health, frontend_dir=tmp_path)
    deployment_id = catalog.deployments[0].deployment_id
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").status_code == 200

        # -- overview --
        response = client.get("/api/v1/experience/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "7"
        assert body["freshness_seconds"] is not None
        item = next(
            row for row in body["data"] if row["deployment_id"] == deployment_id
        )
        # Deployment-level fields
        assert item["alias"] is not None
        assert item["name"] is not None
        assert item["profile_id"] == "response-v6"
        assert item["definition_version"] == "6"
        assert item["suite_version"] == "response-suite-v6"
        assert item["collection_mode"] == "rapid"
        assert item["uptime_24h"] is None  # no route probes seeded
        assert item["uptime_7d"] is None
        assert item["uptime_30d"] is None
        assert item["path_success_rate"] is not None

        # Verify all three tiers exist
        tiers = item["tiers"]
        assert set(tiers.keys()) == set(ALL_TIERS_LIST)
        for tier in ALL_TIERS_LIST:
            t = tiers[tier]
            assert t["sample_count"] == 2
            assert t["fixture_count"] == 2
            assert t["complete_fixture_set"] is True
            assert "first_token_p50" in t
            assert "first_response_p50" in t
            assert "total_response_p50" in t
            assert "output_speed_p50" in t
            assert "reasoning_tokens_p50" in t
            assert t["reasoning_tokens_quality"] == "reported"
            assert t["reasoning_tokens_p50"] != 999
            assert "reported_prompt_tokens_p50" in t
            assert t["latest"] is not None
            assert t["latest_attempt_outcome"] == "success"
            # p95 should be None since n=2 < 10
            assert t["first_token_p95"] is None
            assert t["first_response_p95"] is None
            assert t["total_response_p95"] is None
            assert t["output_speed_p95"] is None

        # ETag and conditional request
        etag = response.headers["etag"]
        assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(
                "/api/v1/experience/overview",
                headers={"If-None-Match": etag},
            ).status_code
            == 304
        )
        catalog_response = client.get(
            "/api/v1/catalog",
            headers={"If-None-Match": etag},
        )
        assert catalog_response.status_code == 200
        assert catalog_response.headers["etag"] != etag

        compare_data = client.get("/api/v1/compare").json()
        assert compare_data["schema_version"] == "7"
        measured = next(
            row for row in compare_data["data"] if row["deployment_id"] == deployment_id
        )
        assert "tiers" in measured
        for tier in ALL_TIERS_LIST:
            t = measured["tiers"][tier]
            assert "first_token_p50" in t
            assert "output_speed_p50" in t
            assert "total_response_p50" in t
            assert "sample_count" in t
            assert "fixture_count" in t
            assert "complete_fixture_set" in t
            # 1k tier: two probes, complete fixture set
            assert t["fixture_count"] == 2
            assert t["complete_fixture_set"] is True
        assert measured["suite_version"] == "response-suite-v6"

        # -- experience/series --
        series_resp = client.get(
            f"/api/v1/deployments/{deployment_id}/experience/series"
        )
        assert series_resp.status_code == 200
        series_data = series_resp.json()["data"]
        assert series_data["deployment_id"] == deployment_id
        assert set(series_data["tiers"].keys()) == set(ALL_TIERS_LIST)
        for tier in ALL_TIERS_LIST:
            tier_pts = series_data["tiers"][tier]["points"]
            assert len(tier_pts) == 2

        # -- experience/latest --
        latest_resp = client.get(
            f"/api/v1/deployments/{deployment_id}/experience/latest"
        )
        assert latest_resp.status_code == 200
        latest_data = latest_resp.json()["data"]
        assert latest_data["deployment_id"] == deployment_id
        assert set(latest_data["tiers"].keys()) == set(ALL_TIERS_LIST)
        for tier in ALL_TIERS_LIST:
            assert latest_data["tiers"][tier]["sample_count"] == 2

        # -- experience/profiles --
        assert client.get("/api/v1/experience/profiles").status_code == 200

        # -- catalog --
        assert client.get("/api/v1/catalog").status_code == 200

        # -- events --
        assert client.get("/api/v1/events").status_code == 200

        # -- removed routes --
        assert client.get("/api/v1/overview").status_code == 404
        assert (
            client.get(f"/api/v1/deployments/{deployment_id}/series").status_code == 404
        )


def test_api_latest_empty_db_returns_warmup_tiers(
    tmp_path: Path,
) -> None:
    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        # Seed one probe in 1k tier only
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-a",
        )
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
        # All three tiers present even when no data
        assert set(data["tiers"].keys()) == set(ALL_TIERS_LIST)
        # 1k tier has data
        assert data["tiers"]["1k"]["sample_count"] == 1
        assert data["tiers"]["1k"]["latest"] is not None
        assert data["tiers"]["1k"]["first_response_p50"] == 0.5
        assert data["tiers"]["1k"]["first_response_p95"] is None
        assert data["tiers"]["1k"]["latest_attempt_outcome"] == "success"
        # 16k/64k have no data — warm-up structure
        for tier in ("16k", "64k"):
            t = data["tiers"][tier]
            assert t["sample_count"] == 0
            assert t["fixture_count"] == 0
            assert t["complete_fixture_set"] is False
            assert t["latest"] is None
            assert t["latest_attempt_reason"] == "first_check_scheduled"


def test_meta_is_v7_and_secret_safe(tmp_path: Path) -> None:
    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-a",
        )
        await close_database(database, writer)
        return settings, catalog, database

    settings, catalog, database = asyncio.run(seed())
    app = create_app(
        database, catalog, settings, RuntimeHealth(), frontend_dir=tmp_path
    )
    with TestClient(app) as client:
        meta = client.get("/api/v1/meta").json()
        assert meta["schema_version"] == "7"
        assert meta["data"]["api_schema_version"] == "7"
        assert meta["data"]["config_schema_version"] == 5
        assert meta["data"]["database_schema_version"] == 5
        assert meta["data"]["definition_version"] == "6"
        assert meta["data"]["context_tiers"] == [
            {"tier": "1k", "target_tokens": 1000, "fixture_count": 2},
            {"tier": "16k", "target_tokens": 16000, "fixture_count": 2},
            {"tier": "64k", "target_tokens": 64000, "fixture_count": 2},
        ]
        assert "fixture_order" not in meta["data"]
        assert meta["data"]["schedule"]["standard_rotation"] == [
            "1K-A",
            "16K-A",
            "64K-A",
            "1K-B",
            "16K-B",
            "64K-B",
        ]
        assert meta["data"]["schedule"]["rapid_context_tier"] == "1k"
        assert "rapid_automatic_limit" not in meta["data"]["schedule"]
        assert meta["data"]["deployment_limits"] is not None
        assert isinstance(meta["data"]["deployment_limits"], list)
        assert len(meta["data"]["deployment_limits"]) > 0
        assert "output_limit" in meta["data"]["deployment_limits"][0]
        assert (
            meta["data"]["request_shape"]["max_tokens"] == "per-deployment-output-limit"
        )
        assert "budget" not in meta["data"]
        # Secret safety
        import json

        meta_text = json.dumps(meta)
        assert "test-secret" not in meta_text
        assert "models.test" not in meta_text
        assert "agent-1k-a" not in meta_text
        assert "sha256" not in meta_text
        assert "tokenizer" not in meta_text

        assert (
            client.get("/api/v1/experience/overview?profile=unknown").status_code == 400
        )
        assert (
            client.get("/api/v1/deployments/unknown/experience/latest").status_code
            == 404
        )


def test_measurement_limit_does_not_reduce_path_success(tmp_path: Path) -> None:
    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-a",
            outcome="success",
        )
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-b",
            outcome="failed",
            error_class="measurement_error",
            error_code="streaming_usage_missing",
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
        assert item["path_success_rate"] == 1.0
        assert item["tiers"]["1k"]["sample_count"] == 1
        assert item["tiers"]["1k"]["latest_attempt_reason"] == ("measurement_limited")
        series = client.get(
            f"/api/v1/deployments/{deployment_id}/experience/series"
        ).json()["data"]
        assert series["tiers"]["1k"]["points"][-1]["reason"] == ("measurement_limited")


def test_generation_failure_visible_in_tier_latest_attempt(
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
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-a",
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
        # deployment-level: route state is current
        # 1k tier: one failed attempt visible
        t1k = item["tiers"]["1k"]
        assert t1k["sample_count"] == 0
        assert t1k["latest_attempt_outcome"] == "failed"
        assert t1k["latest_attempt_error_code"] == "http_503"
        assert t1k["latest_attempt_reason"] == "request_failed"
        assert t1k["latest"] is None
        # 16k/64k have no attempts
        for tier in ("16k", "64k"):
            assert item["tiers"][tier]["latest_attempt_reason"] == (
                "first_check_scheduled"
            )


def test_prompt_deviation_preserves_measurements_in_tier_stats(
    tmp_path: Path,
) -> None:
    """Prompt deviation probes must contribute measurements to tier p50/p95.

    After the fix, prompt deviation is a measurement metadata field
    (prompt_token_deviation_pct / prompt_token_quality), not an outcome
    flip.  Both probes should count as successful path attempts and feed
    into per-tier statistics.
    """

    async def seed() -> tuple[object, object, object]:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-a",
            outcome="success",
            measurement={
                "first_response_seconds": 0.5,
                "first_token_seconds": 0.3,
                "total_response_seconds": 2.1,
                "output_speed_tps": 15.0,
                "reported_prompt_tokens": 1000,
                "ref_prompt_tokens": 1000,
                "reported_completion_tokens": 8,
                "prompt_token_deviation_pct": 0.0,
                "configured_output_tokens": catalog.deployments[0].output_limit,
                "prompt_token_quality": "reported",
            },
        )
        await insert_probe(
            database,
            deployment_id,
            kind="experience",
            context_tier="1k",
            fixture_id="agent-1k-b",
            outcome="success",
            measurement={
                "first_response_seconds": 0.6,
                "first_token_seconds": 0.4,
                "total_response_seconds": 2.3,
                "output_speed_tps": 14.0,
                "reported_prompt_tokens": 1200,
                "ref_prompt_tokens": 1000,
                "reported_completion_tokens": 8,
                "prompt_token_deviation_pct": 20.0,
                "prompt_token_quality": "reference_mismatch",
                "configured_output_tokens": catalog.deployments[0].output_limit,
            },
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
        # Both probes count as path attempts and successes
        assert item["path_success_rate"] == 1.0

        t1k = item["tiers"]["1k"]
        # Both probes should contribute to aggregation
        assert t1k["sample_count"] == 2
        assert t1k["fixture_count"] == 2
        assert t1k["complete_fixture_set"] is True
        # Measurements from both probes in p50
        assert t1k["first_token_p50"] is not None
        assert t1k["first_response_p50"] is not None
        assert t1k["total_response_p50"] is not None
        assert t1k["output_speed_p50"] is not None
        # Prompt token stats include both probes
        assert t1k["reported_prompt_tokens_p50"] is not None
        assert t1k["prompt_token_deviation_p50"] == 10.0
        assert t1k["prompt_token_quality"] == "reference_mismatch"
        # Latest attempt should be success, not failed
        assert t1k["latest_attempt_outcome"] == "success"
        assert t1k["latest"] is not None
        assert t1k["latest_attempt_reason"] is None
        # series endpoint also has both points
        series = client.get(
            f"/api/v1/deployments/{deployment_id}/experience/series"
        ).json()["data"]
        assert len(series["tiers"]["1k"]["points"]) == 2
        latest_point = series["tiers"]["1k"]["points"][-1]
        assert latest_point["quality"] == "exact"
        assert latest_point["reason"] is None
        assert latest_point["prompt_token_quality"] == "reference_mismatch"
        assert latest_point["measurements"]["prompt_token_deviation_pct"] == 20.0
        assert (
            latest_point["measurements"]["configured_output_tokens"]
            == catalog.deployments[0].output_limit
        )
