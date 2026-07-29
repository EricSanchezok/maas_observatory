"""Single-process lifecycle for the API and all critical background tasks."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from maas_common.catalog import (
    DEFAULT_CATALOG,
    DEFAULT_DOTENV,
    load_dotenv,
    load_model_catalog,
)
from maas_observatory.api import RuntimeHealth, create_app
from maas_observatory.collector import RollupEngine, VLLMMetricsCollector
from maas_observatory.database import Database
from maas_observatory.probes import ProbeRunner, ProbeScheduler, profile_definitions
from maas_observatory.settings import (
    DEFAULT_OBSERVABILITY_CONFIG,
    ObservatorySettings,
    load_observability_settings,
)
from maas_observatory.state import StateEngine


async def maintenance_loop(database: Database, stop: asyncio.Event) -> None:
    while not stop.is_set():
        now = datetime.now(UTC)
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        try:
            await asyncio.wait_for(stop.wait(), timeout=(target - now).total_seconds())
            return
        except TimeoutError:
            await database.backup()
            await database.prune_backups()
            await database.apply_retention()


async def _serve_uvicorn(
    app: FastAPI, settings: ObservatorySettings, stop: asyncio.Event
) -> None:
    config = uvicorn.Config(
        app,
        host=settings.server.host,
        port=settings.server.port,
        workers=1,
        log_config=None,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        stop.set()


async def _watch_writer(writer: asyncio.Task[None], stop: asyncio.Event) -> None:
    stop_waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait(
        {writer, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
    )
    if writer in done and not stop.is_set():
        await writer
        raise RuntimeError("SQLite writer stopped unexpectedly")
    stop_waiter.cancel()


async def serve(
    *,
    config_path: Path = DEFAULT_OBSERVABILITY_CONFIG,
    catalog_path: Path = DEFAULT_CATALOG,
    dotenv_path: Path = DEFAULT_DOTENV,
) -> None:
    load_dotenv(dotenv_path)
    settings = load_observability_settings(config_path)
    catalog = load_model_catalog(catalog_path)
    unknown_profiles = set(settings.profiles) - {
        item.alias for item in catalog.deployments
    }
    if unknown_profiles:
        raise ValueError(
            "observability profiles reference unknown models: "
            f"{sorted(unknown_profiles)}"
        )
    database = Database(settings.storage)
    health = RuntimeHealth()
    try:
        await database.migrate()
        ok, detail = await database.quick_check()
    except Exception as exc:
        health.detail = f"database_startup_failed:{type(exc).__name__}"
        app = create_app(database, catalog, settings, health)
        await _serve_uvicorn(app, settings, asyncio.Event())
        return
    if not ok:
        health.detail = f"database_quick_check_failed:{detail}"
        app = create_app(database, catalog, settings, health)
        await _serve_uvicorn(app, settings, asyncio.Event())
        return

    stop = asyncio.Event()
    app = create_app(database, catalog, settings, health)
    async with (
        VLLMMetricsCollector(
            catalog,
            settings.scrape,
            database,
            settings.metrics_sources,
        ) as collector,
        ProbeRunner(
            catalog,
            settings.probes,
            settings.profiles,
            database,
            settings.experience,
        ) as probe_runner,
    ):
        scheduler = ProbeScheduler(probe_runner, database)
        rollups = RollupEngine(
            database, p95_min_samples=settings.scrape.p95_min_samples
        )
        states = StateEngine(catalog, settings.state, database)
        writer = asyncio.create_task(database.writer_loop(), name="sqlite-writer")
        try:
            await database.wait_writer()
            await database.synchronize_catalog(catalog)
            await database.synchronize_metrics_sources(
                catalog, settings.metrics_sources
            )
            for definition in profile_definitions(settings.experience):
                await database.write(
                    """
                    INSERT OR IGNORE INTO experience_profiles(
                        profile_id, definition_version, fixture_sha256,
                        definition_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        definition["profile_id"],
                        definition["definition_version"],
                        definition["fixture_sha256"],
                        json.dumps(definition, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            health.ready = True
            health.detail = "ready"
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_watch_writer(writer, stop), name="writer-supervisor")
                tasks.create_task(collector.run(stop), name="metrics-collector")
                tasks.create_task(
                    rollups.run(
                        stop, [item.deployment_id for item in catalog.deployments]
                    ),
                    name="rollups",
                )
                tasks.create_task(scheduler.route_loop(stop), name="route-liveness")
                tasks.create_task(
                    scheduler.speed_loop(stop), name="interactive-experience"
                )
                tasks.create_task(
                    scheduler.context_loop(stop), name="context-experience"
                )
                tasks.create_task(
                    scheduler.confirmation_loop(stop), name="confirmation"
                )
                tasks.create_task(states.run(stop), name="state-engine")
                tasks.create_task(maintenance_loop(database, stop), name="maintenance")
                tasks.create_task(
                    _serve_uvicorn(app, settings, stop), name="http-server"
                )
            health.ready = False
            health.detail = "stopping"
        finally:
            stop.set()
            await database.queue.join()
            await database.stop_writer()
            await writer
