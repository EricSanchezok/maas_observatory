"""Response state derived only from route liveness checks.

Regression baselines are now isolated by context tier so that a 64K
regression does not affect 1K/16K states.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from maas_common.catalog import ModelCatalog
from maas_observatory.database import Database, isoformat
from maas_observatory.models import ResponseState
from maas_observatory.settings import ObservatorySettings

ALL_TIERS = ("1k", "16k", "64k")


class StateEngine:
    def __init__(
        self,
        catalog: ModelCatalog,
        settings: ObservatorySettings,
        database: Database,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.database = database

    async def evaluate(
        self, deployment_id: str
    ) -> tuple[ResponseState, list[str], str | None, str | None]:
        current = await self.database.query(
            "SELECT response_state FROM current_states WHERE deployment_id=?",
            (deployment_id,),
        )
        if current and current[0]["response_state"] == ResponseState.MAINTENANCE:
            return ResponseState.MAINTENANCE, ["maintenance"], None, None

        routes = await self.database.query(
            """
            SELECT id, finished_at, outcome, error_class, error_code
            FROM probe_runs
            WHERE deployment_id=? AND kind='route'
            ORDER BY finished_at DESC LIMIT 3
            """,
            (deployment_id,),
        )
        attempts = await self.database.query(
            """
            SELECT finished_at
            FROM probe_runs
            WHERE deployment_id=?
              AND kind='experience'
              AND profile_id=? AND definition_version=?
              AND suite_version=? AND collection_mode=?
              AND outcome='success'
            ORDER BY finished_at DESC LIMIT 1
            """,
            (
                deployment_id,
                self.settings.experience.response_profile_id,
                self.settings.experience.definition_version,
                self.settings.experience.suite_version,
                self.settings.collection_mode,
            ),
        )
        last_route_at = routes[0]["finished_at"] if routes else None
        last_response_at = attempts[0]["finished_at"] if attempts else None

        if routes and routes[0]["outcome"] != "success":
            return (
                ResponseState.UNAVAILABLE,
                [str(routes[0]["error_code"] or "route_failed")],
                last_route_at,
                last_response_at,
            )
        if not routes:
            return (
                ResponseState.COLLECTING,
                ["first_check_scheduled"],
                last_route_at,
                last_response_at,
            )
        route_current = (
            self._age(routes[0]["finished_at"])
            <= self.settings.probes.route_interval_seconds * 2
        )
        if route_current:
            return (
                ResponseState.CURRENT,
                ["recent_route_check"],
                last_route_at,
                last_response_at,
            )
        return (
            ResponseState.DELAYED,
            ["route_check_delayed"],
            last_route_at,
            last_response_at,
        )

    @staticmethod
    def _age(timestamp: str) -> float:
        return max(
            0,
            (datetime.now(UTC) - datetime.fromisoformat(timestamp)).total_seconds(),
        )

    async def _regression(self, deployment_id: str) -> list[str]:
        """Aggregate regressions across all context tiers.

        Each tier is evaluated independently — a 64K regression does not
        affect 1K/16K and vice versa.
        """
        all_regressions: list[str] = []
        for tier in ALL_TIERS:
            tier_regressions = await self._regression_for_tier(deployment_id, tier)
            all_regressions.extend(tier_regressions)
        return all_regressions

    async def _regression_for_tier(self, deployment_id: str, tier: str) -> list[str]:
        rows = await self.database.query(
            """
            SELECT measurement_json FROM probe_runs
            WHERE deployment_id=?
              AND kind='experience'
              AND context_tier=?
              AND profile_id=? AND definition_version=? AND suite_version=?
              AND outcome='success' AND finished_at>=?
            ORDER BY finished_at DESC
            """,
            (
                deployment_id,
                tier,
                self.settings.experience.response_profile_id,
                self.settings.experience.definition_version,
                self.settings.experience.suite_version,
                isoformat(datetime.now(UTC) - timedelta(days=7)),
            ),
        )
        measurements = [json.loads(row["measurement_json"]) for row in rows]
        if len(measurements) < self.settings.experience.baseline_min_samples:
            return []
        rules = (
            ("first_response_seconds", 2.0, "high"),
            ("output_speed_tps", 0.7, "low"),
        )
        regressions: list[str] = []
        for metric, ratio, direction in rules:
            values = [
                float(item[metric])
                for item in measurements
                if item.get(metric) is not None
            ]
            if len(values) < self.settings.experience.baseline_min_samples:
                continue
            baseline = statistics.median(values)
            latest = values[:2]
            if len(latest) < 2:
                continue
            if direction == "high" and all(
                value > baseline * ratio for value in latest
            ):
                regressions.append(f"{tier}:{metric}_regression")
            if direction == "low" and all(value < baseline * ratio for value in latest):
                regressions.append(f"{tier}:{metric}_regression")
        return regressions

    async def persist(
        self,
        deployment_id: str,
        state: ResponseState,
        reasons: list[str],
        last_route_at: str | None,
        last_response_at: str | None,
    ) -> None:
        now = isoformat()
        existing = await self.database.query(
            "SELECT * FROM current_states WHERE deployment_id=?", (deployment_id,)
        )
        changed = not existing or existing[0]["response_state"] != state
        await self.database.write(
            """
            INSERT INTO current_states(
                deployment_id, response_state, reasons_json,
                last_route_at, last_response_at, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(deployment_id) DO UPDATE SET
                response_state=excluded.response_state,
                reasons_json=excluded.reasons_json,
                last_route_at=excluded.last_route_at,
                last_response_at=excluded.last_response_at,
                evaluated_at=excluded.evaluated_at
            """,
            (
                deployment_id,
                state,
                json.dumps(reasons),
                last_route_at,
                last_response_at,
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
                deployment_id, response_state, reasons_json, started_at
            ) VALUES (?, ?, ?, ?)
            """,
            (deployment_id, state, json.dumps(reasons), now),
        )
        await self.database.write(
            """
            UPDATE events SET ended_at=?
            WHERE deployment_id=? AND kind='response_state' AND ended_at IS NULL
            """,
            (now, deployment_id),
        )
        if state in {ResponseState.DELAYED, ResponseState.UNAVAILABLE}:
            await self.database.write(
                """
                INSERT INTO events(
                    deployment_id, event_key, kind, severity, state,
                    title, detail_json, started_at
                ) VALUES (?, ?, 'response_state', ?, ?, ?, ?, ?)
                """,
                (
                    deployment_id,
                    f"response:{state}",
                    "critical" if state == ResponseState.UNAVAILABLE else "warning",
                    state,
                    (
                        "Connection unavailable"
                        if state == ResponseState.UNAVAILABLE
                        else "Response checks delayed"
                    ),
                    json.dumps({"reasons": reasons}),
                    now,
                ),
            )

    async def evaluate_all(self) -> None:
        for deployment in self.catalog.deployments:
            state, reasons, route_at, response_at = await self.evaluate(
                deployment.deployment_id
            )
            await self.persist(
                deployment.deployment_id, state, reasons, route_at, response_at
            )
            regressions = await self._regression(deployment.deployment_id)
            if regressions:
                await self._persist_regression(deployment.deployment_id, regressions)

    async def _persist_regression(
        self, deployment_id: str, regressions: list[str]
    ) -> None:
        for reason in regressions:
            event_key = f"response:regression:{reason}"
            active = await self.database.scalar(
                """
                SELECT COUNT(*) FROM events
                WHERE deployment_id=? AND event_key=?
                  AND ended_at IS NULL
                """,
                (deployment_id, event_key),
            )
            if active:
                continue
            now = isoformat()
            await self.database.write(
                """
                INSERT INTO events(
                    deployment_id, event_key, kind, severity, state,
                    title, detail_json, started_at
                ) VALUES (?, ?, 'response_regression',
                          'warning', 'open', 'Response changed', ?, ?)
                """,
                (deployment_id, event_key, json.dumps({"reasons": [reason]}), now),
            )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.evaluate_all()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=15)
