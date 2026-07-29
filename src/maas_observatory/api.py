"""Public, read-only FastAPI contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from maas_common.catalog import ModelCatalog
from maas_observatory.database import Database, isoformat
from maas_observatory.models import ApiEnvelope
from maas_observatory.settings import ObservatorySettings

Window = Literal["1h", "6h", "24h", "7d", "30d"]
Resolution = Literal["15s", "1m", "5m", "1h"]
WINDOW_SECONDS = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}
MAX_WINDOW = {"15s": "24h", "1m": "30d", "5m": "365d", "1h": "365d"}
SCHEMA_VERSION = "2"


class RuntimeHealth:
    def __init__(self) -> None:
        self.ready = False
        self.detail = "starting"


PublicQuality = Literal["exact", "incomplete", "unavailable"]

PUBLIC_PROBE_REASONS = {
    "requests_running": "busy",
    "requests_waiting": "busy",
    "kv_cache": "busy",
    "telemetry_not_fresh": "telemetry_pending",
    "telemetry_unavailable": "telemetry_pending",
    "recent_production_requests": "recently_active",
    "recent_success": "recently_active",
    "insufficient_idle_history": "recently_active",
    "daily_inference_budget": "budget_deferred",
    "daily_output_token_budget": "budget_deferred",
    "daily_speed_budget": "budget_deferred",
    "daily_short_budget": "budget_deferred",
    "daily_context_budget": "budget_deferred",
    "daily_experience_budget": "budget_deferred",
    "daily_input_token_budget": "budget_deferred",
    "recent_preemption": "busy",
    "minimum_interval": "scheduled_interval",
    "maintenance": "maintenance",
}


def _public_probe_reason(outcome: str | None, error_code: str | None) -> str | None:
    if outcome is None:
        return "awaiting_turn"
    if outcome == "success":
        return None
    if error_code in PUBLIC_PROBE_REASONS:
        return PUBLIC_PROBE_REASONS[error_code]
    if outcome == "skipped":
        return "deferred"
    return "attempt_failed"


def _quality(sample_count: int, freshness: float | None) -> PublicQuality:
    if sample_count == 0:
        return "unavailable"
    if freshness is None or freshness > 60:
        return "incomplete"
    return "exact"


def _envelope(
    *,
    window: str,
    freshness: float | None,
    sample_count: int,
    source_mix: dict[str, int],
    data: Any,
) -> dict[str, Any]:
    return ApiEnvelope(
        data_window=window,
        freshness_seconds=freshness,
        sample_count=sample_count,
        source_mix=source_mix,
        quality=_quality(sample_count, freshness),
        data=data,
    ).model_dump(mode="json")


def _freshness(timestamp: str | None) -> float | None:
    if timestamp is None:
        return None
    return max(
        0,
        (datetime.now(UTC) - datetime.fromisoformat(timestamp)).total_seconds(),
    )


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


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
        version="1.0.0",
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

    async def experience_summary(*, profile: str, window: str) -> list[dict[str, Any]]:
        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )
        deployments = await database.query(
            """
            SELECT deployment_id, alias, display_name, precision
            FROM deployments WHERE active=1 ORDER BY alias
            """
        )
        summaries: list[dict[str, Any]] = []
        profile_kind = (
            "experience_context"
            if profile == settings.experience.context_profile_id
            else "experience_short"
        )
        for deployment in deployments:
            runs = await database.query(
                """
                SELECT finished_at, outcome, error_code, profile_id,
                       definition_version, vantage_id, measurement_json
                FROM probe_runs
                WHERE deployment_id=? AND kind=? AND profile_id=?
                  AND finished_at>=?
                ORDER BY finished_at
                """,
                (
                    deployment["deployment_id"],
                    profile_kind,
                    profile,
                    cutoff,
                ),
            )
            attempt_rows = await database.query(
                """
                SELECT finished_at, outcome, error_code
                FROM probe_runs
                WHERE deployment_id=? AND kind=?
                ORDER BY finished_at DESC LIMIT 1
                """,
                (deployment["deployment_id"], profile_kind),
            )
            successful: list[tuple[dict[str, Any], dict[str, Any]]] = []
            executed = [row for row in runs if row["outcome"] != "skipped"]
            for row in runs:
                measurements = json.loads(row["measurement_json"])
                if (
                    row["outcome"] == "success"
                    and measurements.get("client_ttft_seconds") is not None
                    and measurements.get("client_e2e_seconds") is not None
                ):
                    successful.append((row, measurements))
            latest_attempt = attempt_rows[0] if attempt_rows else None
            latest = successful[-1] if successful else None
            sample_count = len(successful)
            enough = sample_count >= 3
            ttft = [float(item[1]["client_ttft_seconds"]) for item in successful]
            tps = [
                float(item[1]["steady_state_output_tps"])
                for item in successful
                if item[1].get("steady_state_output_tps") is not None
            ]
            e2e = [float(item[1]["client_e2e_seconds"]) for item in successful]
            measured_at = latest[0]["finished_at"] if latest else None
            age = _freshness(measured_at)
            if measured_at is None:
                freshness_state = "experience_collecting"
            elif age is not None and age < settings.experience.short_fresh_seconds:
                freshness_state = "experience_fresh"
            elif (
                age is not None and age < settings.experience.short_unavailable_seconds
            ):
                freshness_state = "experience_stale"
            else:
                freshness_state = "experience_unavailable"
            latest_measurements = latest[1] if latest else {}
            success_rate = (
                sum(row["outcome"] == "success" for row in executed) / len(executed)
                if executed
                else None
            )
            summaries.append(
                {
                    **deployment,
                    "profile_id": profile,
                    "definition_version": (
                        latest[0]["definition_version"]
                        if latest
                        else settings.experience.definition_version
                    ),
                    "vantage_id": settings.experience.vantage_id,
                    "experience_state": freshness_state,
                    "sample_count": sample_count,
                    "executed_count": len(executed),
                    "path_success_rate": success_rate,
                    "quality": (
                        "exact" if enough else "incomplete" if latest else "unavailable"
                    ),
                    "reason": None if enough else "insufficient_samples",
                    "ttft_p50": _percentile(ttft, 0.5) if enough else None,
                    "ttft_p90": _percentile(ttft, 0.9) if enough else None,
                    "streaming_tps_p50": _percentile(tps, 0.5) if enough else None,
                    "streaming_tps_p10": _percentile(tps, 0.1) if enough else None,
                    "e2e_p50": _percentile(e2e, 0.5) if enough else None,
                    "e2e_p90": _percentile(e2e, 0.9) if enough else None,
                    "latest": (
                        {
                            "measured_at": measured_at,
                            "client_ttft_seconds": latest_measurements.get(
                                "client_ttft_seconds"
                            ),
                            "first_visible_content_seconds": latest_measurements.get(
                                "first_visible_content_seconds"
                            ),
                            "steady_state_output_tps": latest_measurements.get(
                                "steady_state_output_tps"
                            ),
                            "client_e2e_seconds": latest_measurements.get(
                                "client_e2e_seconds"
                            ),
                            "stream_event_gap_p95_seconds": latest_measurements.get(
                                "stream_event_gap_p95_seconds"
                            ),
                            "reported_prompt_tokens": latest_measurements.get(
                                "reported_prompt_tokens"
                            ),
                            "reported_completion_tokens": latest_measurements.get(
                                "reported_completion_tokens"
                            ),
                        }
                        if latest
                        else None
                    ),
                    "latest_attempt_outcome": (
                        latest_attempt["outcome"] if latest_attempt else None
                    ),
                    "latest_attempt_reason": _public_probe_reason(
                        latest_attempt["outcome"] if latest_attempt else None,
                        latest_attempt["error_code"] if latest_attempt else None,
                    ),
                    "latest_attempt_at": (
                        latest_attempt["finished_at"] if latest_attempt else None
                    ),
                }
            )
        return summaries

    @app.middleware("http")
    async def cache_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/api/v1/"):
            return response
        body = b""
        streaming_response: Any = response
        async for chunk in streaming_response.body_iterator:
            body += chunk
        etag_body = body
        try:
            etag_payload = json.loads(body)
            if isinstance(etag_payload, dict):
                etag_payload.pop("generated_at", None)
                etag_payload.pop("freshness_seconds", None)
                etag_body = json.dumps(
                    etag_payload, sort_keys=True, separators=(",", ":")
                ).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        etag = f'"{hashlib.sha256(etag_body).hexdigest()}"'
        headers = dict(response.headers)
        headers["ETag"] = etag
        headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=30"
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            body = b""
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz(request: Request) -> Response:
        if request.method == "HEAD":
            return Response(status_code=200)
        return JSONResponse({"status": "ok"})

    @app.api_route("/readyz", methods=["GET", "HEAD"])
    async def readyz(request: Request) -> Response:
        status = 200 if health.ready else 503
        if request.method == "HEAD":
            return Response(status_code=status)
        return JSONResponse(
            {
                "status": "ready" if health.ready else "not_ready",
                "detail": health.detail,
            },
            status_code=status,
        )

    @app.api_route("/api/v1/catalog", methods=["GET", "HEAD"])
    async def public_catalog() -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT deployment_id, alias, display_name, provider, family,
                   upstream_model, precision, model_id
            FROM deployments WHERE active=1 ORDER BY alias
            """
        )
        return _envelope(
            window="current",
            freshness=0,
            sample_count=len(rows),
            source_mix={"catalog": len(rows)},
            data=rows,
        )

    @app.api_route("/api/v1/overview", methods=["GET", "HEAD"])
    async def overview(
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        states = await database.query(
            """
            SELECT d.deployment_id, d.alias, d.display_name, d.family, d.precision,
                   s.service_state, s.telemetry_state, s.reasons_json,
                   s.telemetry_at, s.evaluated_at
            FROM deployments d LEFT JOIN current_states s USING(deployment_id)
            WHERE d.active=1 ORDER BY d.alias
            """
        )
        data: list[dict[str, Any]] = []
        freshness_values: list[float] = []
        for row in states:
            latest = await database.query(
                """
                SELECT payload_json, bucket_at, quality,
                       expected_source_count, observed_source_count,
                       source_seconds_coverage
                FROM rollups
                WHERE deployment_id=? AND resolution='1m'
                  AND quality IN ('exact', 'incomplete')
                ORDER BY bucket_at DESC LIMIT 1
                """,
                (row["deployment_id"],),
            )
            metrics = json.loads(latest[0]["payload_json"])["values"] if latest else {}
            measured_at = latest[0]["bucket_at"] if latest else None
            expected_sources = int(
                await database.scalar(
                    """
                    SELECT COUNT(*) FROM metrics_sources
                    WHERE deployment_id=? AND active=1
                    """,
                    (row["deployment_id"],),
                )
                or 0
            )
            freshness = _freshness(row["telemetry_at"])
            if freshness is not None:
                freshness_values.append(freshness)
            error_rows = await database.query(
                """
                SELECT error_class, COUNT(*) AS count FROM (
                    SELECT error_class FROM scrape_snapshots
                    WHERE deployment_id=? AND observed_at>=?
                      AND error_class!='none'
                    UNION ALL
                    SELECT error_class FROM probe_runs
                    WHERE deployment_id=? AND finished_at>=?
                      AND outcome='failed' AND error_class!='none'
                ) GROUP BY error_class
                """,
                (
                    row["deployment_id"],
                    isoformat(datetime.now(UTC) - timedelta(hours=24)),
                    row["deployment_id"],
                    isoformat(datetime.now(UTC) - timedelta(hours=24)),
                ),
            )
            error_counts = {
                item["error_class"]: int(item["count"]) for item in error_rows
            }
            data.append(
                {
                    "deployment_id": row["deployment_id"],
                    "alias": row["alias"],
                    "name": row["display_name"],
                    "family": row["family"],
                    "precision": row["precision"],
                    "service_state": row["service_state"] or "unknown",
                    "telemetry_state": row["telemetry_state"] or "unavailable",
                    "reasons": json.loads(row["reasons_json"] or "[]"),
                    "telemetry_at": row["telemetry_at"],
                    "measured_at": measured_at,
                    "measurement_age_seconds": _freshness(measured_at),
                    "source_coverage": (
                        latest[0]["source_seconds_coverage"] if latest else None
                    ),
                    "expected_source_count": (
                        latest[0]["expected_source_count"]
                        if latest
                        else expected_sources
                    ),
                    "observed_source_count": (
                        latest[0]["observed_source_count"] if latest else 0
                    ),
                    "quality": latest[0]["quality"] if latest else "unavailable",
                    "error_statistics_24h": {
                        "service_failures": error_counts.get("service_error", 0),
                        "transport_unconfirmed": error_counts.get("transport_error", 0),
                        "measurement_errors": error_counts.get("measurement_error", 0),
                    },
                    "metrics": {
                        key: metrics.get(key) for key in settings.public.metric_fields
                    },
                }
            )
        valid_telemetry = sum(item["quality"] != "unavailable" for item in data)
        return _envelope(
            window=window,
            freshness=max(freshness_values) if freshness_values else None,
            sample_count=valid_telemetry,
            source_mix={"passive_metrics": valid_telemetry},
            data=data,
        )

    @app.api_route("/api/v1/experience/overview", methods=["GET", "HEAD"])
    async def public_experience_overview(
        profile: Annotated[str, Query()] = "interactive-short-v1",
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        allowed = {
            settings.experience.short_profile_id,
            settings.experience.context_profile_id,
        }
        if profile not in allowed:
            raise HTTPException(status_code=400, detail="unknown experience profile")
        data = await experience_summary(profile=profile, window=window)
        newest = max(
            (
                item["latest_attempt_at"]
                for item in data
                if item["latest_attempt_at"] is not None
            ),
            default=None,
        )
        samples = sum(int(item["sample_count"]) for item in data)
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=samples,
            source_mix={"observer_path": samples},
            data=data,
        )

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/latest",
        methods=["GET", "HEAD"],
    )
    async def experience_latest(deployment_id: str) -> dict[str, Any]:
        rows = await experience_summary(
            profile=settings.experience.short_profile_id, window="24h"
        )
        item = next(
            (row for row in rows if row["deployment_id"] == deployment_id), None
        )
        if item is None:
            raise HTTPException(status_code=404, detail="unknown deployment")
        return _envelope(
            window="latest",
            freshness=_freshness(
                item["latest"]["measured_at"] if item["latest"] else None
            ),
            sample_count=int(item["sample_count"]),
            source_mix={"observer_path": int(item["sample_count"])},
            data=item,
        )

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/series",
        methods=["GET", "HEAD"],
    )
    async def experience_series(
        deployment_id: str,
        profile: Annotated[str, Query()] = "interactive-short-v1",
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        if profile not in {
            settings.experience.short_profile_id,
            settings.experience.context_profile_id,
        }:
            raise HTTPException(status_code=400, detail="unknown experience profile")
        exists = await database.scalar(
            "SELECT COUNT(*) FROM deployments WHERE deployment_id=? AND active=1",
            (deployment_id,),
        )
        if not exists:
            raise HTTPException(status_code=404, detail="unknown deployment")
        kind = (
            "experience_context"
            if profile == settings.experience.context_profile_id
            else "experience_short"
        )
        rows = await database.query(
            """
            SELECT finished_at, outcome, error_code, definition_version,
                   vantage_id, measurement_json
            FROM probe_runs WHERE deployment_id=? AND kind=? AND profile_id=?
              AND finished_at>=? ORDER BY finished_at
            """,
            (
                deployment_id,
                kind,
                profile,
                isoformat(
                    datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
                ),
            ),
        )
        points = []
        for row in rows:
            measurements = json.loads(row["measurement_json"])
            points.append(
                {
                    "timestamp": row["finished_at"],
                    "quality": (
                        "exact" if row["outcome"] == "success" else "unavailable"
                    ),
                    "reason": _public_probe_reason(row["outcome"], row["error_code"]),
                    "profile_id": profile,
                    "definition_version": row["definition_version"],
                    "vantage_id": row["vantage_id"],
                    "source_kind": "experience_probe",
                    "observation_scope": "observer_path",
                    "sample_count": 1 if row["outcome"] == "success" else 0,
                    "measurements": {
                        key: measurements.get(key)
                        for key in (
                            "time_to_headers_seconds",
                            "client_ttft_seconds",
                            "first_visible_content_seconds",
                            "steady_state_output_tps",
                            "client_e2e_seconds",
                            "stream_event_gap_p50_seconds",
                            "stream_event_gap_p95_seconds",
                            "stream_event_gap_max_seconds",
                            "reported_prompt_tokens",
                            "reported_completion_tokens",
                        )
                    },
                }
            )
        return _envelope(
            window=window,
            freshness=_freshness(points[-1]["timestamp"] if points else None),
            sample_count=sum(point["sample_count"] for point in points),
            source_mix={"observer_path": len(points)},
            data={
                "deployment_id": deployment_id,
                "profile_id": profile,
                "points": points,
            },
        )

    @app.api_route("/api/v1/experience/profiles", methods=["GET", "HEAD"])
    async def experience_profiles() -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT profile_id, definition_version, fixture_sha256,
                   definition_json
            FROM experience_profiles ORDER BY profile_id
            """
        )
        data = []
        for row in rows:
            definition = json.loads(row.pop("definition_json"))
            definition.pop("fixture_sha256", None)
            data.append({**row, **definition})
        return _envelope(
            window="current",
            freshness=0,
            sample_count=len(data),
            source_mix={"configuration": len(data)},
            data=data,
        )

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/series", methods=["GET", "HEAD"]
    )
    async def series(
        deployment_id: str,
        metric: Annotated[str, Query()] = "aggregate_output_tps",
        window: Annotated[Window, Query()] = "24h",
        resolution: Annotated[Resolution, Query()] = "1m",
    ) -> dict[str, Any]:
        exists = await database.scalar(
            "SELECT COUNT(*) FROM deployments WHERE deployment_id=? AND active=1",
            (deployment_id,),
        )
        if not exists:
            raise HTTPException(status_code=404, detail="unknown deployment")
        if metric not in settings.public.metric_fields:
            raise HTTPException(status_code=400, detail="metric is not public")
        window_seconds = WINDOW_SECONDS[window]
        if resolution == "15s" and window_seconds > WINDOW_SECONDS["24h"]:
            raise HTTPException(
                status_code=400, detail="15s resolution supports at most 24h"
            )
        cutoff = isoformat(datetime.now(UTC) - timedelta(seconds=window_seconds))
        points: list[dict[str, Any]] = []
        if resolution == "15s":
            rows = await database.query(
                """
                SELECT observed_at, interval_json FROM scrape_snapshots
                WHERE deployment_id=? AND observed_at>=?
                  AND interval_json IS NOT NULL ORDER BY observed_at
                """,
                (deployment_id, cutoff),
            )
            for row in rows:
                interval = json.loads(row["interval_json"])
                value = interval["values"].get(metric)
                points.append(
                    {
                        "timestamp": row["observed_at"],
                        "value": value,
                        "unit": _metric_unit(metric),
                        "source_kind": "passive_metrics",
                        "observation_scope": "deployment",
                        "quality": ("exact" if value is not None else "unavailable"),
                        "sample_count": interval.get("sample_count", 0),
                        "profile_id": None,
                        "definition_version": "1",
                        "reason": None if value is not None else "no_samples",
                    }
                )
        else:
            rows = await database.query(
                """
                SELECT bucket_at, payload_json, sample_count, quality
                FROM rollups WHERE deployment_id=? AND resolution=?
                  AND bucket_at>=? ORDER BY bucket_at
                """,
                (deployment_id, resolution, cutoff),
            )
            for row in rows:
                payload = json.loads(row["payload_json"])
                value = payload["values"].get(metric)
                points.append(
                    {
                        "timestamp": row["bucket_at"],
                        "value": value,
                        "unit": _metric_unit(metric),
                        "source_kind": "passive_metrics",
                        "observation_scope": "deployment",
                        "quality": ("exact" if value is not None else "unavailable"),
                        "sample_count": row["sample_count"],
                        "profile_id": None,
                        "definition_version": "1",
                        "reason": None if value is not None else "no_samples",
                    }
                )
        newest = points[-1]["timestamp"] if points else None
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=sum(point["sample_count"] for point in points),
            source_mix={"passive_metrics": len(points)},
            data={
                "deployment_id": deployment_id,
                "metric": metric,
                "resolution": resolution,
                "points": points,
                "reason": None if points else "no_data",
            },
        )

    @app.api_route("/api/v1/compare", methods=["GET", "HEAD"])
    async def compare(
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        summaries = await experience_summary(
            profile=settings.experience.short_profile_id, window=window
        )
        data = [
            {
                "deployment_id": item["deployment_id"],
                "alias": item["alias"],
                "value": (
                    item["streaming_tps_p50"]
                    if item["streaming_tps_p50"] is not None
                    else (
                        item["latest"]["steady_state_output_tps"]
                        if item["latest"]
                        else None
                    )
                ),
                "unit": "tokens/s",
                "source_kind": "experience_probe",
                "observation_scope": "observer_path",
                "quality": item["quality"],
                "sample_count": item["sample_count"],
                "profile_id": item["profile_id"],
                "definition_version": item["definition_version"],
                "vantage_id": item["vantage_id"],
                "measured_at": (
                    item["latest"]["measured_at"] if item["latest"] else None
                ),
                "latest_attempt_outcome": item["latest_attempt_outcome"],
                "latest_attempt_reason": item["latest_attempt_reason"],
                "latest_attempt_at": item["latest_attempt_at"],
                "reason": (
                    None
                    if item["latest"]
                    and item["latest"]["steady_state_output_tps"] is not None
                    else "no_valid_experience_sample"
                ),
            }
            for item in summaries
        ]
        newest = max(
            (
                item["latest_attempt_at"]
                for item in data
                if item["latest_attempt_at"] is not None
            ),
            default=None,
        )
        total = sum(int(item["sample_count"]) for item in data)
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=total,
            source_mix={"experience_probe": total},
            data=data,
        )

    @app.api_route("/api/v1/events", methods=["GET", "HEAD"])
    async def events(
        window: Annotated[Window, Query()] = "24h",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        cutoff = isoformat(
            datetime.now(UTC) - timedelta(seconds=WINDOW_SECONDS[window])
        )
        rows = await database.query(
            """
            SELECT id, deployment_id, kind, severity, state, title,
                   detail_json, started_at, ended_at
            FROM events WHERE started_at>=?
            ORDER BY started_at DESC LIMIT ?
            """,
            (cutoff, limit),
        )
        for row in rows:
            row["detail"] = json.loads(row.pop("detail_json"))
        return _envelope(
            window=window,
            freshness=_freshness(rows[0]["started_at"]) if rows else None,
            sample_count=len(rows),
            source_mix={"state_engine": len(rows)},
            data=rows,
        )

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
                "comparison_scope": "observer_path",
                "observer_vantage": settings.experience.vantage_id,
                "windows": list(WINDOW_SECONDS),
                "resolutions": ["15s", "1m", "5m", "1h"],
                "resolution_limits": MAX_WINDOW,
                "public_metrics": settings.public.metric_fields,
                "metric_definitions": {
                    "steady_state_output_tps": (
                        "(reported_completion_tokens - 1) / "
                        "(last_output_event - first_output_event)"
                    ),
                    "aggregate_output_tps": (
                        "sum of per-instance generation token counter rates"
                    ),
                    "client_ttft_seconds": (
                        "observer request start to first non-empty content "
                        "or reasoning event"
                    ),
                },
                "deprecated_fields": {
                    "system_output_tps": "aggregate_output_tps",
                    "observed_decode_tps": None,
                },
            },
        )

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


def _metric_unit(metric: str) -> str:
    if metric.endswith("_tps"):
        return "tokens/s"
    if metric.endswith("_p50") or metric.endswith("_p95"):
        return "s"
    if metric == "kv_cache_usage":
        return "ratio"
    if metric.endswith("_rate"):
        return "ratio"
    return "requests"
