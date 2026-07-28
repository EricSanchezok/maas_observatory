from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tooluse_bench.domain import ModelDeployment


@dataclass(frozen=True)
class ProbeCase:
    name: str
    prompt: str
    tools: list[dict[str, Any]]
    check: Callable[[dict[str, Any]], tuple[bool, str]]


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


ADD_TOOL = _tool(
    "add",
    "Add two numbers.",
    {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
)

WEATHER_TOOL = _tool(
    "get_weather",
    "Get the current weather for a city.",
    {
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        },
        "required": ["location", "unit"],
        "additionalProperties": False,
    },
)

EXCHANGE_TOOL = _tool(
    "get_exchange_rate",
    "Get the current exchange rate between two ISO 4217 currencies.",
    {
        "type": "object",
        "properties": {
            "base": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["base", "quote"],
        "additionalProperties": False,
    },
)

BOOK_FLIGHT_TOOL = _tool(
    "book_flight",
    "Book a flight after all required details are known.",
    {
        "type": "object",
        "properties": {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "date": {"type": "string", "description": "ISO 8601 date"},
        },
        "required": ["origin", "destination", "date"],
        "additionalProperties": False,
    },
)


def _calls(message: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    parsed: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                return [], f"arguments are not valid JSON: {exc}"
        if not isinstance(arguments, dict):
            return [], "arguments must decode to an object"
        parsed.append({"name": function.get("name"), "arguments": arguments})
    return parsed, None


def _check_exact_add(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    expected = {"name": "add", "arguments": {"x": 17, "y": 25}}
    return (calls == [expected], f"expected {expected!r}, got {calls!r}")


def _check_irrelevant(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    content = message.get("content")
    passed = not calls and isinstance(content, str) and bool(content.strip())
    return passed, f"expected a text answer without tool calls, got {calls!r}"


def _check_selection(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    passed = (
        len(calls) == 1
        and calls[0]["name"] == "get_exchange_rate"
        and str(calls[0]["arguments"].get("base", "")).upper() == "USD"
        and str(calls[0]["arguments"].get("quote", "")).upper() == "CNY"
    )
    return passed, f"expected USD/CNY get_exchange_rate, got {calls!r}"


def _check_parallel(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    locations = {
        str(call["arguments"].get("location", "")).lower()
        for call in calls
        if call["name"] == "get_weather" and call["arguments"].get("unit") == "celsius"
    }
    has_shanghai = any(
        "shanghai" in location or "上海" in location for location in locations
    )
    has_beijing = any(
        "beijing" in location or "北京" in location for location in locations
    )
    passed = len(calls) == 2 and has_shanghai and has_beijing
    return (
        passed,
        f"expected two Celsius weather calls for Shanghai and Beijing, got {calls!r}",
    )


def _check_missing_info(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    content = message.get("content")
    passed = not calls and isinstance(content, str) and bool(content.strip())
    return (
        passed,
        f"expected a clarification question without a tool call, got {calls!r}",
    )


CASES = [
    ProbeCase(
        name="exact_arguments",
        prompt="Use the available tool to add 17 and 25.",
        tools=[ADD_TOOL],
        check=_check_exact_add,
    ),
    ProbeCase(
        name="irrelevance",
        prompt="In two short sentences, explain why leaves look green.",
        tools=[WEATHER_TOOL],
        check=_check_irrelevant,
    ),
    ProbeCase(
        name="tool_selection",
        prompt="What is the current USD to CNY exchange rate? Use a tool.",
        tools=[WEATHER_TOOL, ADD_TOOL, EXCHANGE_TOOL],
        check=_check_selection,
    ),
    ProbeCase(
        name="parallel_calls",
        prompt=(
            "Use the tools to get the current weather in both Shanghai and Beijing "
            "in Celsius. Make both independent calls now."
        ),
        tools=[WEATHER_TOOL],
        check=_check_parallel,
    ),
    ProbeCase(
        name="missing_required_information",
        prompt="Please book me a flight to Shanghai tomorrow.",
        tools=[BOOK_FLIGHT_TOOL],
        check=_check_missing_info,
    ),
]


def _post_chat_completion(
    model: ModelDeployment,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    assert model.base_url is not None
    assert model.api_key is not None
    url = f"{model.base_url.rstrip('/')}/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {model.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("response JSON must be an object")
            return payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        body = body.replace(model.api_key, "[REDACTED]")
        raise RuntimeError(f"HTTP {exc.code}: {body[:2000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


def run_case(
    model: ModelDeployment,
    case: ProbeCase,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    payload = {
        "model": model.model_id,
        "messages": [{"role": "user", "content": case.prompt}],
        "tools": case.tools,
        "tool_choice": "auto",
        "temperature": 0,
    }
    try:
        response = _post_chat_completion(
            model,
            payload,
            timeout_seconds or model.timeout_seconds or 600,
        )
        message = response["choices"][0]["message"]
        passed, detail = case.check(message)
        return {
            "model": model.alias,
            "model_id": model.model_id,
            "case": case.name,
            "status": "pass" if passed else "fail",
            "detail": detail,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "message": message,
            "usage": response.get("usage"),
        }
    except (KeyError, IndexError, TypeError, ValueError, RuntimeError) as exc:
        error = str(exc)
        if model.api_key:
            error = error.replace(model.api_key, "[REDACTED]")
        return {
            "model": model.alias,
            "model_id": model.model_id,
            "case": case.name,
            "status": "error",
            "detail": error,
            "latency_seconds": round(time.perf_counter() - started, 3),
        }
