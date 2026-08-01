"""Validated MaaS Observatory configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from maas_common.catalog import PROJECT_ROOT, StrictModel

DEFAULT_OBSERVABILITY_CONFIG = PROJECT_ROOT / "config" / "observability.yaml"
CollectionMode = Literal["rapid", "standard"]


class ServerSettings(StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=list)


class StorageSettings(StrictModel):
    root: Path = Path("var/maas-observatory")
    database: str = "observatory.sqlite3"
    writer_queue_size: int = Field(default=2048, ge=16)
    probe_retention_days: int = Field(default=365, ge=1)
    daily_backups: int = Field(default=7, ge=1)
    weekly_backups: int = Field(default=4, ge=1)

    def root_path(self, project_root: Path = PROJECT_ROOT) -> Path:
        return self.root if self.root.is_absolute() else project_root / self.root


class DailyBudget(StrictModel):
    requests: int = Field(default=240, ge=0)
    input_tokens: int = Field(default=5_000_000, ge=0)
    output_tokens: int = Field(default=3_932_160, ge=0)


class ProbeSettings(StrictModel):
    route_interval_seconds: int = Field(default=60, ge=10)
    confirmation_delay_seconds: int = Field(default=60, ge=10)
    response_start_timeout_seconds: int = Field(default=180, ge=10)
    stream_stall_seconds: int = Field(default=30, ge=1)
    canary_max_output_tokens: int = Field(default=8, ge=1)
    experience_max_output_tokens: int = Field(default=16384, ge=2)
    rapid_block_interval_seconds: int = Field(default=60, ge=10)
    rapid_context_tier: Literal["1k", "16k", "64k"] | None = None
    standard_block_interval_seconds: int = Field(default=600, ge=60)
    daily_budget: DailyBudget = Field(default_factory=DailyBudget)


class ExperienceSettings(StrictModel):
    vantage_id: str = Field(default="observatory-primary", min_length=1)
    suite_version: str = "response-suite-v5"
    response_profile_id: str = "response-v5"
    definition_version: str = "5"
    summary_min_samples: int = Field(default=6, ge=6)
    baseline_min_samples: int = Field(default=20, ge=3)


class ObservatorySettings(StrictModel):
    schema_version: Literal[4]
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    probes: ProbeSettings = Field(default_factory=ProbeSettings)
    profiles: dict[str, str]
    experience: ExperienceSettings = Field(default_factory=ExperienceSettings)
    collection_mode: CollectionMode = "standard"

    @model_validator(mode="after")
    def validate_rapid_tier(self) -> ObservatorySettings:
        if self.collection_mode == "rapid" and self.probes.rapid_context_tier is None:
            raise ValueError("rapid mode requires probes.rapid_context_tier")
        return self

    def interval_for(self) -> int:
        if self.collection_mode == "rapid":
            return self.probes.rapid_block_interval_seconds
        return self.probes.standard_block_interval_seconds


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
    mode = os.getenv("MAAS_OBSERVATORY_COLLECTION_MODE")
    if mode is not None:
        if mode not in {"rapid", "standard"}:
            raise ValueError(
                "MAAS_OBSERVATORY_COLLECTION_MODE must be rapid or standard"
            )
        payload = {**payload, "collection_mode": mode}
    rapid_tier = os.getenv("MAAS_OBSERVATORY_RAPID_CONTEXT_TIER")
    if rapid_tier is not None:
        if rapid_tier not in {"1k", "16k", "64k"}:
            raise ValueError(
                "MAAS_OBSERVATORY_RAPID_CONTEXT_TIER must be 1k, 16k, or 64k"
            )
        probes = dict(payload.get("probes") or {})
        probes["rapid_context_tier"] = rapid_tier
        payload = {**payload, "probes": probes}
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
