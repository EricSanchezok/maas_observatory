"""Real-request response probes and deterministic block scheduling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from time import monotonic
from typing import Any, cast

import httpx

from maas_common.catalog import ModelCatalog, ModelDeployment
from maas_observatory.database import Database, isoformat
from maas_observatory.fixtures import (
    FIXTURE_IDS,
    all_metadata,
    get_payload,
    tier_for,
)
from maas_observatory.models import (
    ErrorClass,
    ProbeKind,
    ProbeOutcome,
    ProbeResult,
)
from maas_observatory.settings import (
    CollectionMode,
    ExperienceSettings,
    ProbeSettings,
)

CANARY_PROMPT = "Return exactly the lowercase word ok."

_FIXTURE_ORDER: list[str] = list(FIXTURE_IDS)


class ProbeConfigurationError(ValueError):
    """Local configuration is missing; this is not a service failure."""


def _observer_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Create a direct client that cannot inherit workstation proxy settings."""
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def classify_error(exc: Exception) -> tuple[ErrorClass, str]:
    if isinstance(exc, httpx.TimeoutException):
        return ErrorClass.TRANSPORT, "timeout"
    if isinstance(exc, httpx.ConnectError):
        return ErrorClass.TRANSPORT, "connect"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return ErrorClass.SERVICE, f"http_{status}"
    return ErrorClass.MEASUREMENT, type(exc).__name__.lower()


def block_nonce(epoch: int, block_index: int) -> str:
    return hashlib.sha256(f"{epoch}:{block_index}".encode()).hexdigest()[:16]


def fixture_prompt(fixture_id: str, nonce: str) -> tuple[str, dict[str, Any]]:
    """Return the fixture_id and an OpenAI payload dict with nonce injected.

    The nonce is injected into the final user message content.
    """
    meta = all_metadata().get(fixture_id)
    if meta is None:
        raise ValueError(f"unknown fixture_id: {fixture_id}")
    payload = get_payload(fixture_id)
    # Shallow copy so we can inject nonce into the final user message
    payload = {**payload}
    messages = [dict(m) for m in payload["messages"]]
    last_msg = dict(messages[-1])
    last_msg["content"] = str(last_msg["content"]).replace("{nonce}", nonce)
    messages[-1] = last_msg
    payload["messages"] = messages
    return fixture_id, payload


def fixture_hashes() -> dict[str, str]:
    """Return {fixture_id: sha256} for all six agent fixtures."""
    from maas_observatory.fixtures import fixture_hashes as _fixture_hashes

    return _fixture_hashes()


def profile_definitions(
    settings: ExperienceSettings,
) -> list[dict[str, Any]]:
    hashes = fixture_hashes()
    meta = all_metadata()
    return [
        {
            "profile_id": settings.response_profile_id,
            "definition_version": settings.definition_version,
            "suite_version": settings.suite_version,
            "kind": "agent_response",
            "streaming": True,
            "temperature": 0,
            "fixtures": [
                {
                    "fixture_id": fid,
                    "context_tier": meta[fid]["context_tier"],
                    "target_input_tokens": meta[fid]["target_input_tokens"],
                    "ref_prompt_tokens": meta[fid]["ref_prompt_tokens"],
                    "payload_bytes": meta[fid]["payload_bytes"],
                    "context_bytes": meta[fid]["context_bytes"],
                    "reference_tokenizer": meta[fid]["reference_tokenizer"],
                    "sha256": hashes[fid],
                }
                for fid in FIXTURE_IDS
            ],
        },
    ]


def balanced_order(
    deployments: list[ModelDeployment], block_index: int
) -> list[ModelDeployment]:
    if not deployments:
        return []
    offset = block_index % len(deployments)
    ordered = deployments[offset:] + deployments[:offset]
    if (block_index // len(deployments)) % 2:
        ordered = list(reversed(ordered))
    return ordered


def _request_payload(
    deployment: ModelDeployment,
    *,
    payload: dict[str, Any],
    operational_profile: str,
    max_tokens: int,
) -> dict[str, Any]:
    req: dict[str, Any] = {
        "model": deployment.model_id,
        "messages": payload["messages"],
        "tools": payload.get("tools"),
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if deployment.capabilities.temperature:
        req["temperature"] = 0
    req.update(deployment.request_defaults)
    if operational_profile != "default-only":
        profile = deployment.profiles.get(operational_profile)
        if profile is None:
            raise ValueError(f"undefined profile {operational_profile}")
        req.update(profile.request_overrides)
    return req


def _text_from_delta_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text", item.get("content"))
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _output_from_delta(delta: dict[str, Any]) -> tuple[str, str]:
    content = _text_from_delta_value(delta.get("content"))
    reasoning = (
        _text_from_delta_value(delta.get("reasoning_content"))
        or _text_from_delta_value(delta.get("reasoning"))
        or _text_from_delta_value(delta.get("reasoning_details"))
    )
    return content, reasoning


def _check_tool_call(choices: list[dict[str, Any]]) -> bool:
    """Return True if any choice contains a tool call delta."""
    for choice in choices:
        delta = choice.get("delta", {})
        if isinstance(delta, dict) and "tool_calls" in delta:
            return True
    return False


class ProbeRunner:
    def __init__(
        self,
        catalog: ModelCatalog,
        settings: ProbeSettings,
        profiles: dict[str, str],
        database: Database,
        experience: ExperienceSettings,
        collection_mode: CollectionMode,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.profiles = profiles
        self.database = database
        self.experience = experience
        self.collection_mode = collection_mode
        self._client = client
        self._owns_client = client is None
        self._inference_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> ProbeRunner:
        if self._client is None:
            self._client = _observer_http_client(httpx.Timeout(30))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def deployment(self, alias_or_id: str) -> ModelDeployment:
        for deployment in self.catalog.deployments:
            if alias_or_id in {deployment.alias, deployment.deployment_id}:
                return deployment
        raise ValueError(f"unknown deployment: {alias_or_id}")

    def _inference_lock(self, deployment_id: str) -> asyncio.Lock:
        lock = self._inference_locks.get(deployment_id)
        if lock is None:
            lock = asyncio.Lock()
            self._inference_locks[deployment_id] = lock
        return lock

    def profile_for(self, deployment: ModelDeployment) -> str:
        profile = self.profiles.get(deployment.alias)
        if profile is None:
            raise ValueError(f"no operational profile for {deployment.alias}")
        if profile != "default-only" and profile not in deployment.profiles:
            raise ValueError(f"profile {profile} is not defined for {deployment.alias}")
        return profile

    async def route_liveness(self, deployment: ModelDeployment) -> ProbeResult:
        scheduled = started = datetime.now(UTC)
        started_clock = monotonic()
        outcome = ProbeOutcome.SUCCESS
        error_class = ErrorClass.NONE
        error_code: str | None = None
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
            if isinstance(exc, ProbeConfigurationError):
                error_class, error_code = ErrorClass.MEASUREMENT, "configuration"
            elif isinstance(exc, (ValueError, json.JSONDecodeError)):
                error_class, error_code = ErrorClass.SERVICE, "protocol_invalid"
            else:
                error_class, error_code = classify_error(exc)
        result = ProbeResult(
            deployment_id=deployment.deployment_id,
            kind=ProbeKind.ROUTE,
            scheduled_at=scheduled,
            started_at=started,
            finished_at=datetime.now(UTC),
            outcome=outcome,
            error_class=error_class,
            error_code=error_code,
            measurements={"route_latency_seconds": monotonic() - started_clock},
        )
        await self.persist(result)
        return result

    async def _maintenance(self, deployment_id: str) -> bool:
        return bool(
            await self.database.scalar(
                """
                SELECT COUNT(*) FROM current_states
                WHERE deployment_id=? AND response_state='maintenance'
                """,
                (deployment_id,),
            )
        )

    async def generation(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        *,
        fixture_id: str | None = None,
        prompt_data: dict[str, Any] | None = None,
        prompt: str | None = None,
        block_id: str | None = None,
        scheduled_at: datetime | None = None,
        confirmation_of: int | None = None,
        force: bool = False,
    ) -> ProbeResult:
        if kind not in {
            ProbeKind.CANARY,
            ProbeKind.EXPERIENCE,
            ProbeKind.CONFIRMATION,
        }:
            raise ValueError("unsupported generation probe kind")
        scheduled = scheduled_at or datetime.now(UTC)
        profile_id: str | None = None
        max_tokens = self.settings.canary_max_output_tokens
        selected_prompt = prompt or CANARY_PROMPT
        payload: dict[str, Any] | None = None
        ref_prompt_tokens = 0
        context_tier: str | None = None
        if kind == ProbeKind.EXPERIENCE:
            if prompt_data is None:
                raise ValueError("experience probes require prompt_data")
            profile_id = self.experience.response_profile_id
            max_tokens = deployment.output_limit
            selected_prompt = prompt_data["messages"][-1]["content"]
            payload = prompt_data
            if fixture_id is not None:
                context_tier = str(tier_for(fixture_id))
                meta = all_metadata().get(fixture_id, {})
                ref_prompt_tokens = meta.get("ref_prompt_tokens", 0)
        if not force and await self._maintenance(deployment.deployment_id):
            return await self._persist_skipped(
                deployment,
                kind,
                scheduled,
                profile_id,
                fixture_id,
                block_id,
                context_tier,
                "maintenance",
            )
        try:
            operational_profile = self.profile_for(deployment)
        except ValueError:
            return await self._persist_skipped(
                deployment,
                kind,
                scheduled,
                profile_id,
                fixture_id,
                block_id,
                context_tier,
                "profile_undefined",
                measurement=True,
            )
        async with self._inference_lock(deployment.deployment_id):
            return await self._stream_generation(
                deployment,
                kind,
                profile_id=profile_id or operational_profile,
                operational_profile=operational_profile,
                prompt=selected_prompt,
                payload=payload,
                max_tokens=max_tokens,
                fixture_id=fixture_id,
                block_id=block_id,
                context_tier=context_tier,
                scheduled_at=scheduled,
                confirmation_of=confirmation_of,
                ref_prompt_tokens=ref_prompt_tokens,
            )

    async def _persist_skipped(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        scheduled: datetime,
        profile_id: str | None,
        fixture_id: str | None,
        block_id: str | None,
        context_tier: str | None,
        reason: str | None,
        *,
        measurement: bool = False,
    ) -> ProbeResult:
        result = ProbeResult(
            deployment_id=deployment.deployment_id,
            kind=kind,
            scheduled_at=scheduled,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            outcome=ProbeOutcome.SKIPPED,
            error_class=ErrorClass.MEASUREMENT if measurement else ErrorClass.NONE,
            error_code=reason,
            profile_id=profile_id,
            definition_version=self.experience.definition_version,
            suite_version=self.experience.suite_version,
            vantage_id=self.experience.vantage_id,
            collection_mode=self.collection_mode,
            fixture_id=fixture_id,
            block_id=block_id,
            context_tier=context_tier,
        )
        await self.persist(result)
        return result

    async def _stream_generation(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        *,
        profile_id: str,
        operational_profile: str,
        prompt: str,
        payload: dict[str, Any] | None,
        max_tokens: int,
        fixture_id: str | None,
        block_id: str | None,
        context_tier: str | None,
        scheduled_at: datetime,
        confirmation_of: int | None,
        ref_prompt_tokens: int,
    ) -> ProbeResult:
        started = datetime.now(UTC)
        scheduler_lag = max(0.0, (started - scheduled_at).total_seconds())
        request_clock = monotonic()
        headers_clock: float | None = None
        first_event: float | None = None
        first_visible: float | None = None
        last_event: float | None = None
        event_times: list[float] = []
        completion_tokens: int | None = None
        prompt_tokens: int | None = None
        reported_reasoning_tokens: int | None = None
        finish_reason: str | None = None
        visible_seen = False
        output_seen = False
        tool_call_detected = False
        outcome = ProbeOutcome.SUCCESS
        error_class = ErrorClass.NONE
        error_code: str | None = None
        reasoning_chars = 0
        try:
            if not deployment.base_url or not deployment.api_key:
                raise ProbeConfigurationError("deployment is not configured")
            base_url = deployment.base_url
            api_key = deployment.api_key
            if payload is not None:
                req_payload = _request_payload(
                    deployment,
                    payload=payload,
                    operational_profile=operational_profile,
                    max_tokens=max_tokens,
                )
            else:
                req_payload = _request_payload(
                    deployment,
                    payload={
                        "messages": [{"role": "user", "content": prompt}],
                        "tools": None,
                    },
                    operational_profile=operational_profile,
                    max_tokens=max_tokens,
                )
            timeout = httpx.Timeout(
                connect=30,
                read=self.settings.response_start_timeout_seconds,
                write=30,
                pool=30,
            )

            async def consume(client: httpx.AsyncClient) -> None:
                nonlocal headers_clock, first_event, first_visible, last_event
                nonlocal completion_tokens, prompt_tokens, reported_reasoning_tokens
                nonlocal finish_reason, visible_seen, output_seen, reasoning_chars
                nonlocal tool_call_detected
                async with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=req_payload,
                ) as response:
                    headers_clock = monotonic()
                    response.raise_for_status()
                    lines = response.aiter_lines()
                    while True:
                        line_timeout = float(self.settings.stream_stall_seconds)
                        if not output_seen:
                            first_output_deadline = (
                                request_clock
                                + self.settings.response_start_timeout_seconds
                            )
                            line_timeout = max(0.0, first_output_deadline - monotonic())
                            if line_timeout == 0:
                                raise TimeoutError
                        try:
                            async with asyncio.timeout(line_timeout):
                                line = await anext(lines)
                        except StopAsyncIteration:
                            break
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        event = json.loads(data)
                        usage = event.get("usage")
                        if isinstance(usage, dict):
                            if isinstance(usage.get("completion_tokens"), int):
                                completion_tokens = usage["completion_tokens"]
                            if isinstance(usage.get("prompt_tokens"), int):
                                prompt_tokens = usage["prompt_tokens"]
                            completion_details = usage.get("completion_tokens_details")
                            if isinstance(completion_details, dict) and isinstance(
                                completion_details.get("reasoning_tokens"), int
                            ):
                                reported_reasoning_tokens = completion_details[
                                    "reasoning_tokens"
                                ]
                            elif isinstance(usage.get("reasoning_tokens"), int):
                                reported_reasoning_tokens = usage["reasoning_tokens"]
                        choices = event.get("choices", [])
                        if not isinstance(choices, list):
                            raise ValueError("invalid choices")
                        event_has_tool_call = _check_tool_call(choices)
                        if event_has_tool_call:
                            tool_call_detected = True
                        event_had_output = False
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            if isinstance(choice.get("finish_reason"), str):
                                finish_reason = choice["finish_reason"]
                            delta = choice.get("delta", {})
                            if not isinstance(delta, dict):
                                continue
                            content, reasoning = _output_from_delta(delta)
                            if content or reasoning:
                                now = monotonic()
                                first_event = first_event or now
                                last_event = now
                                event_times.append(now)
                                event_had_output = True
                                output_seen = True
                                reasoning_chars += len(reasoning)
                                if content:
                                    first_visible = first_visible or now
                                    visible_seen = True
                        if event_has_tool_call and not event_had_output:
                            now = monotonic()
                            first_event = first_event or now
                            last_event = now
                            event_times.append(now)
                            output_seen = True

            if self._client is not None and not self._owns_client:
                await consume(self._client)
            else:
                async with _observer_http_client(timeout) as client:
                    await consume(client)
            if tool_call_detected:
                raise RuntimeError("unexpected_tool_call")
            if not visible_seen:
                raise RuntimeError("empty_visible_output")
        except Exception as exc:
            outcome = ProbeOutcome.FAILED
            if isinstance(exc, RuntimeError):
                reason = str(exc)
                if reason == "empty_visible_output":
                    error_class, error_code = ErrorClass.SERVICE, "empty_visible_output"
                elif reason == "unexpected_tool_call":
                    error_class, error_code = ErrorClass.SERVICE, "unexpected_tool_call"
                else:
                    error_class, error_code = classify_error(exc)
            elif isinstance(exc, TimeoutError):
                error_class = ErrorClass.SERVICE
                error_code = "stream_stall" if output_seen else "response_start_timeout"
            elif isinstance(exc, httpx.ReadTimeout) and headers_clock is not None:
                error_class, error_code = ErrorClass.SERVICE, "response_start_timeout"
            elif isinstance(exc, ProbeConfigurationError):
                error_class, error_code = ErrorClass.MEASUREMENT, "configuration"
            elif isinstance(exc, (json.JSONDecodeError, ValueError)):
                error_class, error_code = ErrorClass.SERVICE, "protocol_invalid"
            else:
                error_class, error_code = classify_error(exc)

        prompt_token_deviation_pct: float | None = None
        prompt_token_quality = "unavailable"
        if ref_prompt_tokens > 0 and prompt_tokens is not None and prompt_tokens > 0:
            deviation = abs(prompt_tokens - ref_prompt_tokens) / ref_prompt_tokens
            prompt_token_deviation_pct = deviation * 100
            prompt_token_quality = (
                "reference_mismatch" if deviation > 0.15 else "reported"
            )

        gaps = [current - previous for previous, current in pairwise(event_times)]
        output_speed: float | None = None
        if (
            completion_tokens is not None
            and completion_tokens >= 2
            and first_event is not None
            and last_event is not None
            and last_event > first_event
        ):
            output_speed = (completion_tokens - 1) / (last_event - first_event)
        elif outcome == ProbeOutcome.SUCCESS:
            error_class = ErrorClass.MEASUREMENT
            error_code = (
                "streaming_usage_missing"
                if completion_tokens is None
                else "insufficient_token_events"
            )

        # first_token_seconds = stream_start_seconds (reasoning-inclusive)
        first_token_secs = (
            first_event - request_clock if first_event is not None else None
        )
        total_response_secs = (
            last_event - request_clock if last_event is not None else None
        )

        measurements: dict[str, float | int | str | None] = {
            "reported_completion_tokens": completion_tokens,
            "reported_prompt_tokens": prompt_tokens,
            "ref_prompt_tokens": ref_prompt_tokens or None,
            "prompt_token_deviation_pct": prompt_token_deviation_pct,
            "prompt_token_quality": prompt_token_quality,
            "configured_output_tokens": max_tokens,
            "time_to_headers_seconds": (
                headers_clock - request_clock if headers_clock is not None else None
            ),
            "first_token_seconds": first_token_secs,
            "stream_start_seconds": first_token_secs,
            "first_response_seconds": (
                first_visible - request_clock if first_visible is not None else None
            ),
            "total_response_seconds": total_response_secs,
            "output_speed_tps": output_speed,
            "stream_gap_p50_seconds": statistics.median(gaps) if gaps else None,
            "stream_gap_p95_seconds": (
                sorted(gaps)[max(0, int(len(gaps) * 0.95) - 1)] if gaps else None
            ),
            "stream_gap_max_seconds": max(gaps) if gaps else None,
            "reasoning_chars": reasoning_chars if reasoning_chars else None,
            "reported_reasoning_tokens": reported_reasoning_tokens,
            "reasoning_tokens_estimated": (
                (reasoning_chars + 3) // 4 if reasoning_chars else None
            ),
            "finish_reason": finish_reason,
            "operational_profile": operational_profile,
        }
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
            suite_version=self.experience.suite_version,
            vantage_id=self.experience.vantage_id,
            collection_mode=self.collection_mode,
            fixture_id=fixture_id,
            block_id=block_id,
            scheduler_lag_seconds=scheduler_lag,
            confirmation_of=confirmation_of,
            context_tier=context_tier,
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
                definition_version, suite_version, vantage_id,
                collection_mode, fixture_id, block_id, scheduler_lag_seconds,
                confirmation_of, context_tier, measurement_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                result.suite_version,
                result.vantage_id,
                result.collection_mode,
                result.fixture_id,
                result.block_id,
                result.scheduler_lag_seconds,
                result.confirmation_of,
                result.context_tier,
                json.dumps(result.measurements, sort_keys=True),
            ),
        )
        units = {
            "route_latency_seconds": "s",
            "reported_completion_tokens": "tokens",
            "reported_prompt_tokens": "tokens",
            "ref_prompt_tokens": "tokens",
            "prompt_token_deviation_pct": "%",
            "configured_output_tokens": "tokens",
            "time_to_headers_seconds": "s",
            "first_token_seconds": "s",
            "stream_start_seconds": "s",
            "first_response_seconds": "s",
            "total_response_seconds": "s",
            "stream_gap_p50_seconds": "s",
            "stream_gap_p95_seconds": "s",
            "stream_gap_max_seconds": "s",
            "reasoning_chars": "chars",
            "reported_reasoning_tokens": "tokens",
            "reasoning_tokens_estimated": "tokens",
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
    """Persisted block scheduling with per-deployment inference locks.

    Six fixed fixtures in strict order across blocks:
      block 0: agent-1k-a, block 1: agent-16k-a, block 2: agent-64k-a,
      block 3: agent-1k-b, block 4: agent-16k-b, block 5: agent-64k-b,
      then repeat.
    """

    def __init__(self, runner: ProbeRunner, database: Database) -> None:
        self.runner = runner
        self.database = database

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

    async def _load_schedule(self) -> dict[str, Any]:
        rows = await self.database.query(
            "SELECT value_json FROM scheduler_state WHERE key='response_blocks'"
        )
        if rows:
            value = cast(dict[str, Any], json.loads(rows[0]["value_json"]))
            if value.get("suite_version") == self.runner.experience.suite_version:
                return value
        value = {
            "block_index": 0,
            "next_block_at": None,
            "suite_version": self.runner.experience.suite_version,
        }
        await self._save_schedule(value)
        return value

    async def _save_schedule(self, value: dict[str, Any]) -> None:
        await self.database.write(
            """
            INSERT INTO scheduler_state(key, value_json, updated_at)
            VALUES ('response_blocks', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (json.dumps(value, sort_keys=True), isoformat()),
        )

    async def run_block(
        self,
        fixture_id: str,
        block_index: int,
        scheduled_at: datetime,
        stop: asyncio.Event | None = None,
    ) -> None:
        deployments = balanced_order(list(self.runner.catalog.deployments), block_index)
        if not deployments:
            return
        epoch = await self.database.current_epoch()
        nonce = block_nonce(epoch, block_index)
        fid, payload = fixture_prompt(fixture_id, nonce)
        profile_id = self.runner.experience.response_profile_id
        block_id = f"{epoch}:{self.runner.collection_mode}:{profile_id}:{block_index}"
        started = datetime.now(UTC)
        lag = max(0.0, (started - scheduled_at).total_seconds())
        await self.database.write(
            """
            INSERT OR REPLACE INTO collection_blocks(
                block_id, profile_id, fixture_id, collection_mode,
                scheduled_at, started_at, order_json,
                scheduler_lag_seconds, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                block_id,
                profile_id,
                fid,
                self.runner.collection_mode,
                isoformat(scheduled_at),
                isoformat(started),
                json.dumps([item.deployment_id for item in deployments]),
                lag,
            ),
        )
        status = "complete"
        if stop is not None and stop.is_set():
            status = "interrupted"
        else:
            async with asyncio.TaskGroup() as tasks:
                for deployment in deployments:
                    tasks.create_task(
                        self.runner.generation(
                            deployment,
                            ProbeKind.EXPERIENCE,
                            fixture_id=fid,
                            prompt_data=payload,
                            block_id=block_id,
                            scheduled_at=scheduled_at,
                        )
                    )
            if stop is not None and stop.is_set():
                status = "interrupted"
        await self.database.write(
            """
            UPDATE collection_blocks SET finished_at=?, status=?
            WHERE block_id=?
            """,
            (isoformat(), status, block_id),
        )

    async def response_loop(self, stop: asyncio.Event) -> None:
        state = await self._load_schedule()
        while not stop.is_set():
            now = datetime.now(UTC)
            block_index = int(state["block_index"])
            next_at = (
                datetime.fromisoformat(state["next_block_at"])
                if state["next_block_at"]
                else now
            )
            delay = (next_at - now).total_seconds()
            if delay > 0:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                continue
            if self.runner.collection_mode == "rapid":
                rapid_tier = self.runner.settings.rapid_context_tier
                if rapid_tier is None:
                    raise RuntimeError("rapid mode requires a configured context tier")
                fixture_order = [
                    f"agent-{rapid_tier}-a",
                    f"agent-{rapid_tier}-b",
                ]
            else:
                fixture_order = _FIXTURE_ORDER
            fixture_id = fixture_order[block_index % len(fixture_order)]
            await self.run_block(fixture_id, block_index, next_at, stop)
            state["block_index"] = block_index + 1
            interval = (
                self.runner.settings.rapid_block_interval_seconds
                if self.runner.collection_mode == "rapid"
                else self.runner.settings.standard_block_interval_seconds
            )
            state["next_block_at"] = isoformat(
                datetime.now(UTC) + timedelta(seconds=interval)
            )
            await self._save_schedule(state)

    async def confirmation_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for deployment in self.runner.catalog.deployments:
                routes = await self.database.query(
                    """
                    SELECT id, finished_at, outcome FROM probe_runs
                    WHERE deployment_id=? AND kind='route'
                    ORDER BY finished_at DESC LIMIT 3
                    """,
                    (deployment.deployment_id,),
                )
                if len(routes) < 3 or not all(
                    row["outcome"] == "failed" for row in routes
                ):
                    continue
                latest = routes[0]
                if datetime.now(UTC) - datetime.fromisoformat(
                    latest["finished_at"]
                ) < timedelta(seconds=self.runner.settings.confirmation_delay_seconds):
                    continue
                already = await self.database.scalar(
                    "SELECT COUNT(*) FROM probe_runs WHERE confirmation_of=?",
                    (latest["id"],),
                )
                if not already:
                    await self.runner.generation(
                        deployment,
                        ProbeKind.CONFIRMATION,
                        confirmation_of=int(latest["id"]),
                    )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=30)
