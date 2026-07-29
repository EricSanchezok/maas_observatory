from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maas_common.catalog import load_model_catalog
from maas_observatory.database import Database
from maas_observatory.settings import (
    StateSettings,
    StorageSettings,
    load_observability_settings,
)


def test_repository_observability_configuration_is_complete() -> None:
    settings = load_observability_settings()
    catalog = load_model_catalog()
    assert settings.scrape.interval_seconds == 15
    assert settings.scrape.max_concurrency == 4
    assert settings.probes.daily_budget.output_tokens == 3584
    assert settings.probes.daily_budget.input_tokens == 25000
    assert set(settings.profiles) == {item.alias for item in catalog.deployments}


def test_invalid_telemetry_threshold_order_is_rejected() -> None:
    with pytest.raises(ValueError, match="must increase"):
        StateSettings(
            telemetry_partial_seconds=60,
            telemetry_stale_seconds=45,
            telemetry_unavailable_seconds=300,
        )


def test_cors_origins_are_environment_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MAAS_OBSERVATORY_CORS_ORIGINS",
        "https://status.example.edu, https://backup.example.edu",
    )
    settings = load_observability_settings()
    assert settings.server.cors_origins == [
        "https://status.example.edu",
        "https://backup.example.edu",
    ]


def test_migration_catalog_snapshot_writer_backup_and_retention(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = StorageSettings(
            root=tmp_path,
            daily_backups=1,
            weekly_backups=1,
            raw_retention_days=1,
            minute_retention_days=1,
            five_minute_retention_days=1,
            probe_retention_days=1,
        )
        database = Database(storage)
        await database.migrate()
        await database.migrate()
        ok, detail = await database.quick_check()
        assert ok and detail == "ok"
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        catalog = load_model_catalog()
        await database.synchronize_catalog(catalog)
        assert await database.scalar("SELECT COUNT(*) FROM deployments") == 9
        document = await database.scalar(
            "SELECT document_json FROM config_snapshots LIMIT 1"
        )
        assert "base_url_env" not in document
        assert "api_key_env" not in document
        assert "private-key" not in document

        old = datetime.now(UTC) - timedelta(days=3)
        deployment_id = catalog.deployments[0].deployment_id
        await database.write(
            """
            INSERT INTO scrape_snapshots(
                deployment_id, observed_at, quality, error_class,
                counters_json, gauges_json, histograms_json
            ) VALUES (?, ?, 'exact', 'none', '{}', '{}', '{}')
            """,
            (deployment_id, old.isoformat()),
        )
        await database.apply_retention()
        assert await database.scalar("SELECT COUNT(*) FROM scrape_snapshots") == 0
        backup = await database.backup()
        assert backup.exists()
        await database.stop_writer()
        await writer

    asyncio.run(scenario())


def test_quick_check_reports_missing_database(tmp_path: Path) -> None:
    database = Database(StorageSettings(root=tmp_path))
    assert asyncio.run(database.quick_check()) == (False, "database_missing")


def test_catalog_snapshot_is_valid_json_and_has_no_secrets(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(StorageSettings(root=tmp_path))
        await database.migrate()
        writer = asyncio.create_task(database.writer_loop())
        await database.wait_writer()
        await database.synchronize_catalog(load_model_catalog())
        value = await database.scalar("SELECT document_json FROM config_snapshots")
        payload = json.loads(value)
        assert len(payload["deployments"]) == 9
        await database.stop_writer()
        await writer

    asyncio.run(scenario())
