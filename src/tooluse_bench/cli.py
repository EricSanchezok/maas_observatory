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
from tooluse_bench.runner import run_experiment, select_deployments

app = typer.Typer(
    name="tooluse-bench",
    help="Reproducible tool-use evaluation for OpenAI-compatible deployments.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Inspect and validate model deployments.")
benchmarks_app = typer.Typer(help="Inspect and validate benchmark adapters.")
app.add_typer(models_app, name="models")
app.add_typer(benchmarks_app, name="benchmarks")
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


def main() -> None:
    app()
