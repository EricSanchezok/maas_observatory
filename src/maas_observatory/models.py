"""Internal and public schemas for MaaS Observatory."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quality(StrEnum):
    EXACT = "exact"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ErrorClass(StrEnum):
    TRANSPORT = "transport_error"
    SERVICE = "service_error"
    MEASUREMENT = "measurement_error"
    NONE = "none"


class ServiceState(StrEnum):
    OPERATIONAL = "operational"
    SLOW = "slow"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class TelemetryState(StrEnum):
    FRESH = "fresh"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ExperienceState(StrEnum):
    FRESH = "experience_fresh"
    STALE = "experience_stale"
    UNAVAILABLE = "experience_unavailable"
    COLLECTING = "experience_collecting"


class ProbeKind(StrEnum):
    ROUTE = "route"
    CANARY = "canary"
    EXPERIENCE_SHORT = "experience_short"
    SPEED = "speed"
    EXPERIENCE_CONTEXT = "experience_context"
    CONFIRMATION = "confirmation"


class ProbeOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class Histogram(FrozenModel):
    buckets: dict[float, float] = Field(default_factory=dict)
    count: float = 0
    total: float = 0


class MetricSnapshot(FrozenModel):
    deployment_id: str
    source_id: str = "legacy-primary"
    observed_at: datetime
    elapsed_seconds: float | None = None
    counters: dict[str, float] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    histograms: dict[str, Histogram] = Field(default_factory=dict)
    quality: Quality = Quality.EXACT
    error_class: ErrorClass = ErrorClass.NONE
    error_code: str | None = None


class IntervalMetrics(FrozenModel):
    deployment_id: str
    source_id: str = "legacy-primary"
    started_at: datetime
    ended_at: datetime
    values: dict[str, float | None]
    histograms: dict[str, Histogram] = Field(default_factory=dict)
    sample_count: int = 0
    quality: Quality
    reason: str | None = None


class ProbeResult(FrozenModel):
    deployment_id: str
    kind: ProbeKind
    scheduled_at: datetime
    started_at: datetime
    finished_at: datetime
    outcome: ProbeOutcome
    error_class: ErrorClass = ErrorClass.NONE
    error_code: str | None = None
    profile_id: str | None = None
    definition_version: str = "1"
    vantage_id: str | None = None
    confirmation_of: int | None = None
    measurements: dict[str, float | int | str | None] = Field(default_factory=dict)


class PublicPoint(FrozenModel):
    timestamp: datetime
    value: float | int | None
    unit: str
    source_kind: str
    observation_scope: str
    quality: Literal["exact", "incomplete", "unavailable"]
    sample_count: int
    profile_id: str | None = None
    definition_version: str = "1"
    vantage_id: str | None = None
    coverage: float | None = None
    measured_at: datetime | None = None
    reason: str | None = None


class ApiEnvelope(FrozenModel):
    schema_version: Literal["2"] = "2"
    generated_at: datetime = Field(default_factory=utc_now)
    data_window: str
    freshness_seconds: float | None
    sample_count: int
    source_mix: dict[str, int]
    quality: Literal["exact", "incomplete", "unavailable"]
    data: Any
