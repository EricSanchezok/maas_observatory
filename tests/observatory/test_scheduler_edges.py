from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from maas_observatory.models import ProbeKind, ProbeOutcome
from maas_observatory.probes import ProbeRunner, ProbeScheduler
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    insert_probe,
    make_settings,
    open_database,
)


def _runner(
    catalog: Any,
    settings: Any,
    database: Any,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProbeRunner:
    return ProbeRunner(
        catalog,
        settings.probes,
        settings.profiles,
        database,
        settings.experience,
        settings.collection_mode,
        client=client,
    )


def test_runner_lookup_context_and_profile_validation(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        runner = _runner(catalog, settings, database)
        try:
            assert (
                runner.deployment(catalog.deployments[0].alias)
                == (catalog.deployments[0])
            )
            with pytest.raises(ValueError, match="unknown deployment"):
                runner.deployment("missing")
            no_profile = _runner(
                catalog,
                settings.model_copy(update={"profiles": {}}),
                database,
            )
            with pytest.raises(ValueError, match="no operational profile"):
                no_profile.profile_for(catalog.deployments[0])
            bad_profile = _runner(
                catalog,
                settings.model_copy(
                    update={
                        "profiles": {
                            **settings.profiles,
                            catalog.deployments[0].alias: "missing",
                        }
                    }
                ),
                database,
            )
            with pytest.raises(ValueError, match="is not defined"):
                bad_profile.profile_for(catalog.deployments[0])
            async with runner:
                assert runner._client is not None
            assert runner._client is not None
            assert runner._client.is_closed
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_route_configuration_protocol_and_transport_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment = catalog.deployments[0]
        env_name = deployment.endpoint.base_url_env
        try:
            monkeypatch.delenv(env_name)
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"data": []}, request=request
                    )
                )
            )
            runner = _runner(catalog, settings, database, client=client)
            missing = await runner.route_liveness(deployment)
            assert (missing.error_class, missing.error_code) == (
                "measurement_error",
                "configuration",
            )
            monkeypatch.setenv(env_name, "https://models.test/v1")
            await client.aclose()

            responses = [
                httpx.Response(200, json={"wrong": []}),
                httpx.Response(503),
            ]

            def handler(request: httpx.Request) -> httpx.Response:
                response = responses.pop(0)
                response.request = request
                return response

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            runner = _runner(catalog, settings, database, client=client)
            invalid = await runner.route_liveness(deployment)
            unavailable = await runner.route_liveness(deployment)
            assert invalid.error_code == "protocol_invalid"
            assert unavailable.error_code == "http_503"
            await client.aclose()
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_generation_skip_paths_and_standard_budget_reservation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path, mode="standard")
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment = catalog.deployments[0]
        runner = _runner(catalog, settings, database)
        try:
            with pytest.raises(ValueError, match="unsupported"):
                await runner.generation(deployment, ProbeKind.ROUTE)
            await database.write(
                """
                INSERT INTO current_states(
                    deployment_id, response_state, reasons_json, evaluated_at
                ) VALUES (?, 'maintenance', '[]', ?)
                """,
                (deployment.deployment_id, datetime.now(UTC).isoformat()),
            )
            skipped = await runner.generation(deployment, ProbeKind.EXPERIENCE_CONTEXT)
            assert (skipped.outcome, skipped.error_code) == (
                ProbeOutcome.SKIPPED,
                "maintenance",
            )
            await database.write(
                "DELETE FROM current_states WHERE deployment_id=?",
                (deployment.deployment_id,),
            )
            no_profile = _runner(
                catalog,
                settings.model_copy(update={"profiles": {}}),
                database,
            )
            skipped = await no_profile.generation(
                deployment, ProbeKind.EXPERIENCE_SHORT
            )
            assert skipped.error_code == "profile_undefined"
            assert skipped.error_class == "measurement_error"

            today = datetime.now(UTC).date().isoformat()
            await database.write(
                """
                INSERT INTO budget_usage(
                    deployment_id, budget_date, short_requests,
                    context_requests, output_tokens
                ) VALUES (?, ?, 0, 1, 0)
                """,
                (deployment.deployment_id, today),
            )
            context_allowed, context_reason = await runner._standard_budget_available(
                deployment, ProbeKind.EXPERIENCE_CONTEXT, 8
            )
            assert (context_allowed, context_reason) == (
                False,
                "daily_context_budget",
            )
            await database.write(
                """
                UPDATE budget_usage SET context_requests=0, output_tokens=20
                WHERE deployment_id=? AND budget_date=?
                """,
                (deployment.deployment_id, today),
            )
            token_allowed, token_reason = await runner._standard_budget_available(
                deployment, ProbeKind.EXPERIENCE_CONTEXT, 8
            )
            assert (token_allowed, token_reason) == (
                False,
                "daily_output_token_budget",
            )
            await database.write(
                """
                UPDATE budget_usage SET output_tokens=0
                WHERE deployment_id=? AND budget_date=?
                """,
                (deployment.deployment_id, today),
            )
            await runner._reserve_standard_budget(
                deployment, ProbeKind.EXPERIENCE_CONTEXT, 8
            )
            usage = (
                await database.query(
                    """
                    SELECT * FROM budget_usage
                    WHERE deployment_id=? AND budget_date=?
                    """,
                    (deployment.deployment_id, today),
                )
            )[0]
            assert usage["context_requests"] == 1
            assert usage["output_tokens"] == 8
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_scheduler_state_rapid_and_standard_loops(tmp_path: Path) -> None:
    async def run_mode(mode: str) -> None:
        settings = make_settings(tmp_path / mode, mode=mode)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        runner = _runner(catalog, settings, database)
        scheduler = ProbeScheduler(runner, database)
        stop = asyncio.Event()
        calls: list[tuple[ProbeKind, int, int]] = []
        scheduled_calls: list[datetime] = []

        async def fake_block(
            kind: ProbeKind,
            fixture_index: int,
            block_index: int,
            scheduled_at: datetime,
            loop_stop: asyncio.Event | None = None,
        ) -> None:
            assert scheduled_at.tzinfo is not None
            calls.append((kind, fixture_index, block_index))
            scheduled_calls.append(scheduled_at)
            stop.set()

        scheduler.run_block = fake_block  # type: ignore[method-assign]
        try:
            initial = await scheduler._load_schedule()
            assert initial["block_index"] == 0
            if mode == "rapid":
                initial["next_rapid_at"] = (
                    datetime.now(UTC) - timedelta(minutes=5)
                ).isoformat()
                await scheduler._save_schedule(initial)
            await scheduler.response_loop(stop)
            assert calls
            stored = json.loads(
                str(
                    await database.scalar(
                        "SELECT value_json FROM scheduler_state "
                        "WHERE key='response_blocks'"
                    )
                )
            )
            assert stored["block_index"] == 1
            if mode == "rapid":
                assert calls[0][0] == ProbeKind.EXPERIENCE_SHORT
                assert stored["next_rapid_at"] is not None
                assert datetime.now(UTC) - scheduled_calls[0] > timedelta(minutes=4)
                assert datetime.fromisoformat(stored["next_rapid_at"]) > (
                    datetime.now(UTC)
                )
            else:
                assert stored["next_short_at"] is not None
            assert (await scheduler._load_schedule())["block_index"] == 1
        finally:
            await close_database(database, writer)

    asyncio.run(run_mode("rapid"))
    asyncio.run(run_mode("standard"))


def test_route_and_confirmation_loops_stop_cleanly(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        runner = _runner(catalog, settings, database)
        scheduler = ProbeScheduler(runner, database)
        route_stop = asyncio.Event()
        route_calls = 0

        async def fake_route(deployment: Any) -> None:
            nonlocal route_calls
            route_calls += 1
            if route_calls == len(catalog.deployments):
                route_stop.set()

        runner.route_liveness = fake_route  # type: ignore[method-assign]
        try:
            await scheduler.route_loop(route_stop)
            assert route_calls == 9

            deployment = catalog.deployments[0]
            latest_id = 0
            old = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
            for _ in range(3):
                latest_id = await insert_probe(
                    database,
                    deployment.deployment_id,
                    kind="route",
                    profile_id="",
                    outcome="failed",
                    error_class="transport_error",
                    finished_at=old,
                )
            confirmation_stop = asyncio.Event()
            confirmations: list[int | None] = []

            async def fake_generation(
                target: Any, kind: ProbeKind, **kwargs: Any
            ) -> None:
                assert target == deployment
                assert kind == ProbeKind.CONFIRMATION
                confirmations.append(kwargs.get("confirmation_of"))
                confirmation_stop.set()

            runner.generation = fake_generation  # type: ignore[method-assign]
            await scheduler.confirmation_loop(confirmation_stop)
            assert confirmations == [latest_id]
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_interrupted_block_records_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        scheduler = ProbeScheduler(_runner(catalog, settings, database), database)
        stop = asyncio.Event()
        stop.set()
        try:
            await scheduler.run_block(
                ProbeKind.EXPERIENCE_SHORT,
                0,
                0,
                datetime.now(UTC),
                stop,
            )
            assert (
                await database.scalar("SELECT status FROM collection_blocks")
                == "interrupted"
            )
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())
