"""Configuration loading with strict schema validation and no implicit secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from tooluse_bench.domain import ExperimentPlan, ModelCatalog, ModelDeployment

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "config" / "models.yaml"
DEFAULT_EXPERIMENT = PROJECT_ROOT / "config" / "experiments" / "release-v1.yaml"
DEFAULT_DOTENV = PROJECT_ROOT / ".env"


def load_dotenv(path: Path = DEFAULT_DOTENV) -> None:
    """Load the conservative KEY=VALUE subset used by this project.

    Existing process environment values always win. Export syntax, interpolation,
    multiline values, and command substitution are intentionally unsupported.
    """

    if not path.exists():
        return

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "A").isalnum() or not key[0].isalpha():
            raise ValueError(f"{path}:{line_number}: invalid environment name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


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


def load_model_catalog(path: Path = DEFAULT_CATALOG) -> ModelCatalog:
    return _validate_yaml(path, ModelCatalog)


def load_experiment(path: Path = DEFAULT_EXPERIMENT) -> ExperimentPlan:
    return _validate_yaml(path, ExperimentPlan)


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
