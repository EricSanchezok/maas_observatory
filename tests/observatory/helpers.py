from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from maas_common.catalog import ModelCatalog, load_model_catalog
from maas_observatory.database import Database, isoformat
from maas_observatory.probes import profile_definitions
from maas_observatory.settings import ObservatorySettings, StorageSettings


def configured_catalog() -> ModelCatalog:
    catalog = load_model_catalog()
    for deployment in catalog.deployments:
        os.environ[deployment.endpoint.base_url_env] = "https://models.test/v1"
        os.environ[deployment.endpoint.api_key_env] = "test-secret"
    return catalog


def make_settings(tmp_path: Path, *, mode: str = "rapid") -> ObservatorySettings:
    return ObservatorySettings.model_validate(
        {
            "schema_version": 4,
            "collection_mode": mode,
            "server": {"host": "127.0.0.1", "port": 8080},
            "storage": {
                "root": str(tmp_path / "data"),
                "database": "observatory.sqlite3",
                "writer_queue_size": 32,
                "probe_retention_days": 365,
                "daily_backups": 2,
                "weekly_backups": 2,
            },
            "probes": {
                "route_interval_seconds": 10,
                "confirmation_delay_seconds": 10,
                "response_start_timeout_seconds": 10,
                "stream_stall_seconds": 2,
                "canary_max_output_tokens": 8,
                "experience_max_output_tokens": 8,
                "rapid_block_interval_seconds": 10,
                "rapid_context_tier": "1k" if mode == "rapid" else None,
                "standard_block_interval_seconds": 60,
                "daily_budget": {
                    "requests": 3,
                    "input_tokens": 100_000,
                    "output_tokens": 24,
                },
            },
            "profiles": {
                deployment.alias: (
                    next(iter(deployment.profiles))
                    if deployment.profiles
                    else "default-only"
                )
                for deployment in configured_catalog().deployments
            },
            "experience": {
                "vantage_id": "test-vantage",
                "suite_version": "response-suite-v5",
                "response_profile_id": "response-v5",
                "definition_version": "5",
                "summary_min_samples": 6,
                "baseline_min_samples": 20,
            },
        }
    )


async def open_database(
    settings: ObservatorySettings, catalog: ModelCatalog
) -> tuple[Database, asyncio.Task[None]]:
    database = Database(settings.storage)
    await database.migrate(
        collection_mode=settings.collection_mode,
        suite_version=settings.experience.suite_version,
    )
    writer = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(catalog)
    for definition in profile_definitions(settings.experience, settings.probes):
        serialized = json.dumps(definition, sort_keys=True)
        await database.write(
            """
            INSERT OR REPLACE INTO experience_profiles(
                profile_id, definition_version, fixture_sha256,
                definition_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                definition["profile_id"],
                definition["definition_version"],
                definition["fixtures"][0]["sha256"],
                serialized,
                isoformat(),
            ),
        )
    return database, writer


async def close_database(database: Database, writer: asyncio.Task[None]) -> None:
    await database.stop_writer()
    await writer


async def insert_probe(
    database: Database,
    deployment_id: str,
    *,
    kind: str = "experience",
    outcome: str = "success",
    error_class: str = "none",
    error_code: str | None = None,
    profile_id: str = "response-v5",
    fixture_id: str = "agent-1k-a",
    collection_mode: str = "rapid",
    finished_at: str | None = None,
    measurement: dict[str, Any] | None = None,
    confirmation_of: int | None = None,
    context_tier: str | None = None,
) -> int:
    timestamp = finished_at or isoformat()
    return await database.write(
        """
        INSERT INTO probe_runs(
            deployment_id, kind, scheduled_at, started_at, finished_at,
            outcome, error_class, error_code, profile_id,
            definition_version, suite_version, vantage_id,
            collection_mode, fixture_id, block_id, scheduler_lag_seconds,
            confirmation_of, context_tier, measurement_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '5', 'response-suite-v5',
                  'test-vantage', ?, ?, 'test-block', 0, ?, ?, ?)
        """,
        (
            deployment_id,
            kind,
            timestamp,
            timestamp,
            timestamp,
            outcome,
            error_class,
            error_code,
            profile_id if kind == "experience" else None,
            collection_mode,
            fixture_id if kind == "experience" else None,
            confirmation_of,
            context_tier,
            json.dumps(
                measurement
                or {
                    "first_response_seconds": 0.5,
                    "first_token_seconds": 0.3,
                    "total_response_seconds": 2.1,
                    "output_speed_tps": 15.0,
                    "reported_prompt_tokens": 32,
                    "reported_completion_tokens": 8,
                },
                sort_keys=True,
            ),
        ),
    )


def storage_at(tmp_path: Path) -> StorageSettings:
    return StorageSettings(
        root=tmp_path / "data",
        database="observatory.sqlite3",
        writer_queue_size=32,
        probe_retention_days=2,
        daily_backups=2,
        weekly_backups=2,
    )
