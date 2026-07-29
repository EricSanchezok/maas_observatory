"""Validated observability configuration."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from maas_common.catalog import PROJECT_ROOT, StrictModel

DEFAULT_OBSERVABILITY_CONFIG = PROJECT_ROOT / "config" / "observability.yaml"


class ServerSettings(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)


class StorageSettings(StrictModel):
    root: Path = Path("var/maas-observatory")
    database: str = "observatory.sqlite3"
    writer_queue_size: int = Field(default=2048, ge=16)
    raw_retention_days: int = Field(default=7, ge=1)
    minute_retention_days: int = Field(default=30, ge=1)
    five_minute_retention_days: int = Field(default=365, ge=1)
    probe_retention_days: int = Field(default=365, ge=1)
    daily_backups: int = Field(default=7, ge=1)
    weekly_backups: int = Field(default=4, ge=1)

    def root_path(self, project_root: Path = PROJECT_ROOT) -> Path:
        return self.root if self.root.is_absolute() else project_root / self.root


class ScrapeSettings(StrictModel):
    interval_seconds: int = Field(default=15, ge=5)
    timeout_seconds: float = Field(default=12, gt=0)
    max_concurrency: int = Field(default=4, ge=1)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    metrics_path: str = Field(default="/metrics", pattern=r"^/")
    p95_min_samples: int = Field(default=20, ge=1)


ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class MetricsSourceSettings(StrictModel):
    """A stable, instance-scoped Prometheus source reference."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    url_env: str
    api_key_env: str

    @model_validator(mode="after")
    def validate_environment_names(self) -> MetricsSourceSettings:
        for value in (self.url_env, self.api_key_env):
            if not ENV_NAME_PATTERN.fullmatch(value):
                raise ValueError(f"invalid environment variable name: {value}")
        return self

    @property
    def url(self) -> str | None:
        return os.getenv(self.url_env) or None

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env) or None


class DailyBudget(StrictModel):
    short_requests: int = Field(default=48, ge=0)
    context_requests: int = Field(default=4, ge=0)
    canary_requests: int = Field(default=96, ge=0)
    experience_requests: int = Field(default=52, ge=0)
    output_tokens: int = Field(default=3584, ge=0)
    input_tokens: int = Field(default=25000, ge=0)
    # Read-only compatibility for v1 configuration/tests; v2 scheduling ignores these.
    speed_requests: int = Field(default=48, ge=0)
    inference_requests: int = Field(default=150, ge=0)


class ProbeSettings(StrictModel):
    route_interval_seconds: int = Field(default=60, ge=10)
    canary_min_interval_seconds: int = Field(default=900, ge=60)
    diagnostic_canary_after_seconds: int = Field(default=7200, ge=300)
    canary_idle_seconds: int = Field(default=300, ge=30)
    short_min_interval_seconds: int = Field(default=1800, ge=60)
    short_dispatch_interval_seconds: int = Field(default=90, ge=10)
    speed_dispatch_interval_seconds: int | None = Field(default=None, ge=10)
    context_min_interval_seconds: int = Field(default=21600, ge=600)
    confirmation_delay_seconds: int = Field(default=60, ge=10)
    stream_stall_seconds: int = Field(default=30, ge=1)
    canary_max_output_tokens: int = Field(default=8, ge=1)
    short_max_output_tokens: int = Field(default=64, ge=2)
    context_max_output_tokens: int = Field(default=128, ge=2)
    short_kv_cache_limit: float = Field(default=0.70, ge=0, le=1)
    context_kv_cache_limit: float = Field(default=0.50, ge=0, le=1)
    telemetry_max_age_seconds: int = Field(default=45, ge=10)
    production_idle_seconds: int = Field(default=30, ge=5)
    daily_budget: DailyBudget = Field(default_factory=DailyBudget)

    @property
    def speed_min_interval_seconds(self) -> int:
        return self.short_min_interval_seconds

    @property
    def speed_max_output_tokens(self) -> int:
        return self.short_max_output_tokens

    @property
    def kv_cache_limit(self) -> float:
        return self.short_kv_cache_limit


class ExperienceSettings(StrictModel):
    vantage_id: str = Field(default="observatory-primary", min_length=1)
    short_profile_id: str = "interactive-short-v1"
    context_profile_id: str = "context-16k-v1"
    definition_version: str = "1"
    short_fresh_seconds: int = Field(default=2700, ge=60)
    short_stale_seconds: int = Field(default=5400, ge=60)
    short_unavailable_seconds: int = Field(default=7200, ge=60)
    baseline_min_samples: int = Field(default=20, ge=3)

    @model_validator(mode="after")
    def validate_freshness_thresholds(self) -> ExperienceSettings:
        if not (
            self.short_fresh_seconds
            < self.short_stale_seconds
            < self.short_unavailable_seconds
        ):
            raise ValueError("experience freshness thresholds must increase")
        return self


class StateSettings(StrictModel):
    telemetry_partial_seconds: int = Field(default=45, ge=1)
    telemetry_stale_seconds: int = Field(default=60, ge=1)
    telemetry_unavailable_seconds: int = Field(default=300, ge=1)
    service_error_rate: float = Field(default=0.05, gt=0, lt=1)
    service_error_min_samples: int = Field(default=20, ge=1)
    passive_baseline_buckets: int = Field(default=96, ge=1)
    speed_baseline_samples: int = Field(default=20, ge=1)
    ttft_slow_multiplier: float = Field(default=2, gt=1)
    speed_slow_ratio: float = Field(default=0.70, gt=0, lt=1)

    @model_validator(mode="after")
    def validate_telemetry_thresholds(self) -> StateSettings:
        if not (
            self.telemetry_partial_seconds
            < self.telemetry_stale_seconds
            < self.telemetry_unavailable_seconds
        ):
            raise ValueError("telemetry thresholds must increase")
        return self


class PublicSettings(StrictModel):
    metric_fields: list[str] = Field(min_length=1)


class ObservatorySettings(StrictModel):
    schema_version: Literal[2]
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    scrape: ScrapeSettings = Field(default_factory=ScrapeSettings)
    metrics_sources: dict[str, list[MetricsSourceSettings]]
    probes: ProbeSettings = Field(default_factory=ProbeSettings)
    profiles: dict[str, str]
    experience: ExperienceSettings = Field(default_factory=ExperienceSettings)
    state: StateSettings = Field(default_factory=StateSettings)
    public: PublicSettings

    @model_validator(mode="after")
    def validate_source_ids(self) -> ObservatorySettings:
        for alias, sources in self.metrics_sources.items():
            ids = [source.source_id for source in sources]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate metrics source_id for {alias}")
        return self


def load_observability_settings(
    path: Path = DEFAULT_OBSERVABILITY_CONFIG,
) -> ObservatorySettings:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    settings = ObservatorySettings.model_validate(payload)
    cors_value = os.getenv("MAAS_OBSERVATORY_CORS_ORIGINS")
    if cors_value is not None:
        origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
        settings = settings.model_copy(
            update={
                "server": settings.server.model_copy(update={"cors_origins": origins})
            }
        )
    return settings
