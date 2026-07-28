from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def test_transport_rejects_invalid_retry_and_timeout_configuration() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    with patch.dict(os.environ, values, clear=False):
        with pytest.raises(ValueError, match="non-negative"):
            OpenAITransport(deployment, max_retries=-1)
        with pytest.raises(ValueError, match="positive"):
            OpenAITransport(deployment, timeout_seconds=0)
        with pytest.raises(ValueError, match="positive"):
            OpenAITransport(deployment, wall_timeout_seconds=0)


def test_transport_enforces_wall_clock_deadline() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }

    def slow_handler(_: httpx.Request) -> httpx.Response:
        time.sleep(1)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(slow_handler))
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure) as captured,
    ):
        OpenAITransport(
            deployment,
            client=client,
            max_retries=0,
            wall_timeout_seconds=0.02,
        ).chat_completion({})
    assert captured.value.category is ErrorCategory.TIMEOUT
    assert "wall-clock" in str(captured.value)


def test_transport_wall_worker_returns_response_and_transport_error() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }

    success_client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
    )
    with patch.dict(os.environ, values, clear=False):
        response = OpenAITransport(
            deployment,
            client=success_client,
            wall_timeout_seconds=2,
        ).chat_completion({})
    assert response.payload == {"ok": True}

    def failed_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic", request=request)

    failed_client = httpx.Client(transport=httpx.MockTransport(failed_handler))
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure) as captured,
    ):
        OpenAITransport(
            deployment,
            client=failed_client,
            max_retries=0,
            wall_timeout_seconds=2,
        ).chat_completion({})
    assert captured.value.category is ErrorCategory.TRANSPORT
    assert "ConnectError" in str(captured.value)


@pytest.mark.parametrize(
    ("error_type", "category"),
    [
        (httpx.ReadTimeout, ErrorCategory.TIMEOUT),
        (httpx.ConnectError, ErrorCategory.TRANSPORT),
    ],
)
def test_transport_classifies_terminal_client_exceptions(
    error_type: type[httpx.TransportError],
    category: ErrorCategory,
) -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }

    def failed_handler(request: httpx.Request) -> httpx.Response:
        raise error_type("synthetic", request=request)

    client = httpx.Client(transport=httpx.MockTransport(failed_handler))
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure) as captured,
    ):
        OpenAITransport(
            deployment,
            client=client,
            max_retries=0,
        ).chat_completion({})
    assert captured.value.category is category
    assert captured.value.attempts == 1


def test_transport_rejects_non_object_json_and_closes_owned_client() -> None:
    deployment = load_catalog()[0]
    values = {
        deployment.endpoint.base_url_env: "https://endpoint.invalid/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    )
    with (
        patch.dict(os.environ, values, clear=False),
        pytest.raises(TransportFailure, match="must be an object"),
    ):
        OpenAITransport(deployment, client=client).chat_completion({})

    with (
        patch.dict(os.environ, values, clear=False),
        patch("tooluse_bench.transport.httpx.Client") as client_type,
        OpenAITransport(deployment),
    ):
        pass
    client_type.return_value.close.assert_called_once_with()


def test_spawned_wall_worker_can_reach_a_real_http_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("content-length", "0"))
            self.rfile.read(content_length)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    deployment = load_catalog()[0]
    host, port = server.server_address
    values = {
        deployment.endpoint.base_url_env: f"http://{host}:{port}/v1",
        deployment.endpoint.api_key_env: "test-secret",
    }
    try:
        with (
            patch.dict(os.environ, values, clear=False),
            OpenAITransport(
                deployment,
                timeout_seconds=2,
                wall_timeout_seconds=5,
            ) as transport,
        ):
            response = transport.chat_completion({})
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
    assert response.payload == {"ok": True}
