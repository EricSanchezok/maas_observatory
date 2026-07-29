"""Operator-only command line interface for MaaS Observatory."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from maas_common.catalog import (
    DEFAULT_CATALOG,
    DEFAULT_DOTENV,
    ModelCatalog,
    load_dotenv,
    load_model_catalog,
)
from maas_observatory.collector import VLLMMetricsCollector
from maas_observatory.database import Database
from maas_observatory.models import ProbeKind
from maas_observatory.probes import ProbeRunner
from maas_observatory.runtime import serve as serve_runtime
from maas_observatory.settings import (
    DEFAULT_OBSERVABILITY_CONFIG,
    ObservatorySettings,
    load_observability_settings,
)

app = typer.Typer(no_args_is_help=True, help="MaaS Observatory operator CLI")
probe_app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
app.add_typer(probe_app, name="probe")
app.add_typer(db_app, name="db")


def _paths(
    config: Path, catalog: Path, dotenv: Path
) -> tuple[ObservatorySettings, ModelCatalog, Database]:
    load_dotenv(dotenv)
    settings = load_observability_settings(config)
    models = load_model_catalog(catalog)
    return settings, models, Database(settings.storage)


@app.command()
def serve(
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
    catalog: Annotated[Path, typer.Option()] = DEFAULT_CATALOG,
    dotenv: Annotated[Path, typer.Option()] = DEFAULT_DOTENV,
) -> None:
    """Migrate storage and run the API plus all background workers."""

    asyncio.run(
        serve_runtime(config_path=config, catalog_path=catalog, dotenv_path=dotenv)
    )


async def _initialized_database(
    database: Database,
    models: ModelCatalog,
    settings: ObservatorySettings | None = None,
) -> asyncio.Task[None]:
    await database.migrate()
    task = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(models)
    if settings is not None:
        await database.synchronize_metrics_sources(models, settings.metrics_sources)
    return task


@app.command()
def inventory(
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
    catalog: Annotated[Path, typer.Option()] = DEFAULT_CATALOG,
    dotenv: Annotated[Path, typer.Option()] = DEFAULT_DOTENV,
    generation: Annotated[
        bool,
        typer.Option(
            "--generation/--no-generation",
            help="Verify streaming usage with one manually authorized microprobe.",
        ),
    ] = True,
) -> None:
    """Check metrics, OpenAI routes, profiles, and optional streaming usage."""

    async def run() -> None:
        settings, models, database = _paths(config, catalog, dotenv)
        writer = await _initialized_database(database, models, settings)
        results = []
        try:
            async with (
                VLLMMetricsCollector(
                    models,
                    settings.scrape,
                    database,
                    settings.metrics_sources,
                ) as collector,
                ProbeRunner(
                    models,
                    settings.probes,
                    settings.profiles,
                    database,
                    settings.experience,
                ) as runner,
            ):
                for deployment in models.deployments:
                    metric = await collector.fetch(deployment)
                    route = await runner.route_liveness(deployment)
                    profile_ok = True
                    try:
                        runner.profile_for(deployment)
                    except ValueError:
                        profile_ok = False
                    stream = None
                    if generation:
                        stream = await runner.generation(
                            deployment, ProbeKind.EXPERIENCE_SHORT, force=True
                        )
                    results.append(
                        {
                            "deployment": deployment.alias,
                            "metrics": metric.quality,
                            "route": route.outcome,
                            "profile": "ok" if profile_ok else "invalid",
                            "streaming_usage": (
                                stream.outcome if stream else "not_checked"
                            ),
                        }
                    )
        finally:
            await database.stop_writer()
            await writer
        typer.echo(json.dumps(results, indent=2, default=str))

    asyncio.run(run())


@probe_app.command("run")
def probe_run(
    model: Annotated[str, typer.Option("--model")],
    kind: Annotated[ProbeKind, typer.Option()] = ProbeKind.EXPERIENCE_SHORT,
    force: Annotated[bool, typer.Option()] = False,
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
    catalog: Annotated[Path, typer.Option()] = DEFAULT_CATALOG,
    dotenv: Annotated[Path, typer.Option()] = DEFAULT_DOTENV,
) -> None:
    """Run one operator-requested probe."""

    async def run() -> None:
        settings, models, database = _paths(config, catalog, dotenv)
        writer = await _initialized_database(database, models, settings)
        try:
            async with ProbeRunner(
                models,
                settings.probes,
                settings.profiles,
                database,
                settings.experience,
            ) as runner:
                deployment = runner.deployment(model)
                result = (
                    await runner.route_liveness(deployment)
                    if kind == ProbeKind.ROUTE
                    else await runner.generation(deployment, kind, force=force)
                )
                typer.echo(result.model_dump_json(indent=2))
        finally:
            await database.stop_writer()
            await writer

    asyncio.run(run())


@db_app.command("migrate")
def db_migrate(
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    settings = load_observability_settings(config)
    asyncio.run(Database(settings.storage).migrate())
    typer.echo("migration complete")


@db_app.command("check")
def db_check(
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    async def run() -> None:
        settings = load_observability_settings(config)
        ok, detail = await Database(settings.storage).quick_check()
        typer.echo(json.dumps({"ok": ok, "detail": detail}))
        if not ok:
            raise typer.Exit(1)

    asyncio.run(run())


@db_app.command("backup")
def db_backup(
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    async def run() -> None:
        settings = load_observability_settings(config)
        path = await Database(settings.storage).backup()
        typer.echo(str(path))

    asyncio.run(run())


@db_app.command("reset")
def db_reset(
    confirm: Annotated[str, typer.Option("--confirm")],
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    """Explicitly rebuild the active database for metrics-source schema v2."""

    settings = load_observability_settings(config)
    database = Database(settings.storage)
    database.reset_v2(confirm)
    asyncio.run(database.migrate())
    typer.echo("database reset complete; collection epoch metrics-source-v2 started")


@app.command("export")
def export_data(
    format: Annotated[Literal["json", "csv"], typer.Option()] = "json",
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    """Export a sanitized operational snapshot."""

    async def run() -> None:
        settings = load_observability_settings(config)
        database = Database(settings.storage)
        rows = await database.query(
            """
            SELECT d.deployment_id, d.alias, d.display_name,
                   s.service_state, s.telemetry_state, s.telemetry_at,
                   s.evaluated_at
            FROM deployments d LEFT JOIN current_states s USING(deployment_id)
            WHERE d.active=1 ORDER BY d.alias
            """
        )
        database.export_dir.mkdir(parents=True, exist_ok=True)
        path = database.export_dir / f"snapshot.{format}"
        if format == "json":
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        else:
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=list(rows[0]) if rows else []
                )
                if rows:
                    writer.writeheader()
                    writer.writerows(rows)
        typer.echo(str(path))

    asyncio.run(run())
