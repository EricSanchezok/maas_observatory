from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maas_observatory.database import SCHEMA_VERSION, Database, isoformat
from maas_observatory.settings import load_observability_settings
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    insert_probe,
    make_settings,
    open_database,
    storage_at,
)


def test_settings_environment_mode_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "observability.yaml"
    config.write_text(
        """
schema_version: 3
collection_mode: standard
profiles: {sample: default-only}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAAS_OBSERVATORY_COLLECTION_MODE", "rapid")
    monkeypatch.setenv(
        "MAAS_OBSERVATORY_CORS_ORIGINS", "https://one.test, https://two.test"
    )
    settings = load_observability_settings(config)
    assert settings.collection_mode == "rapid"
    assert settings.server.cors_origins == [
        "https://one.test",
        "https://two.test",
    ]
    assert settings.interval_for() == 60
    monkeypatch.setenv("MAAS_OBSERVATORY_COLLECTION_MODE", "fast")
    with pytest.raises(ValueError, match="rapid or standard"):
        load_observability_settings(config)
    with pytest.raises(ValueError, match="does not exist"):
        load_observability_settings(tmp_path / "missing.yaml")


def test_schema_v3_contains_only_active_response_tables(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        try:
            version = await database.scalar("PRAGMA user_version")
            assert version == SCHEMA_VERSION
            names = {
                row["name"]
                for row in await database.query(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {
                "probe_runs",
                "probe_measurements",
                "current_states",
                "collection_blocks",
                "experience_profiles",
            } <= names
            assert {
                "scrape_snapshots",
                "metric_accumulators",
                "rollups",
                "metrics_sources",
            }.isdisjoint(names)
            assert len(await database.query("SELECT * FROM deployments")) == 9
            ok, detail = await database.quick_check()
            assert (ok, detail) == (True, "ok")
            assert await database.current_epoch() == 1
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())


def test_writer_retention_backup_and_reset(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        try:
            old = isoformat(datetime.now(UTC) - timedelta(days=366))
            await insert_probe(database, deployment_id, finished_at=old)
            await insert_probe(database, deployment_id)
            await database.apply_retention(now=datetime.now(UTC))
            assert await database.scalar("SELECT COUNT(*) FROM probe_runs") == 1
            backup = await database.backup(now=datetime(2026, 7, 29, 1, 2, tzinfo=UTC))
            assert backup.is_file()
        finally:
            await close_database(database, writer)
        with pytest.raises(ValueError, match="exactly"):
            database.reset_v3("wrong")
        database.reset_v3("response-probes-v3")
        assert not database.path.exists()

    asyncio.run(scenario())


def _make_minimal_v2(path: Path, deployment_id: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA user_version=2;
            CREATE TABLE deployments (
                deployment_id TEXT PRIMARY KEY
            );
            CREATE TABLE probe_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deployment_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                error_class TEXT NOT NULL,
                error_code TEXT,
                profile_id TEXT,
                definition_version TEXT NOT NULL,
                vantage_id TEXT,
                confirmation_of INTEGER,
                measurement_json TEXT NOT NULL
            );
            CREATE TABLE collection_epochs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE TABLE scrape_snapshots(id INTEGER);
            CREATE TABLE metric_accumulators(id INTEGER);
            CREATE TABLE rollups(id INTEGER);
            CREATE TABLE metrics_sources(id INTEGER);
            CREATE TABLE budget_usage(id INTEGER);
            """
        )
        connection.execute(
            "INSERT INTO deployments(deployment_id) VALUES (?)", (deployment_id,)
        )
        connection.execute(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, profile_id, definition_version,
                vantage_id, measurement_json
            ) VALUES (?, 'experience_short', ?, ?, ?, 'success', 'none',
                      'interactive-short-v1', '1', 'old-vantage', '{}')
            """,
            (deployment_id, isoformat(), isoformat(), isoformat()),
        )
        connection.execute(
            """
            INSERT INTO collection_epochs(schema_version, started_at, reason)
            VALUES (2, ?, 'old')
            """,
            (isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()


def test_v2_migration_backs_up_and_preserves_probe_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = storage_at(tmp_path)
        database = Database(storage)
        database.prepare_directories()
        deployment_id = "legacy-deployment"
        _make_minimal_v2(database.path, deployment_id)
        await database.migrate(collection_mode="rapid")
        assert await database.scalar("PRAGMA user_version") == 3
        assert await database.scalar("SELECT COUNT(*) FROM probe_runs") == 1
        tables = {
            row["name"]
            for row in await database.query(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "scrape_snapshots" not in tables
        assert list(database.backup_dir.glob("observatory-*.sqlite3"))

    asyncio.run(scenario())
