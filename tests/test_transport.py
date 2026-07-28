from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest

from tooluse_bench.config import load_catalog
from tooluse_bench.records import ErrorCategory
from tooluse_bench.transport import OpenAITransport, TransportFailure


def configured_deployment() -> Iterator[object]:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    with patch.dict(os.environ, values, clear=False):
        yield deployment


def test_transport_retries_429_and_5xx_without_content_retry() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    statuses = iter((429, 503, 200))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = next(statuses)
        if status == 200:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )
        return httpx.Response(status, text="retry")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    with patch.dict(os.environ, values, clear=False):
        transport = OpenAITransport(
            deployment,
            client=client,
            sleeper=sleeps.append,
        )
        outcome = transport.chat_completion(
            {"model": deployment.model_id, "messages": []}
        )

    assert outcome.attempts == 3
    assert sleeps == [0.5, 1.0]
    assert len(requests) == 3
    assert requests[0].headers["authorization"] == "Bearer test-secret"


def test_transport_does_not_retry_invalid_success_json() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, content=b"not-json")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure) as captured,
    ):
        OpenAITransport(deployment, client=client).chat_completion({})
    assert captured.value.category is ErrorCategory.PROTOCOL
    assert captured.value.attempts == 1
    assert count == 1


def test_transport_redacts_secret_and_endpoint_from_http_error() -> None:
    deployment = load_catalog()[0]
    endpoint = "https://endpoint.invalid/v1"
    secret = "test-secret"
    values = {
        deployment.endpoint.base_url_env: endpoint,
        deployment.endpoint.api_key_env: secret,
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad {secret} at {endpoint}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure) as captured,
    ):
        OpenAITransport(deployment, client=client).chat_completion({})
    detail = str(captured.value)
    assert secret not in detail
    assert endpoint not in detail
    assert detail.count("[REDACTED]") == 2
