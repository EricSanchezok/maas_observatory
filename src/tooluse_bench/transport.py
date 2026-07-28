"""Auditable OpenAI-compatible HTTP transport with bounded retries."""

from __future__ import annotations

import json
import multiprocessing
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from tooluse_bench.domain import ModelDeployment
from tooluse_bench.records import ErrorCategory

RETRYABLE_STATUS_CODES = {429}


class _WallClockExpired(TimeoutError):
    """Internal exception raised when a request worker exceeds its deadline."""


def _post_worker(
    sender: Any,
    client: httpx.Client | None,
    timeout_seconds: float,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> None:
    owns_client = client is None
    worker_client = client or httpx.Client(
        timeout=timeout_seconds,
        trust_env=False,
    )
    try:
        response = worker_client.post(url, json=payload, headers=headers)
        sender.send(
            (
                "response",
                response.status_code,
                list(response.headers.multi_items()),
                response.content,
            )
        )
    except httpx.TimeoutException as exc:
        sender.send(("timeout", type(exc).__name__))
    except httpx.TransportError as exc:
        sender.send(("transport", type(exc).__name__))
    except BaseException as exc:
        sender.send(("unexpected", type(exc).__name__))
    finally:
        try:
            if owns_client:
                worker_client.close()
        finally:
            sender.close()


def _post_with_deadline(
    client: httpx.Client | None,
    timeout_seconds: float,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    seconds: float,
) -> httpx.Response:
    start_method = "spawn" if client is None else "fork"
    try:
        process_context: Any = multiprocessing.get_context(start_method)
    except ValueError as exc:
        raise RuntimeError(
            f"wall-clock request deadline requires {start_method!r} "
            "multiprocessing support"
        ) from exc
    receiver, sender = process_context.Pipe(duplex=False)
    worker = process_context.Process(
        target=_post_worker,
        args=(sender, client, timeout_seconds, url, payload, headers),
        daemon=True,
    )
    worker.start()
    sender.close()
    try:
        if not receiver.poll(seconds):
            worker.terminate()
            worker.join(timeout=5)
            if worker.is_alive():
                worker.kill()
                worker.join()
            raise _WallClockExpired
        try:
            outcome = receiver.recv()
        except EOFError as exc:
            raise RuntimeError("request worker exited without an outcome") from exc
    finally:
        receiver.close()
    worker.join(timeout=5)
    if worker.is_alive():
        worker.kill()
        worker.join()
        raise RuntimeError("request worker did not exit after returning an outcome")

    kind = outcome[0]
    if kind == "response":
        return httpx.Response(
            status_code=int(outcome[1]),
            headers=outcome[2],
            content=outcome[3],
        )
    if kind == "timeout":
        raise httpx.ReadTimeout(f"request worker: {outcome[1]}")
    if kind == "transport":
        raise httpx.TransportError(f"request worker: {outcome[1]}")
    raise RuntimeError(f"request worker failed: {outcome[1]}")


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
        self.timeout_seconds = timeout_seconds or deployment.timeout_seconds or 600
        self.client = client or httpx.Client(
            timeout=self.timeout_seconds,
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
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if self.wall_timeout_seconds is None:
                    response = self.client.post(
                        url,
                        json=payload,
                        headers=headers,
                    )
                else:
                    response = _post_with_deadline(
                        None if self._owns_client else self.client,
                        self.timeout_seconds,
                        url,
                        payload,
                        headers,
                        self.wall_timeout_seconds,
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
