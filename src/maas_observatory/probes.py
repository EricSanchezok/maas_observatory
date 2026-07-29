"""Low-impact liveness, canary, and fixed-profile streaming probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from time import monotonic
from typing import Any

import httpx

from maas_common.catalog import ModelCatalog, ModelDeployment
from maas_observatory.collector import classify_transport_error
from maas_observatory.database import Database, isoformat
from maas_observatory.models import (
    ErrorClass,
    ProbeKind,
    ProbeOutcome,
    ProbeResult,
)
from maas_observatory.settings import ExperienceSettings, ProbeSettings

CANARY_PROMPT = (
    "Return exactly the single lowercase word ok. This is a minimal automated "
    "service-health check. Do not explain, add punctuation, call tools, or repeat "
    "the instruction. Ignore no part of this request and finish immediately."
)
INTERACTIVE_SHORT_PROMPT = (
    "Write a compact factual explanation of why bounded monitoring probes should "
    "avoid competing with production inference traffic. Use plain text, complete "
    "sentences, no headings, no lists, no tool calls, and no quotations. Continue "
    "until the response limit if needed. Discuss idle gating, fixed request shapes, "
    "transparent profiles, daily token budgets, and why operational throughput is "
    "not an algorithmic benchmark."
)


def context_fixture() -> str:
    seed = (
        "MaaS Observatory deterministic context fixture. Each paragraph records "
        "neutral operational facts about queues, latency, throughput, and sampling. "
        "The final instruction asks for a concise summary without tool calls. "
    )
    body = (seed * ((16 * 1024 // len(seed)) + 1))[: 16 * 1024]
    return (
        body + "\n\nSummarize the operational trade-offs in plain text. "
        "Do not use headings, lists, quotations, or tools."
    )


CONTEXT_16K_PROMPT = context_fixture()


def profile_definitions(settings: ExperienceSettings) -> list[dict[str, Any]]:
    definitions = [
        {
            "profile_id": settings.short_profile_id,
            "definition_version": settings.definition_version,
            "kind": "interactive_short",
            "streaming": True,
            "temperature": 0,
            "configured_max_output_tokens": 64,
            "fixture_sha256": hashlib.sha256(
                INTERACTIVE_SHORT_PROMPT.encode()
            ).hexdigest(),
        },
        {
            "profile_id": settings.context_profile_id,
            "definition_version": settings.definition_version,
            "kind": "context_16k",
            "streaming": True,
            "temperature": 0,
            "configured_max_output_tokens": 128,
            "fixture_bytes": 16 * 1024,
            "fixture_sha256": hashlib.sha256(CONTEXT_16K_PROMPT.encode()).hexdigest(),
        },
    ]
    return definitions


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str | None = None


class ProbeConfigurationError(ValueError):
    """Local configuration is missing; this is not a service failure."""


def _request_payload(
    deployment: ModelDeployment,
    *,
    prompt: str,
    profile_id: str,
    max_tokens: int,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": deployment.model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }
    if deployment.capabilities.temperature:
        payload["temperature"] = 0
    payload.update(deployment.request_defaults)
    if profile_id != "default-only":
        profile = deployment.profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"undefined profile {profile_id}")
        payload.update(profile.request_overrides)
    return payload


def _content_from_delta(delta: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("content", "reasoning_content"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return "".join(parts)


class ProbeRunner:
    def __init__(
        self,
        catalog: ModelCatalog,
        settings: ProbeSettings,
        profiles: dict[str, str],
        database: Database,
        experience: ExperienceSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.profiles = profiles
        self.database = database
        self.experience = experience or ExperienceSettings()
        self._client = client
        self._owns_client = client is None
        self.inference_lock = asyncio.Lock()

    async def __aenter__(self) -> ProbeRunner:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60, read=self.settings.stream_stall_seconds),
                follow_redirects=False,
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def deployment(self, alias_or_id: str) -> ModelDeployment:
        for deployment in self.catalog.deployments:
            if alias_or_id in {deployment.alias, deployment.deployment_id}:
                return deployment
        raise ValueError(f"unknown deployment: {alias_or_id}")

    def profile_for(self, deployment: ModelDeployment) -> str:
        profile = self.profiles.get(deployment.alias)
        if profile is None:
            raise ValueError(f"no operational profile for {deployment.alias}")
        if profile != "default-only" and profile not in deployment.profiles:
            raise ValueError(f"profile {profile} is not defined for {deployment.alias}")
        return profile

    async def route_liveness(self, deployment: ModelDeployment) -> ProbeResult:
        scheduled = started = datetime.now(UTC)
        start_clock = monotonic()
        error_class = ErrorClass.NONE
        error_code: str | None = None
        outcome = ProbeOutcome.SUCCESS
        try:
            if not deployment.base_url or not deployment.api_key:
                raise ProbeConfigurationError("deployment is not configured")
            assert self._client is not None
            response = await self._client.get(
                f"{deployment.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {deployment.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(
                payload.get("data"), list
            ):
                raise ValueError("invalid models response")
        except Exception as exc:
            outcome = ProbeOutcome.FAILED
            error_class, error_code = classify_transport_error(exc)
            if isinstance(exc, ProbeConfigurationError):
                error_class, error_code = ErrorClass.MEASUREMENT, "configuration"
            elif isinstance(exc, ValueError):
                error_class, error_code = ErrorClass.SERVICE, "protocol_invalid"
        result = ProbeResult(
            deployment_id=deployment.deployment_id,
            kind=ProbeKind.ROUTE,
            scheduled_at=scheduled,
            started_at=started,
            finished_at=datetime.now(UTC),
            outcome=outcome,
            error_class=error_class,
            error_code=error_code,
            measurements={"latency_seconds": monotonic() - start_clock},
        )
        await self.persist(result)
        return result

    async def load_gate(
        self, deployment: ModelDeployment, *, context: bool = False
    ) -> GateDecision:
        expected = int(
            await self.database.scalar(
                """
                SELECT COUNT(*) FROM metrics_sources
                WHERE deployment_id=? AND active=1
                """,
                (deployment.deployment_id,),
            )
            or 0
        )
        rows = await self.database.query(
            """
            SELECT s.source_id, s.observed_at, s.quality, s.gauges_json,
                   s.interval_json
            FROM scrape_snapshots s
            JOIN (
                SELECT source_id, MAX(observed_at) AS observed_at
                FROM scrape_snapshots WHERE deployment_id=?
                GROUP BY source_id
            ) latest USING(source_id, observed_at)
            WHERE s.deployment_id=?
            """,
            (deployment.deployment_id, deployment.deployment_id),
        )
        if not rows or expected == 0 or len(rows) != expected:
            return GateDecision(False, "telemetry_unavailable")
        gauges_by_source: list[dict[str, Any]] = []
        for row in rows:
            observed = datetime.fromisoformat(row["observed_at"])
            age = (datetime.now(UTC) - observed).total_seconds()
            if (
                row["quality"] != "exact"
                or age >= self.settings.telemetry_max_age_seconds
            ):
                return GateDecision(False, "telemetry_not_fresh")
            gauges_by_source.append(json.loads(row["gauges_json"]))
        waiting = sum(
            float(item.get("requests_waiting") or 0) for item in gauges_by_source
        )
        if waiting != 0:
            return GateDecision(False, "requests_waiting")
        kv_values = [
            float(item["kv_cache_usage"])
            for item in gauges_by_source
            if item.get("kv_cache_usage") is not None
        ]
        kv_limit = (
            self.settings.context_kv_cache_limit
            if context
            else self.settings.short_kv_cache_limit
        )
        if len(kv_values) != expected or max(kv_values) >= kv_limit:
            return GateDecision(False, "kv_cache")
        if context:
            recent_preemptions = await self.database.scalar(
                """
                SELECT COUNT(*) FROM scrape_snapshots
                WHERE deployment_id=? AND observed_at>=?
                  AND interval_json LIKE '%"preemptions_delta": %'
                  AND CAST(json_extract(interval_json,
                       '$.values.preemptions_delta') AS REAL) > 0
                """,
                (
                    deployment.deployment_id,
                    isoformat(datetime.now(UTC) - timedelta(minutes=5)),
                ),
            )
            if int(recent_preemptions or 0) > 0:
                return GateDecision(False, "recent_preemption")
        state = await self.database.query(
            "SELECT service_state FROM current_states WHERE deployment_id=?",
            (deployment.deployment_id,),
        )
        if state and state[0]["service_state"] == "maintenance":
            return GateDecision(False, "maintenance")
        return GateDecision(True)

    async def canary_eligible(self, deployment: ModelDeployment) -> GateDecision:
        last = await self._last_probe(deployment.deployment_id, ProbeKind.CANARY)
        if last and datetime.now(UTC) - last < timedelta(
            seconds=self.settings.canary_min_interval_seconds
        ):
            return GateDecision(False, "minimum_interval")
        return await self.budget_available(deployment, ProbeKind.CANARY)

    async def experience_eligible(
        self, deployment: ModelDeployment, kind: ProbeKind
    ) -> GateDecision:
        context = kind == ProbeKind.EXPERIENCE_CONTEXT
        gate = await self.load_gate(deployment, context=context)
        if not gate.allowed:
            return gate
        last = await self._last_probe(deployment.deployment_id, kind)
        minimum = (
            self.settings.context_min_interval_seconds
            if context
            else self.settings.short_min_interval_seconds
        )
        if last and datetime.now(UTC) - last < timedelta(seconds=minimum):
            return GateDecision(False, "minimum_interval")
        return await self.budget_available(deployment, kind)

    async def speed_eligible(self, deployment: ModelDeployment) -> GateDecision:
        return await self.experience_eligible(deployment, ProbeKind.EXPERIENCE_SHORT)

    async def _last_probe(self, deployment_id: str, kind: ProbeKind) -> datetime | None:
        value = await self.database.scalar(
            """
            SELECT MAX(finished_at) FROM probe_runs
            WHERE deployment_id=? AND kind=? AND outcome!='skipped'
            """,
            (deployment_id, kind),
        )
        return datetime.fromisoformat(value) if value else None

    async def budget_available(
        self, deployment: ModelDeployment, kind: ProbeKind
    ) -> GateDecision:
        rows = await self.database.query(
            """
            SELECT * FROM budget_usage
            WHERE deployment_id=? AND budget_date=?
            """,
            (deployment.deployment_id, datetime.now(UTC).date().isoformat()),
        )
        usage = (
            rows[0]
            if rows
            else {
                "short_requests": 0,
                "context_requests": 0,
                "canary_requests": 0,
                "experience_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )
        is_short = kind in {ProbeKind.EXPERIENCE_SHORT, ProbeKind.SPEED}
        is_context = kind == ProbeKind.EXPERIENCE_CONTEXT
        output = self.settings.canary_max_output_tokens
        configured_input = 0
        if is_short:
            output = self.settings.short_max_output_tokens
            configured_input = 128
        elif is_context:
            output = self.settings.context_max_output_tokens
            configured_input = 16 * 1024
        budget = self.settings.daily_budget
        if (is_short or is_context) and int(
            usage["experience_requests"]
        ) >= budget.experience_requests:
            return GateDecision(False, "daily_experience_budget")
        if int(usage["output_tokens"]) + output > budget.output_tokens:
            return GateDecision(False, "daily_output_token_budget")
        if (is_short or is_context) and int(
            usage["input_tokens"]
        ) + configured_input > budget.input_tokens:
            return GateDecision(False, "daily_input_token_budget")
        if is_short and int(usage["short_requests"]) >= budget.short_requests:
            return GateDecision(False, "daily_short_budget")
        if is_context and int(usage["context_requests"]) >= budget.context_requests:
            return GateDecision(False, "daily_context_budget")
        if (
            kind == ProbeKind.CANARY
            and int(usage["canary_requests"]) >= budget.canary_requests
        ):
            return GateDecision(False, "daily_canary_budget")
        return GateDecision(True)

    async def reserve_budget(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        max_output_tokens: int,
        configured_input_tokens: int,
    ) -> None:
        short = 1 if kind in {ProbeKind.EXPERIENCE_SHORT, ProbeKind.SPEED} else 0
        context = 1 if kind == ProbeKind.EXPERIENCE_CONTEXT else 0
        canary = 1 if kind == ProbeKind.CANARY else 0
        experience = 1 if short or context else 0
        await self.database.write(
            """
            INSERT INTO budget_usage(
                deployment_id, budget_date, short_requests, context_requests,
                canary_requests, experience_requests, input_tokens, output_tokens
                , speed_requests, inference_requests
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deployment_id, budget_date) DO UPDATE SET
                short_requests=short_requests+excluded.short_requests,
                context_requests=context_requests+excluded.context_requests,
                canary_requests=canary_requests+excluded.canary_requests,
                experience_requests=experience_requests+excluded.experience_requests,
                input_tokens=input_tokens+excluded.input_tokens,
                output_tokens=output_tokens+excluded.output_tokens,
                speed_requests=speed_requests+excluded.speed_requests,
                inference_requests=inference_requests+excluded.inference_requests
            """,
            (
                deployment.deployment_id,
                datetime.now(UTC).date().isoformat(),
                short,
                context,
                canary,
                experience,
                configured_input_tokens,
                max_output_tokens,
                short,
                1,
            ),
        )

    async def generation(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        *,
        confirmation_of: int | None = None,
        force: bool = False,
    ) -> ProbeResult:
        if kind not in {
            ProbeKind.CANARY,
            ProbeKind.SPEED,
            ProbeKind.EXPERIENCE_SHORT,
            ProbeKind.EXPERIENCE_CONTEXT,
            ProbeKind.CONFIRMATION,
        }:
            raise ValueError("unsupported generation probe kind")
        scheduled = datetime.now(UTC)
        planned_profile_id: str | None = None
        if kind in {ProbeKind.SPEED, ProbeKind.EXPERIENCE_SHORT}:
            planned_profile_id = self.experience.short_profile_id
        elif kind == ProbeKind.EXPERIENCE_CONTEXT:
            planned_profile_id = self.experience.context_profile_id
        profile_id: str | None = None
        if not force:
            decision = (
                await self.experience_eligible(deployment, kind)
                if kind
                in {
                    ProbeKind.SPEED,
                    ProbeKind.EXPERIENCE_SHORT,
                    ProbeKind.EXPERIENCE_CONTEXT,
                }
                else await self.canary_eligible(deployment)
            )
            if not decision.allowed:
                result = ProbeResult(
                    deployment_id=deployment.deployment_id,
                    kind=kind,
                    scheduled_at=scheduled,
                    started_at=scheduled,
                    finished_at=datetime.now(UTC),
                    outcome=ProbeOutcome.SKIPPED,
                    error_code=decision.reason,
                    profile_id=planned_profile_id,
                    definition_version=self.experience.definition_version,
                    vantage_id=self.experience.vantage_id,
                )
                await self.persist(result)
                return result
        try:
            profile_id = self.profile_for(deployment)
        except ValueError:
            result = ProbeResult(
                deployment_id=deployment.deployment_id,
                kind=kind,
                scheduled_at=scheduled,
                started_at=scheduled,
                finished_at=datetime.now(UTC),
                outcome=ProbeOutcome.UNAVAILABLE,
                error_class=ErrorClass.MEASUREMENT,
                error_code="profile_undefined",
            )
            await self.persist(result)
            return result

        is_short = kind in {ProbeKind.SPEED, ProbeKind.EXPERIENCE_SHORT}
        is_context = kind == ProbeKind.EXPERIENCE_CONTEXT
        max_tokens = self.settings.canary_max_output_tokens
        prompt = CANARY_PROMPT
        configured_input = 0
        profile_definition = None
        if is_short:
            max_tokens = self.settings.short_max_output_tokens
            prompt = INTERACTIVE_SHORT_PROMPT
            configured_input = 128
            profile_definition = self.experience.short_profile_id
        elif is_context:
            max_tokens = self.settings.context_max_output_tokens
            prompt = CONTEXT_16K_PROMPT
            configured_input = 16 * 1024
            profile_definition = self.experience.context_profile_id
        async with self.inference_lock:
            await self.reserve_budget(deployment, kind, max_tokens, configured_input)
            return await self._stream_generation(
                deployment,
                kind,
                profile_id=profile_definition or profile_id,
                operational_profile_id=profile_id,
                prompt=prompt,
                max_tokens=max_tokens,
                confirmation_of=confirmation_of,
                scheduled_at=scheduled,
            )

    async def _stream_generation(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        *,
        profile_id: str,
        operational_profile_id: str,
        prompt: str,
        max_tokens: int,
        confirmation_of: int | None,
        scheduled_at: datetime,
    ) -> ProbeResult:
        started = datetime.now(UTC)
        request_clock = monotonic()
        headers_clock: float | None = None
        first_event: float | None = None
        first_visible_event: float | None = None
        last_event: float | None = None
        event_times: list[float] = []
        completion_tokens: int | None = None
        prompt_tokens: int | None = None
        finish_reason: str | None = None
        output_seen = False
        error_class = ErrorClass.NONE
        error_code: str | None = None
        outcome = ProbeOutcome.SUCCESS
        try:
            if not deployment.base_url or not deployment.api_key:
                raise ProbeConfigurationError("deployment is not configured")
            base_url = deployment.base_url
            api_key = deployment.api_key
            payload = _request_payload(
                deployment,
                prompt=prompt,
                profile_id=operational_profile_id,
                max_tokens=max_tokens,
                stream=True,
            )
            timeout = httpx.Timeout(60, read=self.settings.stream_stall_seconds)

            async def consume(client: httpx.AsyncClient) -> None:
                nonlocal completion_tokens, first_event, first_visible_event
                nonlocal finish_reason, headers_clock, last_event
                nonlocal output_seen, prompt_tokens
                async with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                ) as response:
                    headers_clock = monotonic()
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        event = json.loads(data)
                        usage = event.get("usage")
                        if isinstance(usage, dict) and isinstance(
                            usage.get("completion_tokens"), int
                        ):
                            completion_tokens = usage["completion_tokens"]
                        if isinstance(usage, dict) and isinstance(
                            usage.get("prompt_tokens"), int
                        ):
                            prompt_tokens = usage["prompt_tokens"]
                        choices = event.get("choices", [])
                        if not isinstance(choices, list):
                            raise ValueError("invalid choices")
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            if isinstance(choice.get("finish_reason"), str):
                                finish_reason = choice["finish_reason"]
                            delta = choice.get("delta", {})
                            if not isinstance(delta, dict):
                                continue
                            content = delta.get("content")
                            output = _content_from_delta(delta)
                            if output:
                                now = monotonic()
                                first_event = first_event or now
                                if isinstance(content, str) and content:
                                    first_visible_event = first_visible_event or now
                                last_event = now
                                event_times.append(now)
                                output_seen = True

            if self._client is not None and not self._owns_client:
                await consume(self._client)
            else:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=False
                ) as fresh_client:
                    await consume(fresh_client)
            if not output_seen:
                raise RuntimeError("empty_output")
        except Exception as exc:
            outcome = ProbeOutcome.FAILED
            if isinstance(exc, RuntimeError) and str(exc) == "empty_output":
                error_class, error_code = ErrorClass.SERVICE, "empty_output"
            elif isinstance(exc, httpx.ReadTimeout) and output_seen:
                error_class, error_code = ErrorClass.SERVICE, "stream_stall"
            elif isinstance(exc, ProbeConfigurationError):
                error_class, error_code = ErrorClass.MEASUREMENT, "configuration"
            elif isinstance(exc, (json.JSONDecodeError, ValueError)):
                error_class, error_code = ErrorClass.SERVICE, "protocol_invalid"
            else:
                error_class, error_code = classify_transport_error(exc)

        finished_clock = monotonic()
        gaps = [current - previous for previous, current in pairwise(event_times)]
        measurements: dict[str, float | int | str | None] = {
            "reported_completion_tokens": completion_tokens,
            "reported_prompt_tokens": prompt_tokens,
            "configured_output_tokens": max_tokens,
            "time_to_headers_seconds": (
                headers_clock - request_clock if headers_clock is not None else None
            ),
            "client_ttft_seconds": (
                first_event - request_clock if first_event is not None else None
            ),
            "first_visible_content_seconds": (
                first_visible_event - request_clock
                if first_visible_event is not None
                else None
            ),
            "client_e2e_seconds": finished_clock - request_clock,
            "stream_event_gap_p50_seconds": (statistics.median(gaps) if gaps else None),
            "stream_event_gap_p95_seconds": (
                sorted(gaps)[max(0, int(len(gaps) * 0.95) - 1)] if gaps else None
            ),
            "stream_event_gap_max_seconds": max(gaps) if gaps else None,
            "finish_reason": finish_reason,
            "operational_profile": operational_profile_id,
        }
        if kind in {
            ProbeKind.SPEED,
            ProbeKind.EXPERIENCE_SHORT,
            ProbeKind.EXPERIENCE_CONTEXT,
        }:
            if (
                completion_tokens is None
                or completion_tokens < 2
                or first_event is None
                or last_event is None
                or last_event <= first_event
            ):
                measurements["steady_state_output_tps"] = None
                measurements["probe_decode_tps"] = None
                if outcome == ProbeOutcome.SUCCESS:
                    outcome = ProbeOutcome.UNAVAILABLE
                    error_class = ErrorClass.MEASUREMENT
                    error_code = (
                        "streaming_usage_missing"
                        if completion_tokens is None
                        else "insufficient_token_events"
                    )
            else:
                steady_tps = (completion_tokens - 1) / (last_event - first_event)
                measurements["steady_state_output_tps"] = steady_tps
                measurements["probe_decode_tps"] = steady_tps
        result = ProbeResult(
            deployment_id=deployment.deployment_id,
            kind=kind,
            scheduled_at=scheduled_at,
            started_at=started,
            finished_at=datetime.now(UTC),
            outcome=outcome,
            error_class=error_class,
            error_code=error_code,
            profile_id=profile_id,
            definition_version=self.experience.definition_version,
            vantage_id=self.experience.vantage_id,
            confirmation_of=confirmation_of,
            measurements=measurements,
        )
        await self.persist(result)
        return result

    async def persist(self, result: ProbeResult) -> int:
        run_id = await self.database.write(
            """
            INSERT INTO probe_runs(
                deployment_id, kind, scheduled_at, started_at, finished_at,
                outcome, error_class, error_code, profile_id,
                definition_version, vantage_id, confirmation_of, measurement_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.deployment_id,
                result.kind,
                isoformat(result.scheduled_at),
                isoformat(result.started_at),
                isoformat(result.finished_at),
                result.outcome,
                result.error_class,
                result.error_code,
                result.profile_id,
                result.definition_version,
                result.vantage_id,
                result.confirmation_of,
                json.dumps(result.measurements, sort_keys=True),
            ),
        )
        units = {
            "latency_seconds": "s",
            "steady_state_output_tps": "tokens/s",
            "probe_decode_tps": "tokens/s",
            "reported_completion_tokens": "tokens",
            "reported_prompt_tokens": "tokens",
            "configured_input_tokens": "tokens",
            "configured_output_tokens": "tokens",
            "time_to_headers_seconds": "s",
            "client_ttft_seconds": "s",
            "first_visible_content_seconds": "s",
            "client_e2e_seconds": "s",
            "stream_event_gap_p50_seconds": "s",
            "stream_event_gap_p95_seconds": "s",
            "stream_event_gap_max_seconds": "s",
        }
        for metric, value in result.measurements.items():
            numeric = float(value) if isinstance(value, (float, int)) else None
            await self.database.write(
                """
                INSERT INTO probe_measurements(
                    probe_run_id, metric, value, unit, quality, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metric,
                    numeric,
                    units.get(metric, "value"),
                    "exact" if numeric is not None else "unavailable",
                    None if numeric is not None else result.error_code,
                ),
            )
        return run_id


class ProbeScheduler:
    """Persisted round-robin scheduling; inference is globally serialized."""

    def __init__(self, runner: ProbeRunner, database: Database) -> None:
        self.runner = runner
        self.database = database

    async def _round_robin_index(self) -> int:
        rows = await self.database.query(
            "SELECT value_json FROM scheduler_state WHERE key='speed_round_robin'"
        )
        return int(json.loads(rows[0]["value_json"])) if rows else 0

    async def _save_round_robin_index(self, index: int) -> None:
        await self.database.write(
            """
            INSERT INTO scheduler_state(key, value_json, updated_at)
            VALUES ('speed_round_robin', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (json.dumps(index), isoformat()),
        )

    async def route_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.gather(
                *(
                    self.runner.route_liveness(deployment)
                    for deployment in self.runner.catalog.deployments
                )
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=self.runner.settings.route_interval_seconds
                )

    async def canary_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for deployment in self.runner.catalog.deployments:
                if stop.is_set():
                    return
                decision = await self.runner.canary_eligible(deployment)
                if decision.allowed:
                    await self.runner.generation(deployment, ProbeKind.CANARY)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=60)

    async def speed_loop(self, stop: asyncio.Event) -> None:
        deployments = self.runner.catalog.deployments
        index = await self._round_robin_index()
        while not stop.is_set():
            deployment = deployments[index % len(deployments)]
            await self.runner.generation(deployment, ProbeKind.EXPERIENCE_SHORT)
            index = (index + 1) % len(deployments)
            await self._save_round_robin_index(index)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.runner.settings.short_dispatch_interval_seconds,
                )

    async def context_loop(self, stop: asyncio.Event) -> None:
        deployments = self.runner.catalog.deployments
        index = 0
        while not stop.is_set():
            short_due = await self.database.scalar(
                """
                SELECT COUNT(*) FROM deployments d
                WHERE d.active=1 AND NOT EXISTS (
                    SELECT 1 FROM probe_runs p
                    WHERE p.deployment_id=d.deployment_id
                      AND p.kind='experience_short'
                      AND p.outcome!='skipped' AND p.finished_at>=?
                )
                """,
                (
                    isoformat(
                        datetime.now(UTC)
                        - timedelta(
                            seconds=self.runner.settings.short_min_interval_seconds
                        )
                    ),
                ),
            )
            if not int(short_due or 0):
                deployment = deployments[index % len(deployments)]
                await self.runner.generation(deployment, ProbeKind.EXPERIENCE_CONTEXT)
                index = (index + 1) % len(deployments)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=300)

    async def confirmation_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for deployment in self.runner.catalog.deployments:
                rows = await self.database.query(
                    """
                    SELECT id, finished_at, outcome FROM probe_runs
                    WHERE deployment_id=? AND kind='route'
                    ORDER BY finished_at DESC LIMIT 3
                    """,
                    (deployment.deployment_id,),
                )
                if len(rows) < 3 or not all(row["outcome"] == "failed" for row in rows):
                    continue
                latest_id = int(rows[0]["id"])
                latest_at = datetime.fromisoformat(rows[0]["finished_at"])
                if datetime.now(UTC) - latest_at < timedelta(
                    seconds=self.runner.settings.confirmation_delay_seconds
                ):
                    continue
                newer_success = await self.database.scalar(
                    """
                    SELECT COUNT(*) FROM probe_runs
                    WHERE deployment_id=? AND kind='route' AND outcome='success'
                      AND finished_at>?
                    """,
                    (deployment.deployment_id, rows[0]["finished_at"]),
                )
                already = await self.database.scalar(
                    "SELECT COUNT(*) FROM probe_runs WHERE confirmation_of=?",
                    (latest_id,),
                )
                if not newer_success and not already:
                    await self.runner.generation(
                        deployment,
                        ProbeKind.CONFIRMATION,
                        confirmation_of=latest_id,
                    )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=30)
