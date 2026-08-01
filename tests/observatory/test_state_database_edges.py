from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maas_observatory.database import Database, isoformat
from maas_observatory.models import ResponseState
from maas_observatory.state import StateEngine
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    insert_probe,
    make_settings,
    open_database,
    storage_at,
)

ALL_TIERS = ("1k", "16k", "64k")


def test_database_version_guards_missing_check_and_writer_recovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        missing = Database(storage_at(tmp_path / "missing"))
        assert await missing.quick_check() == (False, "database_missing")
        # v1 is unsupported, v5 is newer than current (v4)
        for version in (1, 5):
            database = Database(storage_at(tmp_path / str(version)))
            database.prepare_directories()
            connection = sqlite3.connect(database.path)
            connection.execute(f"PRAGMA user_version={version}")
            connection.close()
            with pytest.raises(RuntimeError):
                await database.migrate()

        settings = make_settings(tmp_path / "writer")
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        try:
            with pytest.raises(sqlite3.OperationalError):
                await database.write("INSERT INTO missing_table VALUES (1)")
            assert await database.scalar("SELECT COUNT(*) FROM deployments") == 9
            await database.migrate()
            assert await database.current_epoch() == 1
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_backup_pruning_and_catalog_deactivation(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        try:
            now = datetime(2026, 7, 29, tzinfo=UTC)
            for days in (0, 1, 8, 30):
                stamp = now - timedelta(days=days)
                path = database.backup_dir / (
                    f"observatory-{stamp.strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
                )
                path.touch()
            malformed = database.backup_dir / "observatory-invalid.sqlite3"
            malformed.touch()
            await database.prune_backups(now=now)
            assert not malformed.exists()
            assert not (
                database.backup_dir / "observatory-20260629T000000Z.sqlite3"
            ).exists()

            one = catalog.model_copy(update={"deployments": [catalog.deployments[0]]})
            await database.synchronize_catalog(one)
            assert (
                await database.scalar("SELECT COUNT(*) FROM deployments WHERE active=1")
                == 1
            )
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_route_confirmation_failure_marks_unavailable(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        engine = StateEngine(catalog, settings, database)
        try:
            latest_id = 0
            for _ in range(3):
                latest_id = await insert_probe(
                    database,
                    deployment_id,
                    kind="route",
                    profile_id="",
                    outcome="failed",
                    error_class="transport_error",
                )
            await insert_probe(
                database,
                deployment_id,
                kind="confirmation",
                profile_id="",
                outcome="failed",
                error_class="service_error",
                confirmation_of=latest_id,
            )
            state, reasons, _, _ = await engine.evaluate(deployment_id)
            assert state == ResponseState.UNAVAILABLE
            assert reasons == ["route_failed"]
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_regression_detection_deduplication_and_evaluate_all(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        engine = StateEngine(catalog, settings, database)
        try:
            base = datetime.now(UTC) - timedelta(minutes=30)
            # Insert baseline probes with context_tier across all tiers
            for index in range(18):
                await insert_probe(
                    database,
                    deployment_id,
                    context_tier="1k",
                    fixture_id="agent-1k-a",
                    finished_at=isoformat(base + timedelta(seconds=index)),
                    measurement={
                        "first_response_seconds": 1.0,
                        "output_speed_tps": 20.0,
                    },
                )
            # Insert regression probes in 1k tier
            for index in range(2):
                await insert_probe(
                    database,
                    deployment_id,
                    context_tier="1k",
                    fixture_id="agent-1k-b",
                    finished_at=isoformat(datetime.now(UTC) + timedelta(seconds=index)),
                    measurement={
                        "first_response_seconds": 4.0,
                        "output_speed_tps": 5.0,
                    },
                )
            regressions = await engine._regression(deployment_id)
            expected = {
                "1k:first_response_seconds_regression",
                "1k:output_speed_tps_regression",
            }
            assert set(regressions) == expected, f"got {regressions}"

            await engine._persist_regression(deployment_id, regressions)
            # Deduplication: second persist should not create duplicate
            await engine._persist_regression(deployment_id, regressions)
            event_count = await database.scalar(
                """
                SELECT COUNT(*) FROM events
                WHERE deployment_id=? AND kind='response_regression'
                """,
                (deployment_id,),
            )
            assert event_count == 2  # one per unique reason

            await engine.evaluate_all()
            assert await database.scalar("SELECT COUNT(*) FROM current_states") == 9
            state_rows = await database.scalar("SELECT COUNT(*) FROM state_history")
            await engine.evaluate_all()
            assert (
                await database.scalar("SELECT COUNT(*) FROM state_history")
                == state_rows
            )
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_legacy_experience_kinds_do_not_affect_v5_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        engine = StateEngine(catalog, settings, database)
        now = isoformat()
        try:
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=now,
            )
            for _index in range(settings.experience.baseline_min_samples):
                await database.write(
                    """
                    INSERT INTO probe_runs(
                        deployment_id, kind, scheduled_at, started_at, finished_at,
                        outcome, error_class, profile_id, definition_version,
                        suite_version, vantage_id, collection_mode, fixture_id,
                        block_id, scheduler_lag_seconds, context_tier,
                        measurement_json
                    ) VALUES (?, 'experience_context', ?, ?, ?, 'success', 'none',
                              'response-v5', '5', 'response-suite-v5',
                              'test-vantage', 'rapid', 'agent-1k-a',
                              'legacy-block', 0, '1k', ?)
                    """,
                    (
                        deployment_id,
                        now,
                        now,
                        now,
                        '{"first_response_seconds":99,"output_speed_tps":1}',
                    ),
                )

            state, _, _, last_response_at = await engine.evaluate(deployment_id)
            assert state == ResponseState.CURRENT
            assert last_response_at is None
            assert await engine._regression(deployment_id) == []
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_state_loop_can_be_stopped(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        engine = StateEngine(catalog, settings, database)
        stop = asyncio.Event()

        async def evaluate_once() -> None:
            stop.set()

        engine.evaluate_all = evaluate_once  # type: ignore[method-assign]
        try:
            await engine.run(stop)
            assert stop.is_set()
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())
