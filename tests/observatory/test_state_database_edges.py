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


def test_database_version_guards_missing_check_and_writer_recovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        missing = Database(storage_at(tmp_path / "missing"))
        assert await missing.quick_check() == (False, "database_missing")
        for version in (1, 4):
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
            assert reasons == ["confirmed_request_failure"]
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
            for index in range(18):
                await insert_probe(
                    database,
                    deployment_id,
                    finished_at=isoformat(base + timedelta(seconds=index)),
                    measurement={
                        "first_response_seconds": 1.0,
                        "output_speed_tps": 20.0,
                        "total_time_seconds": 2.0,
                    },
                )
            for index in range(2):
                await insert_probe(
                    database,
                    deployment_id,
                    finished_at=isoformat(datetime.now(UTC) + timedelta(seconds=index)),
                    measurement={
                        "first_response_seconds": 4.0,
                        "output_speed_tps": 5.0,
                        "total_time_seconds": 6.0,
                    },
                )
            regressions = await engine._regression(deployment_id)
            assert set(regressions) == {
                "first_response_seconds_regression",
                "output_speed_tps_regression",
                "total_time_seconds_regression",
            }
            await engine._persist_regression(deployment_id, regressions)
            await engine._persist_regression(deployment_id, regressions)
            assert (
                await database.scalar(
                    """
                SELECT COUNT(*) FROM events
                WHERE event_key='response:regression'
                """
                )
                == 1
            )
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
