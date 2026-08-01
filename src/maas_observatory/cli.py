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
from maas_observatory.database import Database
from maas_observatory.fixtures import FIXTURE_IDS
from maas_observatory.models import ProbeKind
from maas_observatory.probes import ProbeRunner, fixture_prompt
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
    settings: ObservatorySettings,
) -> asyncio.Task[None]:
    await database.migrate(
        collection_mode=settings.collection_mode,
        suite_version=settings.experience.suite_version,
    )
    task = asyncio.create_task(database.writer_loop())
    await database.wait_writer()
    await database.synchronize_catalog(models)
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
            help="Run one manually authorized 1K Agent response request per model.",
        ),
    ] = False,
) -> None:
    """Check OpenAI routes, request profiles, and optional streaming responses."""

    async def run() -> None:
        settings, models, database = _paths(config, catalog, dotenv)
        writer = await _initialized_database(database, models, settings)
        results = []
        try:
            async with ProbeRunner(
                models,
                settings.probes,
                settings.profiles,
                database,
                settings.experience,
                settings.collection_mode,
            ) as runner:
                for deployment in models.deployments:
                    route = await runner.route_liveness(deployment)
                    try:
                        runner.profile_for(deployment)
                        profile = "ok"
                    except ValueError:
                        profile = "invalid"
                    stream = None
                    if generation:
                        fid = FIXTURE_IDS[0]
                        _, payload = fixture_prompt(fid, "manual-check-000")
                        stream = await runner.generation(
                            deployment,
                            ProbeKind.EXPERIENCE,
                            fixture_id=fid,
                            prompt_data=payload,
                            force=True,
                        )
                    results.append(
                        {
                            "deployment": deployment.alias,
                            "route": route.outcome,
                            "profile": profile,
                            "streaming_response": (
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
    kind: Annotated[ProbeKind, typer.Option()] = ProbeKind.EXPERIENCE,
    force: Annotated[bool, typer.Option()] = False,
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
    catalog: Annotated[Path, typer.Option()] = DEFAULT_CATALOG,
    dotenv: Annotated[Path, typer.Option()] = DEFAULT_DOTENV,
) -> None:
    """Run one operator-requested route or streaming check."""

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
                settings.collection_mode,
            ) as runner:
                deployment = runner.deployment(model)
                if kind == ProbeKind.ROUTE:
                    result = await runner.route_liveness(deployment)
                else:
                    fid = FIXTURE_IDS[0]
                    _, payload = fixture_prompt(fid, "manual-check-000")
                    result = await runner.generation(
                        deployment,
                        kind,
                        fixture_id=fid,
                        prompt_data=payload,
                        force=force,
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
    asyncio.run(
        Database(settings.storage).migrate(
            collection_mode=settings.collection_mode,
            suite_version=settings.experience.suite_version,
        )
    )
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
    """Explicitly rebuild the active response-probe database."""

    settings = load_observability_settings(config)
    database = Database(settings.storage)
    database.reset_v4(confirm)
    asyncio.run(
        database.migrate(
            collection_mode=settings.collection_mode,
            suite_version=settings.experience.suite_version,
        )
    )
    typer.echo("database reset complete; response-suite-v5 epoch started")


@app.command("export")
def export_data(
    format: Annotated[Literal["json", "csv"], typer.Option()] = "json",
    config: Annotated[Path, typer.Option()] = DEFAULT_OBSERVABILITY_CONFIG,
) -> None:
    """Export a sanitized response-state snapshot."""

    async def run() -> None:
        settings = load_observability_settings(config)
        database = Database(settings.storage)
        rows = await database.query(
            """
            SELECT d.deployment_id, d.alias, d.display_name,
                   s.response_state, s.last_route_at,
                   s.last_response_at, s.evaluated_at
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
