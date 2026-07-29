"""Evidence-based service and telemetry state calculation."""

from __future__ import annotations

import asyncio
import json
import statistics
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from maas_common.catalog import ModelCatalog
from maas_observatory.database import Database, isoformat
from maas_observatory.models import ServiceState, TelemetryState
from maas_observatory.settings import StateSettings


class StateEngine:
    def __init__(
        self,
        catalog: ModelCatalog,
        settings: StateSettings,
        database: Database,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.database = database

    def telemetry_state(self, observed_at: datetime | None) -> TelemetryState:
        if observed_at is None:
            return TelemetryState.UNAVAILABLE
        age = (datetime.now(UTC) - observed_at).total_seconds()
        if age > self.settings.telemetry_unavailable_seconds:
            return TelemetryState.UNAVAILABLE
        if age > self.settings.telemetry_stale_seconds:
            return TelemetryState.STALE
        if age > self.settings.telemetry_partial_seconds:
            return TelemetryState.PARTIAL
        return TelemetryState.FRESH

    async def evaluate(
        self, deployment_id: str
    ) -> tuple[ServiceState, TelemetryState, list[str]]:
        latest_rows = await self.database.query(
            """
            SELECT observed_at, quality, gauges_json, interval_json
            FROM scrape_snapshots WHERE deployment_id=?
            ORDER BY observed_at DESC LIMIT 3
            """,
            (deployment_id,),
        )
        latest_at = (
            datetime.fromisoformat(latest_rows[0]["observed_at"])
            if latest_rows
            else None
        )
        telemetry = self.telemetry_state(latest_at)
        if (
            telemetry == TelemetryState.FRESH
            and latest_rows
            and latest_rows[0]["quality"] != "exact"
        ):
            telemetry = TelemetryState.PARTIAL
        current = await self.database.query(
            "SELECT service_state FROM current_states WHERE deployment_id=?",
            (deployment_id,),
        )
        if current and current[0]["service_state"] == ServiceState.MAINTENANCE:
            return ServiceState.MAINTENANCE, telemetry, ["maintenance"]

        unavailable = await self._unavailable(deployment_id)
        if unavailable:
            return ServiceState.UNAVAILABLE, telemetry, [unavailable]
        degraded = await self._degraded(deployment_id)
        if degraded:
            return ServiceState.DEGRADED, telemetry, [degraded]
        slow = await self._slow(deployment_id, latest_rows)
        if slow:
            return ServiceState.SLOW, telemetry, [slow]
        if (
            telemetry == TelemetryState.FRESH
            and latest_rows
            and latest_rows[0]["quality"] == "exact"
        ):
            return ServiceState.OPERATIONAL, telemetry, ["fresh_telemetry"]
        return ServiceState.UNKNOWN, telemetry, ["insufficient_fresh_evidence"]

    async def _unavailable(self, deployment_id: str) -> str | None:
        canaries = await self.database.query(
            """
            SELECT outcome, error_class FROM probe_runs
            WHERE deployment_id=? AND kind IN ('canary', 'confirmation')
              AND outcome!='skipped'
            ORDER BY finished_at DESC LIMIT 2
            """,
            (deployment_id,),
        )
        if len(canaries) == 2 and all(
            row["outcome"] == "failed" and row["error_class"] == "service_error"
            for row in canaries
        ):
            return "consecutive_generation_service_failures"
        routes = await self.database.query(
            """
            SELECT id, outcome FROM probe_runs
            WHERE deployment_id=? AND kind='route'
            ORDER BY finished_at DESC LIMIT 3
            """,
            (deployment_id,),
        )
        if len(routes) == 3 and all(row["outcome"] == "failed" for row in routes):
            confirmation = await self.database.query(
                """
                SELECT outcome FROM probe_runs
                WHERE confirmation_of=? ORDER BY finished_at DESC LIMIT 1
                """,
                (routes[0]["id"],),
            )
            if confirmation and confirmation[0]["outcome"] == "failed":
                return "route_and_generation_confirmation_failed"
        return None

    async def _degraded(self, deployment_id: str) -> str | None:
        cutoff = isoformat(datetime.now(UTC) - timedelta(minutes=5))
        recent = await self.database.query(
            """
            SELECT outcome, error_class, error_code FROM probe_runs
            WHERE deployment_id=? AND finished_at>=? AND outcome!='skipped'
            """,
            (deployment_id, cutoff),
        )
        service_errors = sum(
            row["outcome"] == "failed" and row["error_class"] == "service_error"
            for row in recent
        )
        if (
            len(recent) >= self.settings.service_error_min_samples
            and service_errors / len(recent) > self.settings.service_error_rate
        ):
            return "service_error_rate"
        generations = await self.database.query(
            """
            SELECT error_code FROM probe_runs
            WHERE deployment_id=? AND kind IN ('canary', 'speed', 'confirmation')
              AND outcome!='skipped'
            ORDER BY finished_at DESC LIMIT 2
            """,
            (deployment_id,),
        )
        degraded_codes = {"empty_output", "protocol_invalid", "stream_stall"}
        if len(generations) == 2 and all(
            row["error_code"] in degraded_codes for row in generations
        ):
            return "consecutive_invalid_generation"
        return None

    async def _slow(
        self, deployment_id: str, latest_rows: list[dict[str, Any]]
    ) -> str | None:
        if len(latest_rows) == 3:
            waiting = []
            for row in latest_rows:
                gauges = json.loads(row["gauges_json"])
                waiting.append(float(gauges.get("requests_waiting") or 0))
            if all(value > 0 for value in waiting):
                return "persistent_waiting_queue"

        passive = await self.database.query(
            """
            SELECT bucket_at, payload_json FROM rollups
            WHERE deployment_id=? AND resolution='5m'
              AND bucket_at>=?
            ORDER BY bucket_at DESC
            """,
            (deployment_id, isoformat(datetime.now(UTC) - timedelta(days=7))),
        )
        baseline_ttft = [
            float(value)
            for row in passive
            if (
                value := json.loads(row["payload_json"])
                .get("values", {})
                .get("ttft_p95")
            )
            is not None
        ]
        if len(baseline_ttft) >= self.settings.passive_baseline_buckets:
            recent_cutoff = datetime.now(UTC) - timedelta(minutes=10)
            recent = [
                float(value)
                for row in passive
                if datetime.fromisoformat(row["bucket_at"]) >= recent_cutoff
                and (
                    value := json.loads(row["payload_json"])
                    .get("values", {})
                    .get("ttft_p95")
                )
                is not None
            ]
            if len(recent) >= 2 and all(
                value
                > statistics.median(baseline_ttft) * self.settings.ttft_slow_multiplier
                for value in recent[:2]
            ):
                return "ttft_above_baseline"

        speed_rows = await self.database.query(
            """
            SELECT measurement_json FROM probe_runs
            WHERE deployment_id=? AND kind='speed' AND outcome='success'
              AND finished_at>=?
            ORDER BY finished_at DESC
            """,
            (deployment_id, isoformat(datetime.now(UTC) - timedelta(days=7))),
        )
        speed_values = [
            float(value)
            for row in speed_rows
            if (value := json.loads(row["measurement_json"]).get("probe_decode_tps"))
            is not None
        ]
        if len(speed_values) >= self.settings.speed_baseline_samples:
            baseline = statistics.median(speed_values)
            if all(
                value < baseline * self.settings.speed_slow_ratio
                for value in speed_values[:2]
            ):
                return "speed_probe_below_baseline"
        return None

    async def persist(
        self,
        deployment_id: str,
        service: ServiceState,
        telemetry: TelemetryState,
        reasons: list[str],
    ) -> None:
        now = isoformat()
        existing = await self.database.query(
            "SELECT * FROM current_states WHERE deployment_id=?", (deployment_id,)
        )
        changed = not existing or (
            existing[0]["service_state"] != service
            or existing[0]["telemetry_state"] != telemetry
        )
        telemetry_at = await self.database.scalar(
            "SELECT MAX(observed_at) FROM scrape_snapshots WHERE deployment_id=?",
            (deployment_id,),
        )
        await self.database.write(
            """
            INSERT INTO current_states(
                deployment_id, service_state, telemetry_state, reasons_json,
                telemetry_at, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(deployment_id) DO UPDATE SET
                service_state=excluded.service_state,
                telemetry_state=excluded.telemetry_state,
                reasons_json=excluded.reasons_json,
                telemetry_at=excluded.telemetry_at,
                evaluated_at=excluded.evaluated_at
            """,
            (
                deployment_id,
                service,
                telemetry,
                json.dumps(reasons),
                telemetry_at,
                now,
            ),
        )
        if not changed:
            return
        await self.database.write(
            """
            UPDATE state_history SET ended_at=?
            WHERE deployment_id=? AND ended_at IS NULL
            """,
            (now, deployment_id),
        )
        await self.database.write(
            """
            INSERT INTO state_history(
                deployment_id, service_state, telemetry_state,
                reasons_json, started_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (deployment_id, service, telemetry, json.dumps(reasons), now),
        )
        await self.database.write(
            """
            UPDATE events SET ended_at=?
            WHERE deployment_id=? AND ended_at IS NULL
            """,
            (now, deployment_id),
        )
        if service not in {ServiceState.OPERATIONAL, ServiceState.UNKNOWN}:
            await self.database.write(
                """
                INSERT INTO events(
                    deployment_id, event_key, kind, severity, state,
                    title, detail_json, started_at
                ) VALUES (?, ?, 'service_state', ?, ?, ?, ?, ?)
                """,
                (
                    deployment_id,
                    f"service:{service}",
                    "critical" if service == ServiceState.UNAVAILABLE else "warning",
                    service,
                    f"Service state changed to {service}",
                    json.dumps({"reasons": reasons}),
                    now,
                ),
            )

    async def evaluate_all(self) -> None:
        for deployment in self.catalog.deployments:
            service, telemetry, reasons = await self.evaluate(deployment.deployment_id)
            await self.persist(deployment.deployment_id, service, telemetry, reasons)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.evaluate_all()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=15)
