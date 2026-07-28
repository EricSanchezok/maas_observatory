"""Versioned official and independent comparison baselines."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, HttpUrl, model_validator

from tooluse_bench.domain import StrictModel


class BaselineSourceKind(StrEnum):
    VENDOR_REPORT = "vendor_report"
    BENCHMARK_LEADERBOARD = "benchmark_leaderboard"
    INDEPENDENT_CONTROL = "independent_control"


class Comparability(StrEnum):
    EXACT = "exact"
    CONTEXTUAL = "contextual"
    INCOMPATIBLE = "incompatible"


class BaselineRecord(StrictModel):
    baseline_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    upstream_model: str
    benchmark_id: str
    benchmark_release: str
    metric: str
    score: float = Field(ge=0, le=100)
    scale: Literal["percent"] = "percent"
    precision: str
    reasoning_mode: str
    source_kind: BaselineSourceKind
    source_url: HttpUrl
    accessed_at: date
    comparability: Comparability
    compatible_deployments: tuple[str, ...] = ()
    compatible_configurations_sha256: tuple[str, ...] = ()
    settings: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @model_validator(mode="after")
    def exact_baselines_require_compatible_deployments(self) -> BaselineRecord:
        if self.comparability is Comparability.EXACT:
            if not self.compatible_deployments:
                raise ValueError(
                    "exact baselines must list at least one compatible deployment"
                )
            if not self.compatible_configurations_sha256:
                raise ValueError(
                    "exact baselines must list at least one compatible "
                    "configuration SHA-256"
                )
            if not self.settings:
                raise ValueError("exact baselines must document comparison settings")
        return self


class BaselineRegistry(StrictModel):
    schema_version: Literal[1]
    baselines: list[BaselineRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> BaselineRegistry:
        identifiers = [item.baseline_id for item in self.baselines]
        duplicates = sorted(
            {value for value in identifiers if identifiers.count(value) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate baseline IDs: {', '.join(duplicates)}")
        return self
