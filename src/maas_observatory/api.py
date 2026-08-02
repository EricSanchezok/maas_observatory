"""Public, read-only FastAPI contract for real-request measurements (v6)."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from maas_common.catalog import ModelCatalog, ModelDeployment
from maas_observatory.database import Database, isoformat
from maas_observatory.models import ApiEnvelope
from maas_observatory.settings import ObservatorySettings

Window = Literal["1h", "6h", "24h", "7d", "30d"]
WINDOW_SECONDS = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}
SCHEMA_VERSION = "6"
ALL_TIERS = ("1k", "16k", "64k")


class RuntimeHealth:
    def __init__(self) -> None:
        self.ready = False
        self.detail = "starting"


# ------------------------------------------------------------------ helpers ---


def _freshness(timestamp: str | None) -> float | None:
    if timestamp is None:
        return None
    return max(
        0,
        (datetime.now(UTC) - datetime.fromisoformat(timestamp)).total_seconds(),
    )


def _envelope(
    *,
    window: str,
    freshness: float | None,
    sample_count: int,
    source_mix: dict[str, int],
    data: Any,
    quality: Literal["exact", "incomplete", "unavailable"] | None = None,
) -> dict[str, Any]:
    resolved_quality = quality
    if resolved_quality is None:
        resolved_quality = (
            "unavailable"
            if sample_count == 0
            else ("incomplete" if freshness is None else "exact")
        )
    return ApiEnvelope(
        data_window=window,
        freshness_seconds=freshness,
        sample_count=sample_count,
        source_mix=source_mix,
        quality=resolved_quality,
        data=data,
    ).model_dump(mode="json")


def _attempt_reason(
    outcome: str | None,
    error_class: str | None,
    error_code: str | None,
) -> str | None:
    if outcome is None:
        return "first_check_scheduled"
    if outcome == "success":
        return None
    if error_code == "maintenance":
        return "maintenance"
    if error_code and error_code.startswith("daily_"):
        return "scheduled_later"
    if error_class == "measurement_error":
        return "measurement_limited"
    if outcome == "skipped":
        return "scheduled_later"
    return "request_failed"


def _counts_as_path_attempt(row: dict[str, Any]) -> bool:
    if row["outcome"] == "skipped":
        return False
    return not (
        row["outcome"] == "failed" and row["error_class"] == "measurement_error"
    )


def _p50(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: Sequence[float]) -> float | None:
    if len(values) < 10:
        return None
    return statistics.quantiles(values, n=100)[94]


def _uptime_pct(row: dict[str, Any]) -> float | None:
    denominator = int(row["total"]) - int(row["maintenance_excluded"])
    if denominator <= 0:
        return None
    return round(100 * int(row["success"]) / denominator, 2)


def _etag_document(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _etag_document(item)
            for key, item in value.items()
            if key
            not in {
                "generated_at",
                "freshness_seconds",
                "measurement_age_seconds",
            }
        }
    if isinstance(value, list):
        return [_etag_document(item) for item in value]
    return value


# ------------------------------------------------------------- tier helpers ---


def _fixtures_for_tier(tier: str) -> set[str]:
    """A/B fixture IDs that must be observed for *tier* completeness."""
    return {f"agent-{tier}-a", f"agent-{tier}-b"}


def _reasoning_enabled(
    deployment: ModelDeployment,
    operational_profile: str,
) -> bool:
    if not deployment.capabilities.reasoning:
        return False
    if operational_profile == "default-only":
        return True
    profile = deployment.profiles.get(operational_profile)
    if profile is None:
        return True
    overrides = profile.request_overrides
    template_kwargs = overrides.get("chat_template_kwargs", {})
    if isinstance(template_kwargs, dict):
        for key in ("thinking", "enable_thinking"):
            value = template_kwargs.get(key)
            if isinstance(value, bool):
                return value
    return True


def _build_tier_experience(
    runs: list[dict[str, Any]],
    reasoning_deployment: bool,
) -> dict[str, Any]:
    """Return a TierExperience dict from rows for a single (deployment, tier)."""
    attempts = [r for r in runs if r["outcome"] != "skipped"]
    successful: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in runs:
        measurement = json.loads(row["measurement_json"])
        if (
            row["outcome"] == "success"
            and measurement.get("first_response_seconds") is not None
        ):
            successful.append((row, measurement))

    fixtures = {r["fixture_id"] for r, _ in successful if r["fixture_id"]}
    sample_count = len(successful)
    expected = _fixtures_for_tier(runs[0]["context_tier"] if runs else ALL_TIERS[0])
    complete = sample_count > 0 and expected.issubset(fixtures)

    first_token = [
        float(m.get("first_token_seconds", m.get("stream_start_seconds", 0)))
        for _, m in successful
        if m.get("first_token_seconds") is not None
        or m.get("stream_start_seconds") is not None
    ]
    first_response = [float(m["first_response_seconds"]) for _, m in successful]
    output_speed = [
        float(m["output_speed_tps"])
        for _, m in successful
        if m.get("output_speed_tps") is not None
    ]
    total_response = [
        float(m["total_response_seconds"])
        for _, m in successful
        if m.get("total_response_seconds") is not None
    ]
    reasoning_tokens: list[float] = []
    reported_reasoning_count = 0
    if reasoning_deployment:
        for _, measurement in successful:
            reported = measurement.get("reported_reasoning_tokens")
            estimated = measurement.get("reasoning_tokens_estimated")
            if reported is not None:
                reasoning_tokens.append(float(reported))
                reported_reasoning_count += 1
            elif estimated is not None:
                reasoning_tokens.append(float(estimated))
    reasoning_tokens_quality = (
        "reported"
        if reasoning_tokens and reported_reasoning_count == len(reasoning_tokens)
        else ("estimated" if reasoning_tokens else "unavailable")
    )
    reported_prompt_tokens = [
        float(m["reported_prompt_tokens"])
        for _, m in successful
        if m.get("reported_prompt_tokens") is not None
    ]
    ref_prompt_tokens = [
        float(m["ref_prompt_tokens"])
        for _, m in successful
        if m.get("ref_prompt_tokens") is not None
    ]
    prompt_token_deviations = [
        float(m["prompt_token_deviation_pct"])
        for _, m in successful
        if m.get("prompt_token_deviation_pct") is not None
    ]
    prompt_token_quality = (
        "reference_mismatch"
        if any(
            m.get("prompt_token_quality") == "reference_mismatch" for _, m in successful
        )
        else ("reported" if prompt_token_deviations else "unavailable")
    )

    latest = successful[-1] if successful else None
    latest_attempt = attempts[-1] if attempts else None
    latest_payload = None
    measured_at = None
    if latest:
        row, measurement = latest
        measured_at = row["finished_at"]
        latest_payload = {
            "measured_at": measured_at,
            "first_token_seconds": measurement.get(
                "first_token_seconds",
                measurement.get("stream_start_seconds"),
            ),
            "first_response_seconds": measurement["first_response_seconds"],
            "total_response_seconds": measurement.get("total_response_seconds"),
            "output_speed_tps": measurement.get("output_speed_tps"),
            "reported_prompt_tokens": measurement.get("reported_prompt_tokens"),
            "ref_prompt_tokens": measurement.get("ref_prompt_tokens"),
            "prompt_token_deviation_pct": measurement.get("prompt_token_deviation_pct"),
            "prompt_token_quality": measurement.get("prompt_token_quality"),
            "reported_completion_tokens": measurement.get("reported_completion_tokens"),
            "fixture_id": row["fixture_id"],
            "block_id": row["block_id"],
            "scheduler_lag_seconds": row["scheduler_lag_seconds"],
        }

    return {
        "sample_count": sample_count,
        "fixture_count": len(fixtures),
        "complete_fixture_set": complete,
        "first_token_p50": _p50(first_token),
        "first_token_p95": _p95(first_token),
        "first_response_p50": _p50(first_response),
        "first_response_p95": _p95(first_response),
        "total_response_p50": _p50(total_response),
        "total_response_p95": _p95(total_response),
        "output_speed_p50": _p50(output_speed),
        "output_speed_p95": _p95(output_speed),
        "reasoning_tokens_p50": _p50(reasoning_tokens),
        "reasoning_tokens_quality": reasoning_tokens_quality,
        "reported_prompt_tokens_p50": _p50(reported_prompt_tokens),
        "ref_prompt_tokens_p50": _p50(ref_prompt_tokens),
        "prompt_token_deviation_p50": _p50(prompt_token_deviations),
        "prompt_token_quality": prompt_token_quality,
        "latest": latest_payload,
        "latest_attempt_outcome": (
            latest_attempt["outcome"] if latest_attempt else None
        ),
        "latest_attempt_error_class": (
            latest_attempt["error_class"] if latest_attempt else None
        ),
        "latest_attempt_error_code": (
            latest_attempt["error_code"] if latest_attempt else None
        ),
        "latest_attempt_reason": _attempt_reason(
            latest_attempt["outcome"] if latest_attempt else None,
            latest_attempt["error_class"] if latest_attempt else None,
            latest_attempt["error_code"] if latest_attempt else None,
        ),
        "latest_attempt_at": (
            latest_attempt["finished_at"] if latest_attempt else None
        ),
        "measurement_age_seconds": _freshness(measured_at),
        "response_state": None,  # filled at deployment level
        "state_reasons": [],
    }


# --------------------------------------------------------- aggregated query ---


async def _query_tiered_runs(
    database: Database,
    settings: ObservatorySettings,
    cutoff: str,
    deployment_id: str | None = None,
    extra_where: str = "",
    extra_params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """All experience runs filtered by suite/profile/definition/mode/tier."""
    params: list[Any] = [
        settings.experience.response_profile_id,
        settings.experience.definition_version,
        settings.experience.suite_version,
        settings.collection_mode,
        cutoff,
    ]
    if deployment_id is not None:
        params.insert(0, deployment_id)
    params.extend(extra_params)
    return await database.query(
        f"""
        SELECT deployment_id, finished_at, outcome, error_class, error_code,
               profile_id, definition_version, suite_version,
               vantage_id, collection_mode, fixture_id, block_id,
               scheduler_lag_seconds, context_tier, measurement_json
        FROM probe_runs
        WHERE {"deployment_id=?" if deployment_id is not None else "1=1"}
          AND kind='experience'
          AND profile_id=?
          AND definition_version=? AND suite_version=?
          AND collection_mode=? AND finished_at>=?
          AND context_tier IS NOT NULL
          {extra_where}
        ORDER BY finished_at
        """,
        tuple(params),
    )


# ---------------------------------------------------------------- app build ---


def create_app(
    database: Database,
    catalog: ModelCatalog,
    settings: ObservatorySettings,
    health: RuntimeHealth,
    *,
    frontend_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(
        title="MaaS Observatory API",
        version="6.0.0",
        docs_url="/docs",
        redoc_url=None,
    )
    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "HEAD"],
            allow_headers=["If-None-Match"],
        )

    reasoning_by_deployment = {
        deployment.deployment_id: _reasoning_enabled(
            deployment,
            settings.profiles.get(deployment.alias, "default-only"),
        )
        for deployment in catalog.deployments
    }

    # -------------------------------------------------- experience/overview ---

    @app.api_route("/api/v1/experience/overview", methods=["GET", "HEAD"])
    async def experience_overview(
        profile: Annotated[str, Query()] = (settings.experience.response_profile_id),
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        if profile != settings.experience.response_profile_id:
            raise HTTPException(status_code=400, detail="unknown profile")
        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )

        deployments = await database.query(
            """
            SELECT d.deployment_id, d.alias, d.display_name, d.precision,
                   s.response_state, s.reasons_json
            FROM deployments d
            LEFT JOIN current_states s USING(deployment_id)
            WHERE d.active=1 ORDER BY d.alias
            """
        )

        # Global uptime
        uptime_by_window: dict[str, dict[str, float | None]] = {}
        for wk, secs in (
            ("uptime_24h", 86400),
            ("uptime_7d", 604800),
            ("uptime_30d", 2592000),
        ):
            rows = await database.query(
                """
                SELECT r.deployment_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN r.outcome='success' THEN 1 ELSE 0 END)
                           AS success,
                       SUM(CASE WHEN (r.outcome='skipped'
                                      AND r.error_code='maintenance')
                                      OR EXISTS (
                           SELECT 1 FROM events e
                           WHERE e.deployment_id=r.deployment_id
                             AND e.kind='maintenance'
                             AND r.finished_at>=e.started_at
                             AND (e.ended_at IS NULL OR r.finished_at<=e.ended_at)
                       ) THEN 1 ELSE 0 END) AS maintenance_excluded
                FROM probe_runs r
                WHERE r.kind='route' AND r.finished_at>=?
                GROUP BY r.deployment_id
                """,
                (isoformat(datetime.now(UTC) - timedelta(seconds=secs)),),
            )
            uptime_by_window[wk] = {r["deployment_id"]: _uptime_pct(r) for r in rows}

        # All tiered runs for this window (all deployments at once)
        all_runs = await _query_tiered_runs(database, settings, cutoff)

        # Group by (deployment_id, context_tier)
        by_deployment: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in all_runs:
            dep_id = row["deployment_id"]
            tier = row["context_tier"]
            by_deployment.setdefault(dep_id, {})
            by_deployment[dep_id].setdefault(tier, []).append(row)

        summaries: list[dict[str, Any]] = []
        for dep_row in deployments:
            dep_id = dep_row["deployment_id"]
            tier_runs = by_deployment.get(dep_id, {})
            tiers: dict[str, Any] = {}
            total_attempts = 0
            total_success = 0
            tier_any_complete = False
            tier_sample_total = 0

            for tier in ALL_TIERS:
                tier_rows = tier_runs.get(tier, [])
                exp = _build_tier_experience(
                    tier_rows, reasoning_by_deployment.get(dep_id, False)
                )
                exp["response_state"] = dep_row["response_state"] or "collecting"
                exp["state_reasons"] = json.loads(dep_row["reasons_json"] or "[]")
                tiers[tier] = exp
                tier_attempts = [r for r in tier_rows if _counts_as_path_attempt(r)]
                total_attempts += len(tier_attempts)
                total_success += sum(
                    1
                    for r in tier_rows
                    if r["outcome"] == "success"
                    and json.loads(r["measurement_json"]).get("first_response_seconds")
                    is not None
                )
                if exp["complete_fixture_set"]:
                    tier_any_complete = True
                tier_sample_total += exp["sample_count"]

            path_success = total_success / total_attempts if total_attempts else None
            quality: Literal["exact", "incomplete", "unavailable"] = (
                "exact"
                if tier_any_complete
                else ("incomplete" if tier_sample_total else "unavailable")
            )

            summaries.append(
                {
                    "deployment_id": dep_id,
                    "alias": dep_row["alias"],
                    "name": dep_row["display_name"],
                    "precision": dep_row["precision"],
                    "reasoning_enabled": reasoning_by_deployment.get(dep_id, False),
                    "profile_id": settings.experience.response_profile_id,
                    "definition_version": settings.experience.definition_version,
                    "suite_version": settings.experience.suite_version,
                    "vantage_id": settings.experience.vantage_id,
                    "collection_mode": settings.collection_mode,
                    "path_success_rate": path_success,
                    "quality": quality,
                    "uptime_24h": uptime_by_window["uptime_24h"].get(dep_id),
                    "uptime_7d": uptime_by_window["uptime_7d"].get(dep_id),
                    "uptime_30d": uptime_by_window["uptime_30d"].get(dep_id),
                    "tiers": tiers,
                }
            )

        total_samples = sum(
            sum(t["sample_count"] for t in s["tiers"].values()) for s in summaries
        )
        newest = max(
            (
                tier["latest"]["measured_at"]
                for summary in summaries
                for tier in summary["tiers"].values()
                if tier["latest"] is not None
            ),
            default=None,
        )
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=total_samples,
            source_mix={"streaming_requests": len(all_runs)},
            data=summaries,
        )

    # --------------------------------------------------------------- compare ---

    @app.api_route("/api/v1/compare", methods=["GET", "HEAD"])
    async def compare(
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )
        deployments = await database.query(
            """
            SELECT d.deployment_id, d.alias, s.response_state
            FROM deployments d
            LEFT JOIN current_states s USING(deployment_id)
            WHERE d.active=1 ORDER BY d.alias
            """
        )
        all_runs = await _query_tiered_runs(database, settings, cutoff)

        by_dep: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in all_runs:
            by_dep.setdefault(row["deployment_id"], {})
            by_dep[row["deployment_id"]].setdefault(row["context_tier"], []).append(row)

        data: list[dict[str, Any]] = []
        for dep_row in deployments:
            dep_id = dep_row["deployment_id"]
            tier_runs = by_dep.get(dep_id, {})
            tiers: dict[str, Any] = {}
            for tier in ALL_TIERS:
                tier_rows = tier_runs.get(tier, [])
                exp = _build_tier_experience(
                    tier_rows, reasoning_by_deployment.get(dep_id, False)
                )
                tiers[tier] = {
                    "first_token_p50": (
                        exp["first_token_p50"] if exp["complete_fixture_set"] else None
                    ),
                    "output_speed_p50": (
                        exp["output_speed_p50"] if exp["complete_fixture_set"] else None
                    ),
                    "total_response_p50": (
                        exp["total_response_p50"]
                        if exp["complete_fixture_set"]
                        else None
                    ),
                    "quality": (
                        "exact"
                        if exp["complete_fixture_set"]
                        else ("incomplete" if exp["sample_count"] else "unavailable")
                    ),
                    "sample_count": exp["sample_count"],
                    "fixture_count": exp["fixture_count"],
                    "complete_fixture_set": exp["complete_fixture_set"],
                    "prompt_token_deviation_p50": exp["prompt_token_deviation_p50"],
                    "prompt_token_quality": exp["prompt_token_quality"],
                    "measured_at": (
                        exp["latest"]["measured_at"] if exp["latest"] else None
                    ),
                    "latest_attempt_outcome": exp["latest_attempt_outcome"],
                    "latest_attempt_reason": exp["latest_attempt_reason"],
                    "latest_attempt_at": exp["latest_attempt_at"],
                    "reason": (
                        None
                        if exp["complete_fixture_set"]
                        else "waiting_for_complete_fixture_set"
                    ),
                }
            data.append(
                {
                    "deployment_id": dep_id,
                    "alias": dep_row["alias"],
                    "response_state": dep_row["response_state"] or "collecting",
                    "tiers": tiers,
                    "source_kind": "streaming_request",
                    "observation_scope": "observatory_vantage",
                    "profile_id": settings.experience.response_profile_id,
                    "definition_version": settings.experience.definition_version,
                    "suite_version": settings.experience.suite_version,
                    "vantage_id": settings.experience.vantage_id,
                    "collection_mode": settings.collection_mode,
                }
            )

        newest = max(
            (
                t["measured_at"]
                for item in data
                for t in item["tiers"].values()
                if t["measured_at"] is not None
            ),
            default=None,
        )
        total = sum(t["sample_count"] for item in data for t in item["tiers"].values())
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=total,
            source_mix={"streaming_requests": total},
            data=data,
        )

    # -------------------------------------------------- experience/series ---

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/series",
        methods=["GET", "HEAD"],
    )
    async def experience_series(
        deployment_id: str,
        profile: Annotated[str, Query()] = (settings.experience.response_profile_id),
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        if profile != settings.experience.response_profile_id:
            raise HTTPException(status_code=400, detail="unknown profile")
        found = await database.scalar(
            "SELECT COUNT(*) FROM deployments WHERE deployment_id=?",
            (deployment_id,),
        )
        if not found:
            raise HTTPException(status_code=404, detail="unknown deployment")

        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )
        rows = await _query_tiered_runs(
            database,
            settings,
            cutoff,
            deployment_id=deployment_id,
        )

        tiers: dict[str, Any] = {}
        for tier in ALL_TIERS:
            tier_rows = [r for r in rows if r["context_tier"] == tier]
            points: list[dict[str, Any]] = []
            for row in tier_rows:
                measurements = json.loads(row["measurement_json"])
                points.append(
                    {
                        "timestamp": row["finished_at"],
                        "quality": (
                            "exact" if row["outcome"] == "success" else "unavailable"
                        ),
                        "prompt_token_quality": measurements.get(
                            "prompt_token_quality", "unavailable"
                        ),
                        "reason": _attempt_reason(
                            row["outcome"],
                            row["error_class"],
                            row["error_code"],
                        ),
                        "source_kind": "streaming_request",
                        "observation_scope": "observatory_vantage",
                        "profile_id": profile,
                        "definition_version": settings.experience.definition_version,
                        "suite_version": settings.experience.suite_version,
                        "vantage_id": settings.experience.vantage_id,
                        "collection_mode": row["collection_mode"],
                        "fixture_id": row["fixture_id"],
                        "block_id": row["block_id"],
                        "scheduler_lag_seconds": row["scheduler_lag_seconds"],
                        "sample_count": (
                            1
                            if row["outcome"] == "success"
                            and measurements.get("first_response_seconds") is not None
                            else 0
                        ),
                        "measurements": {
                            key: measurements.get(key)
                            for key in (
                                "time_to_headers_seconds",
                                "first_token_seconds",
                                "stream_start_seconds",
                                "first_response_seconds",
                                "total_response_seconds",
                                "output_speed_tps",
                                "stream_gap_p50_seconds",
                                "stream_gap_p95_seconds",
                                "stream_gap_max_seconds",
                                "reported_prompt_tokens",
                                "reported_completion_tokens",
                                "reasoning_chars",
                                "reported_reasoning_tokens",
                                "reasoning_tokens_estimated",
                                "ref_prompt_tokens",
                                "prompt_token_deviation_pct",
                                "scheduler_lag_seconds",
                            )
                        },
                    }
                )
            tiers[tier] = {"points": points}

        total_samples = sum(
            sum(p["sample_count"] for p in t["points"]) for t in tiers.values()
        )
        newest = max(
            (t["points"][-1]["timestamp"] for t in tiers.values() if t["points"]),
            default=None,
        )
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=total_samples,
            source_mix={"streaming_requests": len(rows)},
            data={
                "deployment_id": deployment_id,
                "profile_id": profile,
                "tiers": tiers,
            },
        )

    # --------------------------------------------------- experience/latest ---

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/latest",
        methods=["GET", "HEAD"],
    )
    async def experience_latest(
        deployment_id: str,
        profile: Annotated[str, Query()] = (settings.experience.response_profile_id),
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        if profile != settings.experience.response_profile_id:
            raise HTTPException(status_code=400, detail="unknown profile")
        found = await database.scalar(
            "SELECT COUNT(*) FROM deployments WHERE deployment_id=?",
            (deployment_id,),
        )
        if not found:
            raise HTTPException(status_code=404, detail="unknown deployment")

        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )
        rows = await _query_tiered_runs(
            database,
            settings,
            cutoff,
            deployment_id=deployment_id,
        )

        if not rows:
            return _envelope(
                window=window,
                freshness=None,
                sample_count=0,
                source_mix={},
                data={
                    "deployment_id": deployment_id,
                    "profile_id": profile,
                    "tiers": {
                        tier: {
                            "sample_count": 0,
                            "fixture_count": 0,
                            "complete_fixture_set": False,
                            "first_token_p50": None,
                            "first_token_p95": None,
                            "first_response_p50": None,
                            "first_response_p95": None,
                            "total_response_p50": None,
                            "total_response_p95": None,
                            "output_speed_p50": None,
                            "output_speed_p95": None,
                            "reasoning_tokens_p50": None,
                            "reasoning_tokens_quality": "unavailable",
                            "reported_prompt_tokens_p50": None,
                            "ref_prompt_tokens_p50": None,
                            "prompt_token_deviation_p50": None,
                            "prompt_token_quality": "unavailable",
                            "latest": None,
                            "latest_attempt_outcome": None,
                            "latest_attempt_error_class": None,
                            "latest_attempt_error_code": None,
                            "latest_attempt_reason": "first_check_scheduled",
                            "latest_attempt_at": None,
                            "measurement_age_seconds": None,
                        }
                        for tier in ALL_TIERS
                    },
                },
            )

        tiers: dict[str, Any] = {}
        total = 0
        for tier in ALL_TIERS:
            tier_rows = [r for r in rows if r["context_tier"] == tier]
            exp = _build_tier_experience(
                tier_rows, reasoning_by_deployment.get(deployment_id, False)
            )
            del exp["response_state"]
            del exp["state_reasons"]
            tiers[tier] = exp
            total += exp["sample_count"]

        return _envelope(
            window=window,
            freshness=None,
            sample_count=total,
            source_mix={"streaming_requests": len(rows)},
            data={
                "deployment_id": deployment_id,
                "profile_id": profile,
                "tiers": tiers,
            },
        )

    # ------------------------------------------------- experience/profiles ---

    @app.api_route("/api/v1/experience/profiles", methods=["GET", "HEAD"])
    async def experience_profiles() -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT profile_id, definition_version, fixture_sha256,
                   definition_json
            FROM experience_profiles
            WHERE definition_version=?
            ORDER BY profile_id
            """,
            (settings.experience.definition_version,),
        )
        data = []
        for row in rows:
            definition = json.loads(row.pop("definition_json"))
            data.append({**row, **definition})
        return _envelope(
            window="current",
            freshness=0,
            sample_count=len(data),
            source_mix={"configuration": len(data)},
            data=data,
        )

    # ----------------------------------------------- availability / events ---

    @app.api_route("/api/v1/availability", methods=["GET", "HEAD"])
    async def availability(
        days: Annotated[int, Query(ge=7, le=30)] = 30,
    ) -> dict[str, Any]:
        if days not in (7, 30):
            raise HTTPException(status_code=400, detail="days must be 7 or 30")
        cutoff = isoformat(datetime.now(UTC) - timedelta(days=days))
        deployments = await database.query(
            """
            SELECT deployment_id, alias FROM deployments
            WHERE active=1 ORDER BY alias
            """
        )
        rows = await database.query(
            """
            SELECT r.deployment_id, date(r.finished_at) AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN r.outcome='success' THEN 1 ELSE 0 END)
                       AS success,
                   SUM(CASE WHEN (r.outcome='skipped'
                                  AND r.error_code='maintenance')
                                  OR EXISTS (
                       SELECT 1 FROM events e
                       WHERE e.deployment_id=r.deployment_id
                         AND e.kind='maintenance'
                         AND r.finished_at>=e.started_at
                         AND (e.ended_at IS NULL OR r.finished_at<=e.ended_at)
                   ) THEN 1 ELSE 0 END) AS maintenance_excluded
            FROM probe_runs r
            WHERE r.kind='route' AND r.finished_at>=?
            GROUP BY r.deployment_id, date(r.finished_at)
            """,
            (cutoff,),
        )
        by_deployment: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            by_deployment.setdefault(row["deployment_id"], {})[row["day"]] = row
        today = datetime.now(UTC).date()
        data = []
        for deployment in deployments:
            day_rows = by_deployment.get(deployment["deployment_id"], {})
            daily: list[dict[str, str | float | int | None]] = []
            for offset in range(days - 1, -1, -1):
                day = (today - timedelta(days=offset)).isoformat()
                day_row = day_rows.get(day)
                if day_row is None:
                    daily.append(
                        {
                            "date": day,
                            "uptime_pct": None,
                            "samples": 0,
                            "maintenance_excluded": 0,
                        }
                    )
                    continue
                maintenance_excluded = int(day_row["maintenance_excluded"])
                denominator = int(day_row["total"]) - maintenance_excluded
                daily.append(
                    {
                        "date": day,
                        "uptime_pct": (
                            round(100 * int(day_row["success"]) / denominator, 2)
                            if denominator > 0
                            else None
                        ),
                        "samples": denominator,
                        "maintenance_excluded": maintenance_excluded,
                    }
                )
            data.append(
                {
                    "deployment_id": deployment["deployment_id"],
                    "alias": deployment["alias"],
                    "days": days,
                    "daily": daily,
                }
            )
        return _envelope(
            window=f"{days}d",
            freshness=0,
            sample_count=sum(len(entry["daily"]) for entry in data),
            source_mix={"route_checks": sum(int(row["total"]) for row in rows)},
            data=data,
        )

    @app.api_route("/api/v1/events", methods=["GET", "HEAD"])
    async def events(
        window: Annotated[Window, Query()] = "24h",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT e.id, e.deployment_id, d.alias, e.kind, e.severity,
                   e.state, e.title, e.detail_json, e.started_at, e.ended_at
            FROM events e JOIN deployments d USING(deployment_id)
            WHERE e.started_at>=?
              AND e.kind IN ('response_state', 'response_regression')
            ORDER BY e.started_at DESC LIMIT ?
            """,
            (
                isoformat(
                    datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
                ),
                limit,
            ),
        )
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json"))
        return _envelope(
            window=window,
            freshness=_freshness(rows[0]["started_at"]) if rows else None,
            sample_count=len(rows),
            source_mix={"response_state": len(rows)},
            data=rows,
        )

    # ----------------------------------------------------------------- meta ---

    @app.api_route("/api/v1/meta", methods=["GET", "HEAD"])
    async def meta() -> dict[str, Any]:
        return _envelope(
            window="current",
            freshness=0,
            sample_count=1,
            source_mix={"configuration": 1},
            data={
                "api_schema_version": SCHEMA_VERSION,
                "service": "MaaS Observatory",
                "config_schema_version": 4,
                "database_schema_version": 4,
                "collection_mode": settings.collection_mode,
                "response_profile_id": settings.experience.response_profile_id,
                "suite_version": settings.experience.suite_version,
                "definition_version": settings.experience.definition_version,
                "observer_vantage": settings.experience.vantage_id,
                "windows": list(WINDOW_SECONDS),
                "context_tiers": [
                    {"tier": "1k", "target_tokens": 1000, "fixture_count": 2},
                    {"tier": "16k", "target_tokens": 16000, "fixture_count": 2},
                    {"tier": "64k", "target_tokens": 64000, "fixture_count": 2},
                ],
                "schedule": {
                    "route_seconds": settings.probes.route_interval_seconds,
                    "response_start_timeout_seconds": (
                        settings.probes.response_start_timeout_seconds
                    ),
                    "stream_stall_seconds": settings.probes.stream_stall_seconds,
                    "rapid_block_seconds": (
                        settings.probes.rapid_block_interval_seconds
                    ),
                    "standard_block_seconds": (
                        settings.probes.standard_block_interval_seconds
                    ),
                    "global_inference_concurrency": len(catalog.deployments),
                    "rapid_context_tier": settings.probes.rapid_context_tier,
                    "standard_rotation": [
                        "1K-A",
                        "16K-A",
                        "64K-A",
                        "1K-B",
                        "16K-B",
                        "64K-B",
                    ],
                },
                "request_shape": {
                    "max_tokens": settings.probes.experience_max_output_tokens,
                    "proxy_environment": "ignored",
                },
                "budget": {
                    "scope": "per deployment per UTC day",
                    "applies_to": ["rapid", "standard"],
                    "requests": settings.probes.daily_budget.requests,
                    "input_tokens": settings.probes.daily_budget.input_tokens,
                    "output_tokens": settings.probes.daily_budget.output_tokens,
                },
                "metric_definitions": {
                    "first_token_seconds": (
                        "request start to first streamed token "
                        "(includes reasoning for thinking models)"
                    ),
                    "first_response_seconds": (
                        "request start to first non-empty visible content"
                    ),
                    "total_response_seconds": (
                        "request start to last token-bearing event"
                    ),
                    "output_speed_tps": (
                        "(reported completion tokens - 1) / "
                        "(last output event - first output event)"
                    ),
                    "first_token_p50": ("median of first_token_seconds in window"),
                    "first_token_p95": (
                        "p95 of first_token_seconds; null when sample_count < 10"
                    ),
                    "first_response_p50": (
                        "median of first_response_seconds in window"
                    ),
                    "first_response_p95": (
                        "p95 of first_response_seconds; null when sample_count < 10"
                    ),
                    "total_response_p50": (
                        "median of total_response_seconds in window"
                    ),
                    "total_response_p95": (
                        "p95 of total_response_seconds; null when sample_count < 10"
                    ),
                    "output_speed_p50": "median of output_speed_tps in window",
                    "output_speed_p95": (
                        "p95 of output_speed_tps; null when sample_count < 10"
                    ),
                    "reported_reasoning_tokens": (
                        "server-reported reasoning token usage when available"
                    ),
                    "reasoning_tokens_estimated": (
                        "fallback estimate = ceil(reasoning_chars / 4)"
                    ),
                    "reasoning_tokens_p50": (
                        "median reasoning usage for reasoning-enabled profiles; "
                        "reported usage takes precedence over estimates"
                    ),
                    "prompt_deviation_rule": (
                        "prompt token deviation >15% from reference is recorded "
                        "as a measurement quality signal; samples enter "
                        "p50/p95 normally"
                    ),
                    "uptime_24h": (
                        "route success % over 24h; maintenance samples excluded"
                    ),
                    "uptime_7d": (
                        "route success % over 7d; maintenance samples excluded"
                    ),
                    "uptime_30d": (
                        "route success % over 30d; maintenance samples excluded"
                    ),
                    "summary": (
                        "per-tier median (p50) across A/B fixture pair; "
                        "p95 only when sample_count >= 10; "
                        "tier completeness requires both A/B fixtures"
                    ),
                },
            },
        )

    # ------------------------------------------------------- catalog / misc ---

    @app.api_route("/api/v1/catalog", methods=["GET", "HEAD"])
    async def catalog_endpoint() -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT deployment_id, alias, display_name AS name, provider,
                   family, upstream_model, precision, model_id
            FROM deployments WHERE active=1 ORDER BY alias
            """
        )
        return _envelope(
            window="current",
            freshness=0,
            sample_count=len(rows),
            source_mix={"configuration": len(rows)},
            data=rows,
        )

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route("/readyz", methods=["GET", "HEAD"])
    async def readyz() -> Response:
        if not health.ready:
            return Response(status_code=503, content=health.detail)
        return Response(status_code=200)

    @app.middleware("http")
    async def cache_and_etag(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if (
            request.method != "GET"
            or not request.url.path.startswith("/api/")
            or response.status_code != 200
        ):
            return response

        body = getattr(response, "body", None)
        if isinstance(body, (bytes, bytearray, memoryview)):
            body_bytes = bytes(body)
        else:
            streaming_response = cast(StreamingResponse, response)
            chunks = [chunk async for chunk in streaming_response.body_iterator]
            body_bytes = b"".join(
                chunk.encode() if isinstance(chunk, str) else bytes(chunk)
                for chunk in chunks
            )

        stable = _etag_document(json.loads(body_bytes))
        digest = hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        etag = f'W/"{digest}"'
        headers = dict(response.headers)
        headers["etag"] = etag
        headers["cache-control"] = "no-store"
        if request.headers.get("If-None-Match") == etag:
            return Response(
                status_code=304,
                headers={
                    "etag": etag,
                    "cache-control": "no-store",
                },
            )
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        )

    # ---------------------------------------------------------------- static ---

    resolved_frontend = frontend_dir or Path(
        os.environ.get("MAAS_OBSERVATORY_FRONTEND_DIR", "frontend/dist")
    )
    if (resolved_frontend / "index.html").is_file():
        assets = resolved_frontend / "assets"
        if assets.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets, check_dir=True),
                name="frontend-assets",
            )

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def frontend_index(request: Request) -> Response:
            if request.method == "HEAD":
                return Response(
                    status_code=200,
                    headers={"Cache-Control": "no-cache"},
                    media_type="text/html",
                )
            return FileResponse(
                resolved_frontend / "index.html",
                headers={"Cache-Control": "no-cache"},
            )

    else:

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def api_index(request: Request) -> Response:
            if request.method == "HEAD":
                return Response(status_code=200)
            return JSONResponse(
                {
                    "service": "MaaS Observatory API",
                    "docs": "/docs",
                    "frontend": "not_built",
                }
            )

    return app
