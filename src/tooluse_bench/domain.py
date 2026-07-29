"""Validated public domain models for configuration and experiment planning."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from maas_common.catalog import (
    Capabilities,
    CatalogDefaults,
    EndpointReference,
    ModelCatalog,
    ModelDeployment,
    Precision,
    RequestProfile,
    ServingMetadata,
    StrictModel,
)

__all__ = [
    "BenchmarkSelection",
    "Capabilities",
    "CatalogDefaults",
    "EndpointReference",
    "ExperimentPlan",
    "Lane",
    "ModelCatalog",
    "ModelDeployment",
    "Precision",
    "RequestProfile",
    "ServingMetadata",
    "StrictModel",
]


class Lane(StrEnum):
    STANDARDIZED = "standardized"
    OFFICIAL_REPRODUCTION = "official-reproduction"


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
