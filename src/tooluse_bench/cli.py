"""Public command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from tooluse_bench.config import (
    DEFAULT_CATALOG,
    DEFAULT_EXPERIMENT,
    load_dotenv,
    load_experiment,
    load_model_catalog,
)
from tooluse_bench.registry import BenchmarkRegistry
from tooluse_bench.release import build_release, validate_release
from tooluse_bench.reporting import build_report
from tooluse_bench.runner import run_experiment, select_deployments

app = typer.Typer(
    name="tooluse-bench",
    help="Reproducible tool-use evaluation for OpenAI-compatible deployments.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Inspect and validate model deployments.")
benchmarks_app = typer.Typer(help="Inspect and validate benchmark adapters.")
report_app = typer.Typer(help="Build deterministic reports from private runs.")
release_app = typer.Typer(help="Build and validate sanitized release bundles.")
app.add_typer(models_app, name="models")
app.add_typer(benchmarks_app, name="benchmarks")
app.add_typer(report_app, name="report")
app.add_typer(release_app, name="release")
console = Console()


@models_app.command("list")
def models_list(
    catalog: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CATALOG,
) -> None:
    model_catalog = load_model_catalog(catalog)
    table = Table("Alias", "Deployment ID", "Upstream model", "Precision", "Input")
    for model in model_catalog.deployments:
        table.add_row(
            model.alias,
            model.deployment_id,
            model.upstream_model,
            model.precision.value,
            ",".join(model.input_modalities),
        )
    console.print(table)


@models_app.command("validate")
def models_validate(
    catalog: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CATALOG,
    require_endpoints: Annotated[
        bool,
        typer.Option(help="Also require private endpoint URLs and API keys from .env."),
    ] = False,
) -> None:
    load_dotenv()
    model_catalog = load_model_catalog(catalog)
    errors = 0
    for model in model_catalog.deployments:
        configuration_errors = model.configuration_errors() if require_endpoints else []
        if configuration_errors:
            errors += 1
            console.print(
                f"[red]FAIL[/red] {model.alias}: {'; '.join(configuration_errors)}"
            )
        else:
            console.print(f"[green]OK[/green]   {model.alias}")
    if errors:
        raise typer.Exit(1)


@benchmarks_app.command("list")
def benchmarks_list() -> None:
    registry = BenchmarkRegistry.discover()
    table = Table("ID", "Version", "Profiles", "Hermetic default")
    for adapter in registry.all():
        metadata = adapter.metadata
        table.add_row(
            metadata.benchmark_id,
            metadata.version,
            ", ".join(metadata.supported_profiles),
            "yes" if metadata.hermetic_default else "no",
        )
    console.print(table)


@benchmarks_app.command("validate")
def benchmarks_validate(
    experiment: Annotated[Path, typer.Option(exists=True)] = DEFAULT_EXPERIMENT,
    catalog: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CATALOG,
) -> None:
    plan = load_experiment(experiment)
    model_catalog = load_model_catalog(catalog)
    deployments = select_deployments(plan, model_catalog)
    registry = BenchmarkRegistry.discover()
    errors = 0
    for selection in plan.benchmarks:
        adapter = registry.get(selection.benchmark_id)
        for deployment in deployments:
            issues = adapter.validate(selection, deployment)
            for issue in issues:
                style = "red" if issue.level == "error" else "yellow"
                console.print(
                    f"[{style}]{issue.level.upper()}[/{style}] "
                    f"{selection.benchmark_id}/{deployment.alias}: "
                    f"{issue.code}: {issue.message}"
                )
                errors += issue.level == "error"
    if errors:
        raise typer.Exit(1)
    console.print("[green]All benchmark selections are valid.[/green]")


@app.command("run")
def run(
    experiment: Annotated[Path, typer.Option(exists=True)] = DEFAULT_EXPERIMENT,
    catalog: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CATALOG,
    output_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Execute an immutable experiment plan."""

    load_dotenv()
    run_directory = run_experiment(
        experiment_path=experiment,
        catalog_path=catalog,
        output_root=output_root,
    )
    console.print(f"[green]Run completed:[/green] {run_directory}")


def _resolve_directory(identifier: str, root: Path) -> Path:
    direct = Path(identifier)
    directory = direct if direct.is_dir() else root / identifier
    if not directory.is_dir():
        raise typer.BadParameter(f"directory does not exist: {directory}")
    return directory.resolve()


@report_app.command("build")
def report_build(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = Path("runs"),
) -> None:
    directory = _resolve_directory(run_id, runs_root)
    report_directory = build_report(directory)
    console.print(f"[green]Report built:[/green] {report_directory}")


@release_app.command("build")
def release_build(
    run_id: str,
    runs_root: Annotated[Path, typer.Option()] = Path("runs"),
    output_root: Annotated[Path, typer.Option()] = Path("release-staging"),
) -> None:
    load_dotenv()
    directory = _resolve_directory(run_id, runs_root)
    release_directory, archive = build_release(directory, output_root=output_root)
    console.print(f"[green]Release built:[/green] {release_directory}")
    console.print(f"[green]Archive:[/green] {archive}")


@release_app.command("validate")
def release_validate(
    release_id: str,
    releases_root: Annotated[Path, typer.Option()] = Path("release-staging"),
) -> None:
    load_dotenv()
    directory = _resolve_directory(release_id, releases_root)
    validate_release(directory)
    console.print(f"[green]Release is valid:[/green] {directory}")


def main() -> None:
    app()
