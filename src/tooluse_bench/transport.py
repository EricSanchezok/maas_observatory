"""Auditable OpenAI-compatible HTTP transport with bounded retries."""

from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from tooluse_bench.domain import ModelDeployment
from tooluse_bench.records import ErrorCategory

RETRYABLE_STATUS_CODES = {429}


class _WallClockExpired(TimeoutError):
    """Internal exception raised by the POSIX request deadline."""


@contextmanager
def _wall_clock_deadline(seconds: float | None) -> Iterator[None]:
    if seconds is None:
        yield
        return
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not (
        threading.main_thread()
    ):
        raise RuntimeError("wall-clock request deadlines require a POSIX main thread")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("an existing process wall-clock timer is active")

    def expire(_: int, __: object) -> None:
        raise _WallClockExpired

    previous_handler = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@dataclass(frozen=True)
class TransportResponse:
    payload: dict[str, Any]
    attempts: int
    latency_seconds: float
    status_code: int


class TransportFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.attempts = attempts
        self.status_code = status_code


class OpenAITransport:
    """One deployment-scoped client.

    Authentication is injected only into HTTP headers and is never returned in
    request traces. Retries apply only to transport failures, 429, and 5xx.
    """

    def __init__(
        self,
        deployment: ModelDeployment,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        timeout_seconds: float | None = None,
        wall_timeout_seconds: float | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if deployment.configuration_errors():
            raise ValueError("; ".join(deployment.configuration_errors()))
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if wall_timeout_seconds is not None and wall_timeout_seconds <= 0:
            raise ValueError("wall_timeout_seconds must be positive")
        self.deployment = deployment
        self.max_retries = max_retries
        self.wall_timeout_seconds = wall_timeout_seconds
        self.sleeper = sleeper
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout_seconds or deployment.timeout_seconds or 600,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> OpenAITransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def chat_completion(self, payload: dict[str, Any]) -> TransportResponse:
        base_url = self.deployment.base_url
        api_key = self.deployment.api_key
        assert base_url is not None
        assert api_key is not None
        url = f"{base_url.rstrip('/')}/chat/completions"
        started = time.perf_counter()
        attempts = 0

        while True:
            attempts += 1
            try:
                with _wall_clock_deadline(self.wall_timeout_seconds):
                    response = self.client.post(
                        url,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    )
            except _WallClockExpired as exc:
                if attempts <= self.max_retries:
                    self._backoff(attempts)
                    continue
                raise TransportFailure(
                    "request exceeded wall-clock deadline",
                    category=ErrorCategory.TIMEOUT,
                    attempts=attempts,
                ) from exc
            except httpx.TimeoutException as exc:
                if attempts <= self.max_retries:
                    self._backoff(attempts)
                    continue
                raise TransportFailure(
                    "request timed out",
                    category=ErrorCategory.TIMEOUT,
                    attempts=attempts,
                ) from exc
            except httpx.TransportError as exc:
                if attempts <= self.max_retries:
                    self._backoff(attempts)
                    continue
                raise TransportFailure(
                    f"transport error: {type(exc).__name__}",
                    category=ErrorCategory.TRANSPORT,
                    attempts=attempts,
                ) from exc

            retryable = (
                response.status_code in RETRYABLE_STATUS_CODES
                or response.status_code >= 500
            )
            if retryable and attempts <= self.max_retries:
                self._backoff(attempts)
                continue
            if response.is_error:
                detail = self._safe_error_body(response, api_key, base_url)
                raise TransportFailure(
                    f"HTTP {response.status_code}: {detail}",
                    category=ErrorCategory.TRANSPORT,
                    attempts=attempts,
                    status_code=response.status_code,
                )
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                raise TransportFailure(
                    "response body is not valid JSON",
                    category=ErrorCategory.PROTOCOL,
                    attempts=attempts,
                    status_code=response.status_code,
                ) from exc
            if not isinstance(body, dict):
                raise TransportFailure(
                    "response JSON must be an object",
                    category=ErrorCategory.PROTOCOL,
                    attempts=attempts,
                    status_code=response.status_code,
                )
            return TransportResponse(
                payload=body,
                attempts=attempts,
                latency_seconds=round(time.perf_counter() - started, 6),
                status_code=response.status_code,
            )

    def _backoff(self, attempts: int) -> None:
        self.sleeper(min(0.5 * (2 ** (attempts - 1)), 4.0))

    @staticmethod
    def _safe_error_body(response: httpx.Response, *secrets: str) -> str:
        body = response.text[:2000]
        for secret in secrets:
            if secret:
                body = body.replace(secret, "[REDACTED]")
        return body
