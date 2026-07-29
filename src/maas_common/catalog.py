"""Secret-safe model catalog schemas and loading helpers."""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "config" / "models.yaml"
DEFAULT_DOTENV = PROJECT_ROOT / ".env"
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class StrictModel(BaseModel):
    """Base class for immutable, forward-versioned public schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Precision(StrEnum):
    """Deployment precision names accepted by the catalog."""

    BF16 = "BF16"
    FP8_MIXED = "FP8-mixed"
    W4A8 = "W4A8"
    W8A8 = "W8A8"
    UNKNOWN = "unknown"


class EndpointReference(StrictModel):
    base_url_env: str
    api_key_env: str

    @model_validator(mode="after")
    def validate_environment_names(self) -> EndpointReference:
        for value in (self.base_url_env, self.api_key_env):
            if not ENV_NAME_PATTERN.fullmatch(value):
                raise ValueError(f"invalid environment variable name: {value}")
        if self.base_url_env == self.api_key_env:
            raise ValueError("base URL and API key must use different variables")
        return self


class Capabilities(StrictModel):
    reasoning: bool
    temperature: bool
    tool_call_declared: bool


class ServingMetadata(StrictModel):
    engine: str = "unknown"
    chat_template: str = "unknown"
    reasoning_parser: str = "unknown"
    tool_call_parser: str = "unknown"


class RequestProfile(StrictModel):
    request_overrides: dict[str, Any] = Field(default_factory=dict)


class ModelDeployment(StrictModel):
    deployment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    alias: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    name: str
    provider: str
    family: str
    upstream_model: str
    upstream_source: HttpUrl
    model_id: str
    precision: Precision
    context_limit: int = Field(gt=0)
    output_limit: int = Field(gt=0)
    input_modalities: list[str] = Field(min_length=1)
    endpoint: EndpointReference
    capabilities: Capabilities
    serving: ServingMetadata = Field(default_factory=ServingMetadata)
    timeout_seconds: float | None = Field(default=None, gt=0)
    request_defaults: dict[str, Any] = Field(default_factory=dict)
    profiles: dict[str, RequestProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_limits(self) -> ModelDeployment:
        if len(self.input_modalities) != len(set(self.input_modalities)):
            raise ValueError("input_modalities must not contain duplicates")
        return self

    @property
    def base_url(self) -> str | None:
        return os.getenv(self.endpoint.base_url_env) or None

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.endpoint.api_key_env) or None

    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.base_url:
            errors.append(f"missing {self.endpoint.base_url_env}")
        elif not self.base_url.startswith(("https://", "http://")):
            errors.append(f"{self.endpoint.base_url_env} must be an HTTP(S) URL")
        if not self.api_key:
            errors.append(f"missing {self.endpoint.api_key_env}")
        return errors


class CatalogDefaults(StrictModel):
    timeout_seconds: float = Field(default=600, gt=0)


class ModelCatalog(StrictModel):
    schema_version: Literal[1]
    defaults: CatalogDefaults = Field(default_factory=CatalogDefaults)
    deployments: list[ModelDeployment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> ModelCatalog:
        for field_name in ("deployment_id", "alias"):
            values = [getattr(item, field_name) for item in self.deployments]
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"duplicate {field_name}(s): {', '.join(duplicates)}")
        return self


def load_dotenv(path: Path = DEFAULT_DOTENV) -> None:
    """Load the conservative KEY=VALUE subset used by this repository."""

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
        key, value = key.strip(), value.strip()
        if not ENV_NAME_PATTERN.fullmatch(key):
            raise ValueError(f"{path}:{line_number}: invalid environment name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_model_catalog(path: Path = DEFAULT_CATALOG) -> ModelCatalog:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return ModelCatalog.model_validate(payload)
