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
from maas_observatory.models import ErrorClass, ProbeKind, ProbeOutcome, ProbeResult
from maas_observatory.settings import (
    CollectionMode,
    ExperienceSettings,
    ProbeSettings,
)

CANARY_PROMPT = "Return exactly the lowercase word ok."

SHORT_TEMPLATES = (
    (
        "response-01",
        "Check {nonce}. Explain in plain language why a monitoring request should "
        "be small and repeatable. Use complete sentences and no lists.",
    ),
    (
        "response-02",
        "Check {nonce}. Describe how queueing can change the time a person waits "
        "for a model response. Use concise plain text and no headings.",
    ),
    (
        "response-03",
        "Check {nonce}. Explain why response speed and model quality are different "
        "measurements. Use complete sentences and no lists.",
    ),
)

LONG_SEEDS = (
    (
        "response-04",
        "A scheduled measurement records request timing, output events, and reported "
        "token usage without retaining content. ",
    ),
    (
        "response-05",
        "A reproducible response check uses fixed request shapes, transparent "
        "profiles, and one observer location. ",
    ),
    (
        "response-06",
        "A low-concurrency monitor separates transport failures, service failures, "
        "and measurement limitations. ",
    ),
)


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


def _long_prompt(seed: str, nonce: str) -> str:
    prefix = f"Check {nonce}. "
    suffix = (
        "\n\nSummarize the main operational trade-offs in concise plain text. "
        "Do not use headings, lists, quotations, or tools."
    )
    size = 16 * 1024
    body_size = size - len(prefix.encode()) - len(suffix.encode())
    repeated = (seed * ((body_size // len(seed.encode())) + 2)).encode()[:body_size]
    prompt = prefix.encode() + repeated + suffix.encode()
    return prompt.decode("utf-8", errors="ignore")


def fixture_prompt(kind: ProbeKind, fixture_index: int, nonce: str) -> tuple[str, str]:
    if kind == ProbeKind.EXPERIENCE_SHORT:
        fixture_id, template = SHORT_TEMPLATES[fixture_index % len(SHORT_TEMPLATES)]
        return fixture_id, template.format(nonce=nonce)
    if kind == ProbeKind.EXPERIENCE_CONTEXT:
        fixture_id, seed = LONG_SEEDS[fixture_index % len(LONG_SEEDS)]
        return fixture_id, _long_prompt(seed, nonce)
    return "canary", CANARY_PROMPT


def fixture_hashes() -> dict[str, str]:
    hashes = {
        fixture_id: hashlib.sha256(template.encode()).hexdigest()
        for fixture_id, template in SHORT_TEMPLATES
    }
    hashes.update(
        {
            fixture_id: hashlib.sha256(
                _long_prompt(seed, "{nonce:016}").encode()
            ).hexdigest()
            for fixture_id, seed in LONG_SEEDS
        }
    )
    return hashes


def profile_definitions(
    settings: ExperienceSettings, probes: ProbeSettings
) -> list[dict[str, Any]]:
    hashes = fixture_hashes()
    return [
        {
            "profile_id": settings.response_profile_id,
            "definition_version": settings.definition_version,
            "suite_version": settings.suite_version,
            "kind": "balanced_response",
            "streaming": True,
            "temperature": 0,
            "fixtures": [
                {
                    "fixture_id": fixture_id,
                    "input_shape": "compact",
                    "configured_max_output_tokens": (probes.short_max_output_tokens),
                    "sha256": hashes[fixture_id],
                }
                for fixture_id, _ in SHORT_TEMPLATES
            ]
            + [
                {
                    "fixture_id": fixture_id,
                    "input_shape": "extended",
                    "configured_max_output_tokens": (probes.context_max_output_tokens),
                    "fixture_bytes": 16 * 1024,
                    "sha256": hashes[fixture_id],
                }
                for fixture_id, _ in LONG_SEEDS
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
    prompt: str,
    operational_profile: str,
    max_tokens: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": deployment.model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if deployment.capabilities.temperature:
        payload["temperature"] = 0
    payload.update(deployment.request_defaults)
    if operational_profile != "default-only":
        profile = deployment.profiles.get(operational_profile)
        if profile is None:
            raise ValueError(f"undefined profile {operational_profile}")
        payload.update(profile.request_overrides)
    return payload


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

    async def _standard_budget_available(
        self, deployment: ModelDeployment, kind: ProbeKind, max_tokens: int
    ) -> tuple[bool, str | None]:
        if self.collection_mode == "rapid":
            return True, None
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
                "output_tokens": 0,
            }
        )
        budget = self.settings.standard_budget
        if (
            int(usage["short_requests"]) + int(usage["context_requests"])
            >= budget.response_requests
        ):
            return False, "daily_response_budget"
        if int(usage["output_tokens"]) + max_tokens > budget.output_tokens:
            return False, "daily_output_token_budget"
        return True, None

    async def _reserve_standard_budget(
        self, deployment: ModelDeployment, kind: ProbeKind, max_tokens: int
    ) -> None:
        if self.collection_mode == "rapid":
            return
        short = int(kind == ProbeKind.EXPERIENCE_SHORT)
        context = int(kind == ProbeKind.EXPERIENCE_CONTEXT)
        await self.database.write(
            """
            INSERT INTO budget_usage(
                deployment_id, budget_date, short_requests,
                context_requests, output_tokens
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(deployment_id, budget_date) DO UPDATE SET
                short_requests=short_requests+excluded.short_requests,
                context_requests=context_requests+excluded.context_requests,
                output_tokens=output_tokens+excluded.output_tokens
            """,
            (
                deployment.deployment_id,
                datetime.now(UTC).date().isoformat(),
                short,
                context,
                max_tokens,
            ),
        )

    async def generation(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        *,
        fixture_id: str | None = None,
        prompt: str | None = None,
        block_id: str | None = None,
        scheduled_at: datetime | None = None,
        confirmation_of: int | None = None,
        force: bool = False,
    ) -> ProbeResult:
        if kind not in {
            ProbeKind.CANARY,
            ProbeKind.EXPERIENCE_SHORT,
            ProbeKind.EXPERIENCE_CONTEXT,
            ProbeKind.CONFIRMATION,
        }:
            raise ValueError("unsupported generation probe kind")
        scheduled = scheduled_at or datetime.now(UTC)
        profile_id: str | None = None
        max_tokens = self.settings.canary_max_output_tokens
        selected_prompt = prompt or CANARY_PROMPT
        if kind == ProbeKind.EXPERIENCE_SHORT:
            profile_id = self.experience.response_profile_id
            max_tokens = self.settings.short_max_output_tokens
        elif kind == ProbeKind.EXPERIENCE_CONTEXT:
            profile_id = self.experience.response_profile_id
            max_tokens = self.settings.context_max_output_tokens
        if not force and await self._maintenance(deployment.deployment_id):
            return await self._persist_skipped(
                deployment,
                kind,
                scheduled,
                profile_id,
                fixture_id,
                block_id,
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
                "profile_undefined",
                measurement=True,
            )
        async with self._inference_lock(deployment.deployment_id):
            allowed, reason = await self._standard_budget_available(
                deployment, kind, max_tokens
            )
            if not force and not allowed:
                return await self._persist_skipped(
                    deployment,
                    kind,
                    scheduled,
                    profile_id,
                    fixture_id,
                    block_id,
                    reason,
                )
            await self._reserve_standard_budget(deployment, kind, max_tokens)
            return await self._stream_generation(
                deployment,
                kind,
                profile_id=profile_id or operational_profile,
                operational_profile=operational_profile,
                prompt=selected_prompt,
                max_tokens=max_tokens,
                fixture_id=fixture_id,
                block_id=block_id,
                scheduled_at=scheduled,
                confirmation_of=confirmation_of,
            )

    async def _persist_skipped(
        self,
        deployment: ModelDeployment,
        kind: ProbeKind,
        scheduled: datetime,
        profile_id: str | None,
        fixture_id: str | None,
        block_id: str | None,
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
        max_tokens: int,
        fixture_id: str | None,
        block_id: str | None,
        scheduled_at: datetime,
        confirmation_of: int | None,
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
        finish_reason: str | None = None
        visible_seen = False
        output_seen = False
        outcome = ProbeOutcome.SUCCESS
        error_class = ErrorClass.NONE
        error_code: str | None = None
        reasoning_chars = 0
        try:
            if not deployment.base_url or not deployment.api_key:
                raise ProbeConfigurationError("deployment is not configured")
            base_url = deployment.base_url
            api_key = deployment.api_key
            payload = _request_payload(
                deployment,
                prompt=prompt,
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
                nonlocal completion_tokens, prompt_tokens, finish_reason
                nonlocal visible_seen, output_seen, reasoning_chars
                async with client.stream(
                    "POST",
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
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
                            content, reasoning = _output_from_delta(delta)
                            if content or reasoning:
                                now = monotonic()
                                first_event = first_event or now
                                last_event = now
                                event_times.append(now)
                                output_seen = True
                                reasoning_chars += len(reasoning)
                                if content:
                                    first_visible = first_visible or now
                                    visible_seen = True

            if self._client is not None and not self._owns_client:
                await consume(self._client)
            else:
                async with _observer_http_client(timeout) as client:
                    await consume(client)
            if not visible_seen:
                raise RuntimeError("empty_visible_output")
        except Exception as exc:
            outcome = ProbeOutcome.FAILED
            if isinstance(exc, RuntimeError) and str(exc) == "empty_visible_output":
                error_class, error_code = ErrorClass.SERVICE, "empty_visible_output"
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
        measurements: dict[str, float | int | str | None] = {
            "reported_completion_tokens": completion_tokens,
            "reported_prompt_tokens": prompt_tokens,
            "configured_output_tokens": max_tokens,
            "time_to_headers_seconds": (
                headers_clock - request_clock if headers_clock is not None else None
            ),
            "stream_start_seconds": (
                first_event - request_clock if first_event is not None else None
            ),
            "first_response_seconds": (
                first_visible - request_clock if first_visible is not None else None
            ),
            "output_speed_tps": output_speed,
            "stream_gap_p50_seconds": statistics.median(gaps) if gaps else None,
            "stream_gap_p95_seconds": (
                sorted(gaps)[max(0, int(len(gaps) * 0.95) - 1)] if gaps else None
            ),
            "stream_gap_max_seconds": max(gaps) if gaps else None,
            "reasoning_chars": reasoning_chars if reasoning_chars else None,
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
                confirmation_of, measurement_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(result.measurements, sort_keys=True),
            ),
        )
        units = {
            "route_latency_seconds": "s",
            "reported_completion_tokens": "tokens",
            "reported_prompt_tokens": "tokens",
            "configured_output_tokens": "tokens",
            "time_to_headers_seconds": "s",
            "stream_start_seconds": "s",
            "first_response_seconds": "s",
            "stream_gap_p50_seconds": "s",
            "stream_gap_p95_seconds": "s",
            "stream_gap_max_seconds": "s",
            "reasoning_chars": "chars",
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
    """Persisted block scheduling with per-deployment inference locks."""

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
        kind: ProbeKind,
        fixture_index: int,
        block_index: int,
        scheduled_at: datetime,
        stop: asyncio.Event | None = None,
    ) -> None:
        deployments = balanced_order(list(self.runner.catalog.deployments), block_index)
        if not deployments:
            return
        epoch = await self.database.current_epoch()
        nonce = block_nonce(epoch, block_index)
        fixture_id, prompt = fixture_prompt(kind, fixture_index, nonce)
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
                fixture_id,
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
                            kind,
                            fixture_id=fixture_id,
                            prompt=prompt,
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
            kind = (
                ProbeKind.EXPERIENCE_SHORT
                if block_index % 2 == 0
                else ProbeKind.EXPERIENCE_CONTEXT
            )
            fixture_index = (block_index // 2) % 3
            actual_start = datetime.now(UTC)
            await self.run_block(kind, fixture_index, block_index, next_at, stop)
            state["block_index"] = block_index + 1
            interval = (
                self.runner.settings.rapid_block_interval_seconds
                if self.runner.collection_mode == "rapid"
                else self.runner.settings.standard_block_interval_seconds
            )
            state["next_block_at"] = isoformat(
                max(
                    next_at + timedelta(seconds=interval),
                    actual_start + timedelta(seconds=interval),
                )
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
