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


class ErrorClass(StrEnum):
    TRANSPORT = "transport_error"
    SERVICE = "service_error"
    MEASUREMENT = "measurement_error"
    NONE = "none"


class ResponseState(StrEnum):
    COLLECTING = "collecting"
    CURRENT = "current"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"
    MAINTENANCE = "maintenance"


class ProbeKind(StrEnum):
    ROUTE = "route"
    CANARY = "canary"
    EXPERIENCE_SHORT = "experience_short"
    EXPERIENCE_CONTEXT = "experience_context"
    CONFIRMATION = "confirmation"


class ProbeOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


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
    definition_version: str = "3"
    suite_version: str | None = None
    vantage_id: str | None = None
    collection_mode: str | None = None
    fixture_id: str | None = None
    block_id: str | None = None
    scheduler_lag_seconds: float | None = None
    confirmation_of: int | None = None
    measurements: dict[str, float | int | str | None] = Field(default_factory=dict)


class ApiEnvelope(FrozenModel):
    schema_version: Literal["4"] = "4"
    generated_at: datetime = Field(default_factory=utc_now)
    data_window: str
    freshness_seconds: float | None
    sample_count: int
    source_mix: dict[str, int]
    quality: Literal["exact", "incomplete", "unavailable"]
    data: Any
