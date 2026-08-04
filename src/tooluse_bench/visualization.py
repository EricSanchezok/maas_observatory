"""Deterministic publication figures derived from validated aggregate metrics."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from tooluse_bench.baselines import BaselineRecord, BaselineRegistry, Comparability
from tooluse_bench.benchmarks.bfcl import ALL_PUBLIC_SUBSETS
from tooluse_bench.config import PROJECT_ROOT, load_model_catalog
from tooluse_bench.domain import Lane, StrictModel
from tooluse_bench.records import RunManifest
from tooluse_bench.store import sha256_file

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
    from matplotlib.gridspec import SubplotSpec

    from tooluse_bench.reporting import (
        AggregateResult,
        ReportMetadata,
        TaskGroupAggregate,
    )

FIGURE_FILES = ("benchmark-overview.png", "benchmark-overview.svg")
BLUE = "#1769E0"
BLUE_DARK = "#0C3F93"
BLUE_LIGHT = "#E8F1FF"
AMBER = "#C87918"
INK = "#172033"
MUTED = "#647084"
GRID = "#D9DEE7"
EMPTY = "#F1F3F6"


class FigureMetadata(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    source_metrics_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    figure_builder_git_commit: str
    figure_builder_package_version: str
    baseline_registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: dict[str, str]

    @model_validator(mode="after")
    def validate_file_inventory(self) -> FigureMetadata:
        if set(self.files) != set(FIGURE_FILES):
            raise ValueError("figure metadata file inventory does not match")
        if any(
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            for digest in self.files.values()
        ):
            raise ValueError("figure metadata contains an invalid SHA-256")
        return self


def _standardized_aggregates(
    aggregates: Sequence[AggregateResult],
) -> dict[tuple[str, str], AggregateResult]:
    return {
        (item.benchmark_id, item.deployment_id): item
        for item in aggregates
        if item.lane is Lane.STANDARDIZED
    }


def _model_labels(
    manifest: RunManifest,
    metrics: dict[tuple[str, str], AggregateResult],
) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    for deployment_id in manifest.selected_deployments:
        candidates = [
            item.model_alias
            for (benchmark_id, candidate_id), item in metrics.items()
            if candidate_id == deployment_id and benchmark_id
        ]
        labels.append((deployment_id, candidates[0] if candidates else deployment_id))
    return labels


def _baselines_by_deployment(
    manifest: RunManifest,
    baselines: BaselineRegistry,
) -> dict[str, list[BaselineRecord]]:
    catalog_path = Path(manifest.catalog_path)
    if not catalog_path.is_absolute():
        catalog_path = PROJECT_ROOT / catalog_path
    catalog = load_model_catalog(catalog_path)

    def model_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.split(" (", 1)[0].casefold())

    upstream_by_deployment = {
        deployment.deployment_id: model_key(deployment.upstream_model)
        for deployment in catalog.deployments
    }
    output: dict[str, list[BaselineRecord]] = {}
    for deployment_id in manifest.selected_deployments:
        upstream = upstream_by_deployment.get(deployment_id, "")
        output[deployment_id] = [
            baseline
            for baseline in baselines.baselines
            if model_key(baseline.upstream_model) == upstream
        ]
    return output


def _render_score_panel(
    axis: Axes,
    *,
    benchmark_id: str,
    title: str,
    models: list[tuple[str, str]],
    metrics: dict[tuple[str, str], AggregateResult],
    baselines: dict[str, list[BaselineRecord]],
) -> None:
    axis.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=12)
    positions = list(range(len(models)))[::-1]
    for position, (deployment_id, _label) in zip(positions, models, strict=True):
        metric = metrics.get((benchmark_id, deployment_id))
        official = next(
            (
                item
                for item in baselines.get(deployment_id, ())
                if item.benchmark_id == benchmark_id and item.metric == "pass_at_1"
            ),
            None,
        )
        axis.barh(position, 100, height=0.54, color=EMPTY, edgecolor="none")
        if metric is None or metric.pass_at_1 is None:
            axis.barh(
                position,
                100,
                height=0.54,
                facecolor="none",
                edgecolor=GRID,
                hatch="////",
                linewidth=0.7,
            )
            value_label = "NOT RUN"
        else:
            passed = metric.pass_at_1 * 100
            error = (metric.error_rate or 0) * 100
            axis.barh(position, passed, height=0.54, color=BLUE, edgecolor="none")
            if error:
                axis.barh(
                    position,
                    error,
                    left=passed,
                    height=0.54,
                    facecolor="none",
                    edgecolor=AMBER,
                    hatch="////",
                    linewidth=0.8,
                )
            qualifier = " · partial" if not metric.complete else ""
            error_label = f" · err {error:.1f}" if error else ""
            value_label = f"{passed:.1f}{error_label}{qualifier}"
        if official is not None:
            filled = (
                official.comparability is Comparability.EXACT
                and metric is not None
                and metric.exact_baseline_id == official.baseline_id
            )
            axis.scatter(
                [official.score],
                [position],
                marker="D",
                s=31,
                facecolor=INK if filled else "white",
                edgecolor=INK,
                linewidth=1,
                zorder=5,
            )
            value_label += f" · official {'◆' if filled else '◇'} {official.score:.1f}"
        axis.text(
            101.5,
            position,
            value_label,
            va="center",
            fontsize=8.1,
            color=INK if metric and metric.pass_at_1 is not None else MUTED,
        )
    axis.set_yticks(positions, [label for _, label in models], fontsize=8.5, color=INK)
    axis.set_xlim(0, 150)
    axis.set_xticks((0, 25, 50, 75, 100), ("0", "25", "50", "75", "100"))
    axis.tick_params(axis="x", labelsize=7.5, colors=MUTED, length=0)
    axis.tick_params(axis="y", length=0, pad=5)
    axis.grid(axis="x", color=GRID, linewidth=0.65)
    axis.set_axisbelow(True)
    for spine in axis.spines.values():
        spine.set_visible(False)


def _render_official_context(
    figure: Figure,
    grid: SubplotSpec,
    *,
    models: list[tuple[str, str]],
    baselines: dict[str, list[BaselineRecord]],
) -> None:
    nested = grid.subgridspec(
        4,
        3,
        height_ratios=(0.38, 1, 1, 1),
        hspace=0.68,
        wspace=0.22,
    )
    for index, (deployment_id, label) in enumerate(models):
        axis = figure.add_subplot(nested[index // 3 + 1, index % 3])
        axis.set_axis_off()
        axis.set_title(
            label,
            loc="left",
            fontsize=9.5,
            fontweight="bold",
            color=INK,
            pad=5,
        )
        records = baselines.get(deployment_id, [])
        if not records:
            axis.text(
                0,
                0.65,
                "No official tool-use score found",
                transform=axis.transAxes,
                color=MUTED,
                fontsize=8,
            )
            continue
        for row, record in enumerate(records[:4]):
            benchmark = record.benchmark_id.replace("-verified", " Verified")
            metric = record.metric.replace("pass_at_1", "Pass@1").replace(
                "pass_pow_3", "Pass^3"
            )
            axis.text(
                0,
                0.82 - row * 0.22,
                f"{benchmark} · {metric}",
                transform=axis.transAxes,
                color=MUTED,
                fontsize=7.2,
            )
            axis.text(
                0.98,
                0.82 - row * 0.22,
                f"{record.score:.1f}",
                transform=axis.transAxes,
                ha="right",
                color=INK,
                fontsize=8.1,
                fontweight="bold",
            )


def _render_bfcl_matrix(
    axis: Axes,
    *,
    models: list[tuple[str, str]],
    metrics: dict[tuple[str, str], AggregateResult],
) -> None:
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import Rectangle

    available_groups = {
        group.group_id
        for deployment_id, _ in models
        if (metric := metrics.get(("bfcl-v4", deployment_id))) is not None
        for group in metric.task_groups
    }
    subsets = [
        subset for subset in ALL_PUBLIC_SUBSETS if subset in available_groups
    ] + sorted(available_groups - set(ALL_PUBLIC_SUBSETS))
    axis.set_title(
        "BFCL V4 capability map",
        loc="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
        pad=18,
    )
    if not subsets:
        axis.text(
            0.5,
            0.5,
            "BFCL task-level subset evidence was not produced.",
            ha="center",
            va="center",
            color=MUTED,
            transform=axis.transAxes,
        )
        axis.set_axis_off()
        return

    values: list[list[float]] = []
    groups_by_model: list[dict[str, TaskGroupAggregate]] = []
    for deployment_id, _ in models:
        metric = metrics.get(("bfcl-v4", deployment_id))
        groups = (
            {group.group_id: group for group in metric.task_groups}
            if metric is not None
            else {}
        )
        groups_by_model.append(groups)
        values.append(
            [
                (
                    float(group.pass_at_1)
                    if (group := groups.get(subset)) is not None
                    and group.pass_at_1 is not None
                    else math.nan
                )
                for subset in subsets
            ]
        )

    color_map = LinearSegmentedColormap.from_list(
        "tooluse_blue",
        ("#F4F7FB", "#C8DCFA", "#6EA6F0", BLUE_DARK),
    ).with_extremes(
        bad=EMPTY,
    )
    axis.imshow(values, cmap=color_map, vmin=0, vmax=1, aspect="auto")
    for row, groups in enumerate(groups_by_model):
        for column, subset in enumerate(subsets):
            group = groups.get(subset)
            if group is None or group.pass_at_1 is None:
                label = "—"
                color = MUTED
            else:
                label = f"{group.pass_at_1 * 100:.0f}"
                color = "white" if group.pass_at_1 >= 0.62 else INK
                if not group.complete:
                    axis.add_patch(
                        Rectangle(
                            (column - 0.5, row - 0.5),
                            1,
                            1,
                            facecolor="none",
                            edgecolor=AMBER,
                            hatch="////",
                            linewidth=1,
                        )
                    )
            axis.text(
                column,
                row,
                label,
                ha="center",
                va="center",
                fontsize=7,
                color=color,
            )
    axis.set_yticks(
        range(len(models)),
        [label for _, label in models],
        fontsize=8.5,
        color=INK,
    )
    axis.set_xticks(
        range(len(subsets)),
        [subset.replace("multi_turn_", "mt_") for subset in subsets],
        rotation=52,
        ha="right",
        fontsize=7,
        color=INK,
    )
    axis.tick_params(length=0)
    axis.set_xticks(
        [value - 0.5 for value in range(1, len(subsets))],
        minor=True,
    )
    axis.set_yticks(
        [value - 0.5 for value in range(1, len(models))],
        minor=True,
    )
    axis.grid(which="minor", color="white", linewidth=1.2)
    for spine in axis.spines.values():
        spine.set_visible(False)


def build_benchmark_figure(
    report_directory: Path,
    manifest: RunManifest,
    aggregates: Sequence[AggregateResult],
    report_metadata: ReportMetadata,
    baselines: BaselineRegistry,
) -> FigureMetadata:
    """Render deterministic overview assets from canonical aggregate metrics."""

    import matplotlib
    from matplotlib import pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    metrics_path = report_directory / "metrics.json"
    metrics = _standardized_aggregates(aggregates)
    models = _model_labels(manifest, metrics)
    deployment_baselines = _baselines_by_deployment(manifest, baselines)

    matplotlib.use("Agg", force=True)
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "svg.fonttype": "none",
            "svg.hashsalt": "maas-observatory-v1",
            "axes.unicode_minus": False,
        }
    )
    figure = plt.figure(figsize=(16, 16), facecolor="white")
    grid = figure.add_gridspec(
        3,
        3,
        height_ratios=(1.0, 1.05, 0.82),
        hspace=0.42,
        wspace=0.28,
        left=0.075,
        right=0.96,
        top=0.88,
        bottom=0.075,
    )
    figure.text(
        0.075,
        0.955,
        "MaaS tool-use evaluation",
        fontsize=23,
        fontweight="bold",
        color=INK,
    )
    figure.text(
        0.075,
        0.92,
        "Standardized lane · task-level Pass@1 (%) · "
        "transport and infrastructure failures retained",
        fontsize=10.5,
        color=MUTED,
    )

    panels = (
        ("probe", "Protocol readiness"),
        ("bfcl-v4", "BFCL V4 overall"),
        ("toolathlon-verified", "Toolathlon-Verified"),
    )
    for column, (benchmark_id, title) in enumerate(panels):
        _render_score_panel(
            figure.add_subplot(grid[0, column]),
            benchmark_id=benchmark_id,
            title=title,
            models=models,
            metrics=metrics,
            baselines=deployment_baselines,
        )
    _render_bfcl_matrix(
        figure.add_subplot(grid[1, :]),
        models=models,
        metrics=metrics,
    )
    context_axis = figure.add_subplot(grid[2, :])
    context_axis.set_axis_off()
    context_axis.set_title(
        "Official published context",
        loc="left",
        fontsize=13,
        fontweight="bold",
        color=INK,
        pad=20,
    )
    context_axis.text(
        0,
        0.99,
        "Different benchmark releases, precisions, or reasoning settings are "
        "contextual (◇); no cross-benchmark delta is calculated.",
        transform=context_axis.transAxes,
        fontsize=8.5,
        color=MUTED,
        va="top",
    )
    _render_official_context(
        figure,
        grid[2, :],
        models=models,
        baselines=deployment_baselines,
    )
    figure.legend(
        handles=(
            Patch(facecolor=BLUE, label="Pass@1"),
            Patch(facecolor=EMPTY, label="Model-scored fail"),
            Patch(
                facecolor="none",
                edgecolor=AMBER,
                hatch="////",
                label="Transport / infrastructure error",
            ),
            Patch(
                facecolor="none",
                edgecolor=GRID,
                hatch="////",
                label="Not run / missing evidence",
            ),
            Line2D(
                [],
                [],
                color=INK,
                marker="D",
                markerfacecolor="white",
                linestyle="none",
                label="◇ official contextual score",
            ),
        ),
        loc="lower left",
        bbox_to_anchor=(0.075, 0.018),
        ncol=5,
        frameon=False,
        fontsize=8.5,
    )
    figure.text(
        0.96,
        0.028,
        f"run {manifest.run_id} · report builder "
        f"{report_metadata.report_builder_git_commit[:8]} · no composite score",
        ha="right",
        fontsize=7.7,
        color=MUTED,
    )

    svg_path = report_directory / "benchmark-overview.svg"
    png_path = report_directory / "benchmark-overview.png"
    figure.savefig(
        svg_path,
        format="svg",
        metadata={"Date": None, "Creator": "maas-observatory"},
    )
    figure.savefig(
        png_path,
        format="png",
        dpi=180,
        metadata={"Software": "maas-observatory"},
    )
    plt.close(figure)

    metadata = FigureMetadata(
        run_id=manifest.run_id,
        source_metrics_sha256=sha256_file(metrics_path),
        figure_builder_git_commit=report_metadata.report_builder_git_commit,
        figure_builder_package_version=report_metadata.report_builder_package_version,
        baseline_registry_sha256=report_metadata.baseline_registry_sha256,
        files={
            filename: sha256_file(report_directory / filename)
            for filename in FIGURE_FILES
        },
    )
    (report_directory / "figure-metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata
