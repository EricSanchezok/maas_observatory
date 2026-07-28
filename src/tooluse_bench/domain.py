"""Validated public domain models for configuration and experiment planning."""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class StrictModel(BaseModel):
    """Base class for immutable, forward-versioned public schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Precision(StrEnum):
    BF16 = "BF16"
    FP8_MIXED = "FP8-mixed"
    W4A8 = "W4A8"
    W8A8 = "W8A8"
    UNKNOWN = "unknown"


class Lane(StrEnum):
    STANDARDIZED = "standardized"
    OFFICIAL_REPRODUCTION = "official-reproduction"


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


class BenchmarkSelection(StrictModel):
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    profile: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    trials: int = Field(default=3, ge=1, le=100)
    options: dict[str, Any] = Field(default_factory=dict)


class ExperimentPlan(StrictModel):
    schema_version: Literal[1]
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    description: str
    models: list[str] = Field(min_length=1)
    lanes: list[Lane] = Field(min_length=1)
    output_root: Path = Path("runs")
    benchmarks: list[BenchmarkSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selections(self) -> ExperimentPlan:
        if "*" in self.models and self.models != ["*"]:
            raise ValueError("'*' must be the only model selector")
        if len(self.models) != len(set(self.models)):
            raise ValueError("models must not contain duplicates")
        if len(self.lanes) != len(set(self.lanes)):
            raise ValueError("lanes must not contain duplicates")
        keys = [(item.benchmark_id, item.profile) for item in self.benchmarks]
        if len(keys) != len(set(keys)):
            raise ValueError("benchmark/profile selections must be unique")
        return self
