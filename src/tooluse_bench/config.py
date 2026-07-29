"""Configuration loading with strict schema validation and no implicit secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from maas_common.catalog import (
    DEFAULT_CATALOG,
    DEFAULT_DOTENV,
    PROJECT_ROOT,
    load_dotenv,
    load_model_catalog,
)
from tooluse_bench.baselines import BaselineRegistry
from tooluse_bench.domain import ExperimentPlan, ModelCatalog, ModelDeployment

DEFAULT_BASELINES = PROJECT_ROOT / "config" / "baselines.yaml"
DEFAULT_EXPERIMENT = PROJECT_ROOT / "config" / "experiments" / "release-v1.yaml"

__all__ = [
    "DEFAULT_BASELINES",
    "DEFAULT_CATALOG",
    "DEFAULT_DOTENV",
    "DEFAULT_EXPERIMENT",
    "PROJECT_ROOT",
    "load_baselines",
    "load_catalog",
    "load_dotenv",
    "load_experiment",
    "load_model_catalog",
    "resolve_models",
]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _validate_yaml[SchemaT: BaseModel](path: Path, schema: type[SchemaT]) -> SchemaT:
    return schema.model_validate(_load_yaml(path))


def load_experiment(path: Path = DEFAULT_EXPERIMENT) -> ExperimentPlan:
    return _validate_yaml(path, ExperimentPlan)


def load_baselines(path: Path = DEFAULT_BASELINES) -> BaselineRegistry:
    return _validate_yaml(path, BaselineRegistry)


def load_catalog(path: Path = DEFAULT_CATALOG) -> list[ModelDeployment]:
    """Compatibility helper returning validated deployments."""

    return load_model_catalog(path).deployments


def resolve_models(
    aliases: list[str] | None,
    all_models: bool,
    *,
    catalog: ModelCatalog | None = None,
) -> list[ModelDeployment]:
    deployments = (catalog or load_model_catalog()).deployments
    if all_models:
        return deployments
    if not aliases:
        raise ValueError("select at least one --model, or pass --all")

    by_alias = {model.alias: model for model in deployments}
    unknown = sorted(set(aliases) - set(by_alias))
    if unknown:
        raise ValueError(
            f"unknown model alias(es): {', '.join(unknown)}; "
            f"available: {', '.join(by_alias)}"
        )
    return [by_alias[alias] for alias in aliases]
