"""Public, read-only FastAPI contract for real-request measurements."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from maas_common.catalog import ModelCatalog
from maas_observatory.database import Database, isoformat
from maas_observatory.models import ApiEnvelope
from maas_observatory.probes import LONG_SEEDS, SHORT_TEMPLATES
from maas_observatory.settings import ObservatorySettings

Window = Literal["1h", "6h", "24h", "7d", "30d"]
WINDOW_SECONDS = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}
SCHEMA_VERSION = "4"


class RuntimeHealth:
    def __init__(self) -> None:
        self.ready = False
        self.detail = "starting"


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


def _attempt_reason(outcome: str | None, error_code: str | None) -> str | None:
    if outcome is None:
        return "first_check_scheduled"
    if outcome == "success":
        return None
    if error_code == "maintenance":
        return "maintenance"
    if error_code and error_code.startswith("daily_"):
        return "scheduled_later"
    if outcome == "skipped":
        return "scheduled_later"
    return "request_failed"


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
        version="4.0.0",
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
            SELECT d.deployment_id, d.alias, d.display_name, d.precision,
                   s.response_state, s.reasons_json
            FROM deployments d
            LEFT JOIN current_states s USING(deployment_id)
            WHERE d.active=1 ORDER BY d.alias
            """
        )
        expected_fixtures = {item[0] for item in (*SHORT_TEMPLATES, *LONG_SEEDS)}
        summaries: list[dict[str, Any]] = []
        for deployment in deployments:
            runs = await database.query(
                """
                SELECT finished_at, outcome, error_class, error_code,
                       profile_id, definition_version, suite_version,
                       vantage_id, collection_mode, fixture_id, block_id,
                       scheduler_lag_seconds, measurement_json
                FROM probe_runs
                WHERE deployment_id=?
                  AND kind IN ('experience_short', 'experience_context')
                  AND profile_id=?
                  AND definition_version=? AND suite_version=?
                  AND collection_mode=? AND finished_at>=?
                ORDER BY finished_at
                """,
                (
                    deployment["deployment_id"],
                    profile,
                    settings.experience.definition_version,
                    settings.experience.suite_version,
                    settings.collection_mode,
                    cutoff,
                ),
            )
            attempts = [row for row in runs if row["outcome"] != "skipped"]
            successful: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for row in runs:
                measurement = json.loads(row["measurement_json"])
                if (
                    row["outcome"] == "success"
                    and measurement.get("first_response_seconds") is not None
                ):
                    successful.append((row, measurement))
            latest_attempt = attempts[-1] if attempts else None
            latest = successful[-1] if successful else None
            fixtures = {row["fixture_id"] for row, _ in successful if row["fixture_id"]}
            sample_count = len(successful)
            complete_suite = (
                sample_count >= settings.experience.summary_min_samples
                and expected_fixtures.issubset(fixtures)
            )
            first_response = [
                float(measurement["first_response_seconds"])
                for _, measurement in successful
            ]
            output_speed = [
                float(measurement["output_speed_tps"])
                for _, measurement in successful
                if measurement.get("output_speed_tps") is not None
            ]
            latest_payload = None
            measured_at = None
            if latest:
                row, measurement = latest
                measured_at = row["finished_at"]
                latest_payload = {
                    "measured_at": measured_at,
                    "first_response_seconds": measurement["first_response_seconds"],
                    "output_speed_tps": measurement.get("output_speed_tps"),
                    "reported_prompt_tokens": measurement.get("reported_prompt_tokens"),
                    "reported_completion_tokens": measurement.get(
                        "reported_completion_tokens"
                    ),
                    "fixture_id": row["fixture_id"],
                    "block_id": row["block_id"],
                    "scheduler_lag_seconds": row["scheduler_lag_seconds"],
                }
            executed_count = len(attempts)
            path_success = (
                sum(row["outcome"] == "success" for row in attempts) / executed_count
                if executed_count
                else None
            )
            latest_attempt_at = (
                latest_attempt["finished_at"] if latest_attempt else None
            )
            summaries.append(
                {
                    "deployment_id": deployment["deployment_id"],
                    "alias": deployment["alias"],
                    "name": deployment["display_name"],
                    "precision": deployment["precision"],
                    "response_state": deployment["response_state"] or "collecting",
                    "state_reasons": json.loads(deployment["reasons_json"] or "[]"),
                    "profile_id": profile,
                    "definition_version": settings.experience.definition_version,
                    "suite_version": settings.experience.suite_version,
                    "vantage_id": settings.experience.vantage_id,
                    "collection_mode": settings.collection_mode,
                    "sample_count": sample_count,
                    "fixture_count": len(fixtures),
                    "complete_fixture_set": complete_suite,
                    "path_success_rate": path_success,
                    "first_response_mean": (
                        statistics.fmean(first_response) if first_response else None
                    ),
                    "output_speed_mean": (
                        statistics.fmean(output_speed) if output_speed else None
                    ),
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
                        latest_attempt["error_code"] if latest_attempt else None,
                    ),
                    "latest_attempt_at": latest_attempt_at,
                    "measurement_age_seconds": _freshness(measured_at),
                    "quality": (
                        "exact"
                        if complete_suite
                        else ("incomplete" if sample_count else "unavailable")
                    ),
                }
            )
        return summaries

    @app.middleware("http")
    async def cache_and_etag(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/") and response.status_code == 200:
            body = b""
            body_iterator = cast(Any, response).body_iterator
            async for chunk in body_iterator:
                body += chunk
            etag_body = body
            try:
                etag_document = json.loads(body)
                if isinstance(etag_document, dict):
                    etag_body = json.dumps(
                        _etag_document(etag_document),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            etag = f'"{hashlib.sha256(etag_body).hexdigest()}"'
            if request.headers.get("if-none-match") == etag:
                return Response(
                    status_code=304,
                    headers={
                        "ETag": etag,
                        "Cache-Control": (
                            "public, max-age=10, stale-while-revalidate=30"
                        ),
                    },
                )
            headers = dict(response.headers)
            headers["ETag"] = etag
            headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=30"
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
        return response

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
            {"ready": health.ready, "detail": health.detail}, status_code=status
        )

    @app.api_route("/api/v1/catalog", methods=["GET", "HEAD"])
    async def public_catalog() -> dict[str, Any]:
        rows = await database.query(
            """
            SELECT deployment_id, alias, display_name AS name,
                   provider, family, upstream_model, precision, model_id
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

    @app.api_route("/api/v1/experience/overview", methods=["GET", "HEAD"])
    async def public_experience_overview(
        profile: Annotated[str | None, Query()] = None,
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        profile = profile or settings.experience.response_profile_id
        if profile != settings.experience.response_profile_id:
            raise HTTPException(status_code=400, detail="unknown response profile")
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
        complete = sum(bool(item["complete_fixture_set"]) for item in data)
        return _envelope(
            window=window,
            freshness=_freshness(newest),
            sample_count=samples,
            source_mix={"streaming_requests": samples},
            quality=(
                "exact"
                if complete == len(data)
                else ("incomplete" if samples else "unavailable")
            ),
            data=data,
        )

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/latest",
        methods=["GET", "HEAD"],
    )
    async def experience_latest(deployment_id: str) -> dict[str, Any]:
        rows = await experience_summary(
            profile=settings.experience.response_profile_id, window="24h"
        )
        item = next(
            (row for row in rows if row["deployment_id"] == deployment_id), None
        )
        if item is None:
            raise HTTPException(status_code=404, detail="unknown deployment")
        return _envelope(
            window="latest",
            freshness=item["measurement_age_seconds"],
            sample_count=int(item["sample_count"]),
            source_mix={"streaming_requests": int(item["sample_count"])},
            quality=item["quality"],
            data=item,
        )

    @app.api_route(
        "/api/v1/deployments/{deployment_id}/experience/series",
        methods=["GET", "HEAD"],
    )
    async def experience_series(
        deployment_id: str,
        profile: Annotated[str | None, Query()] = None,
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        profile = profile or settings.experience.response_profile_id
        if profile != settings.experience.response_profile_id:
            raise HTTPException(status_code=400, detail="unknown response profile")
        exists = await database.scalar(
            "SELECT COUNT(*) FROM deployments WHERE deployment_id=? AND active=1",
            (deployment_id,),
        )
        if not exists:
            raise HTTPException(status_code=404, detail="unknown deployment")
        rows = await database.query(
            """
            SELECT finished_at, outcome, error_class, error_code,
                   fixture_id, block_id, scheduler_lag_seconds,
                   collection_mode, measurement_json
            FROM probe_runs
            WHERE deployment_id=?
              AND kind IN ('experience_short', 'experience_context')
              AND profile_id=?
              AND definition_version=? AND suite_version=?
              AND collection_mode=? AND finished_at>=?
            ORDER BY finished_at
            """,
            (
                deployment_id,
                profile,
                settings.experience.definition_version,
                settings.experience.suite_version,
                settings.collection_mode,
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
                    "reason": _attempt_reason(row["outcome"], row["error_code"]),
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
                    "sample_count": 1 if row["outcome"] == "success" else 0,
                    "measurements": {
                        key: measurements.get(key)
                        for key in (
                            "time_to_headers_seconds",
                            "stream_start_seconds",
                            "first_response_seconds",
                            "output_speed_tps",
                            "stream_gap_p50_seconds",
                            "stream_gap_p95_seconds",
                            "stream_gap_max_seconds",
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
            source_mix={"streaming_requests": len(points)},
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

    @app.api_route("/api/v1/compare", methods=["GET", "HEAD"])
    async def compare(
        window: Annotated[Window, Query()] = "24h",
    ) -> dict[str, Any]:
        summaries = await experience_summary(
            profile=settings.experience.response_profile_id, window=window
        )
        data = [
            {
                "deployment_id": item["deployment_id"],
                "alias": item["alias"],
                "value": (
                    item["output_speed_mean"] if item["complete_fixture_set"] else None
                ),
                "unit": "tokens/s",
                "source_kind": "streaming_request",
                "observation_scope": "observatory_vantage",
                "quality": item["quality"],
                "sample_count": item["sample_count"],
                "fixture_count": item["fixture_count"],
                "complete_fixture_set": item["complete_fixture_set"],
                "profile_id": item["profile_id"],
                "definition_version": item["definition_version"],
                "suite_version": item["suite_version"],
                "vantage_id": item["vantage_id"],
                "collection_mode": item["collection_mode"],
                "measured_at": (
                    item["latest"]["measured_at"] if item["latest"] else None
                ),
                "latest_attempt_outcome": item["latest_attempt_outcome"],
                "latest_attempt_reason": item["latest_attempt_reason"],
                "latest_attempt_at": item["latest_attempt_at"],
                "reason": (
                    None
                    if item["complete_fixture_set"]
                    and item["output_speed_mean"] is not None
                    else "waiting_for_complete_fixture_set"
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
            source_mix={"streaming_requests": total},
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
                "collection_mode": settings.collection_mode,
                "response_profile_id": settings.experience.response_profile_id,
                "suite_version": settings.experience.suite_version,
                "definition_version": settings.experience.definition_version,
                "observer_vantage": settings.experience.vantage_id,
                "windows": list(WINDOW_SECONDS),
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
                    "global_inference_concurrency": 1,
                    "rapid_automatic_limit": None,
                },
                "request_shape": {
                    "compact_max_completion_tokens": (
                        settings.probes.short_max_output_tokens
                    ),
                    "extended_max_completion_tokens": (
                        settings.probes.context_max_output_tokens
                    ),
                    "proxy_environment": "ignored",
                },
                "metric_definitions": {
                    "first_response_seconds": (
                        "request start to first non-empty visible content"
                    ),
                    "output_speed_tps": (
                        "(reported completion tokens - 1) / "
                        "(last output event - first output event)"
                    ),
                    "summary": (
                        "arithmetic mean across the balanced six-fixture suite"
                    ),
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
