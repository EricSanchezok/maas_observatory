"""Small native protocol benchmark for OpenAI tool-calling compatibility."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tooluse_bench.benchmarks.base import AdapterContext, BenchmarkAdapter
from tooluse_bench.domain import BenchmarkSelection, ModelDeployment
from tooluse_bench.records import (
    BenchmarkMetadata,
    ErrorCategory,
    TaskResult,
    TaskStatus,
    ValidationIssue,
    result_from_spec,
)
from tooluse_bench.transport import TransportFailure


@dataclass(frozen=True)
class ProbeCase:
    name: str
    prompt: str
    tools: list[dict[str, Any]]
    failure_category: ErrorCategory
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
        "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
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
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        return [], "message.tool_calls must be a list"
    for call in raw_calls:
        if not isinstance(call, dict):
            return [], "each tool call must be an object"
        function = call.get("function") or {}
        if not isinstance(function, dict):
            return [], "tool call function must be an object"
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
    return calls == [expected], f"expected {expected!r}, got {calls!r}"


def _check_irrelevant(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    content = message.get("content")
    passed = not calls and isinstance(content, str) and bool(content.strip())
    return passed, f"expected text without tool calls, got {calls!r}"


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
    return passed, f"expected two parallel weather calls, got {calls!r}"


def _check_missing_info(message: dict[str, Any]) -> tuple[bool, str]:
    calls, error = _calls(message)
    if error:
        return False, error
    content = message.get("content")
    normalized = content.lower() if isinstance(content, str) else ""
    asks_for_origin = any(
        marker in normalized
        for marker in (
            "origin",
            "departure",
            "departing",
            "depart from",
            "leaving from",
            "出发",
            "起飞",
        )
    )
    requests_information = any(
        marker in normalized
        for marker in (
            "?",
            "please provide",
            "need to know",
            "which",
            "what",
            "请提供",
            "需要知道",
        )
    )
    passed = not calls and asks_for_origin and requests_information
    return passed, f"expected a clarification without a tool call, got {calls!r}"


CASES = (
    ProbeCase(
        "exact_arguments",
        "Use the available tool to add 17 and 25.",
        [ADD_TOOL],
        ErrorCategory.ARGUMENTS,
        _check_exact_add,
    ),
    ProbeCase(
        "irrelevance",
        "In two short sentences, explain why leaves look green.",
        [WEATHER_TOOL],
        ErrorCategory.SELECTION,
        _check_irrelevant,
    ),
    ProbeCase(
        "tool_selection",
        "What is the current USD to CNY exchange rate? Use a tool.",
        [WEATHER_TOOL, ADD_TOOL, EXCHANGE_TOOL],
        ErrorCategory.SELECTION,
        _check_selection,
    ),
    ProbeCase(
        "parallel_calls",
        (
            "Use the tools to get the current weather in both Shanghai and "
            "Beijing in Celsius. Make both independent calls now."
        ),
        [WEATHER_TOOL],
        ErrorCategory.PLANNING,
        _check_parallel,
    ),
    ProbeCase(
        "missing_required_information",
        "Please book me a flight to Shanghai tomorrow.",
        [BOOK_FLIGHT_TOOL],
        ErrorCategory.POLICY,
        _check_missing_info,
    ),
)


class ProbeAdapter(BenchmarkAdapter):
    @property
    def metadata(self) -> BenchmarkMetadata:
        return BenchmarkMetadata(
            benchmark_id="probe",
            display_name="SII Native Tool-calling Protocol Probe",
            version="1.1.0",
            source_url="https://github.com/sii-holos/tool-use",
            revision="probe-v1.1",
            hermetic_default=True,
            supported_profiles=("full",),
        )

    def needs_native_transport(self) -> bool:
        return True

    def validate(
        self, selection: BenchmarkSelection, deployment: ModelDeployment
    ) -> tuple[ValidationIssue, ...]:
        issues = list(super().validate(selection, deployment))
        max_tokens = selection.options.get("max_tokens", 4096)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            issues.append(
                ValidationIssue(
                    level="error",
                    code="invalid_max_tokens",
                    message="probe max_tokens must be a positive integer",
                )
            )
        elif max_tokens <= 0 or max_tokens > deployment.output_limit:
            issues.append(
                ValidationIssue(
                    level="error",
                    code="invalid_max_tokens",
                    message=(
                        "probe max_tokens must be positive and no greater than "
                        "the deployment output limit"
                    ),
                )
            )
        return tuple(issues)

    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        if context.transport is None:
            raise RuntimeError("probe requires the native OpenAI transport")
        for case in CASES:
            started_at = datetime.now(UTC)
            payload = {
                **context.deployment.request_defaults,
                "model": context.deployment.model_id,
                "messages": [{"role": "user", "content": case.prompt}],
                "tools": case.tools,
                "tool_choice": "auto",
                "temperature": 0,
                "seed": context.spec.seed,
                "max_tokens": int(context.selection.options.get("max_tokens", 4096)),
            }
            response_payload: dict[str, Any] | None = None
            try:
                outcome = context.transport.chat_completion(payload)
                response_payload = outcome.payload
                message = outcome.payload["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise TypeError("choices[0].message must be an object")
                passed, detail = case.check(message)
                finished_at = datetime.now(UTC)
                yield result_from_spec(
                    context.spec,
                    task_id=case.name,
                    status=TaskStatus.PASS if passed else TaskStatus.FAIL,
                    score=1.0 if passed else 0.0,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_seconds=outcome.latency_seconds,
                    attempts=outcome.attempts,
                    request=payload,
                    response=outcome.payload,
                    usage=outcome.payload.get("usage"),
                    error_category=(
                        ErrorCategory.NONE if passed else case.failure_category
                    ),
                    error_detail=None if passed else detail,
                )
            except TransportFailure as exc:
                finished_at = datetime.now(UTC)
                yield result_from_spec(
                    context.spec,
                    task_id=case.name,
                    status=TaskStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_seconds=(finished_at - started_at).total_seconds(),
                    attempts=exc.attempts,
                    request=payload,
                    error_category=exc.category,
                    error_detail=str(exc),
                )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                finished_at = datetime.now(UTC)
                yield result_from_spec(
                    context.spec,
                    task_id=case.name,
                    status=TaskStatus.ERROR,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_seconds=(finished_at - started_at).total_seconds(),
                    request=payload,
                    response=response_payload,
                    error_category=ErrorCategory.PROTOCOL,
                    error_detail=f"{type(exc).__name__}: {exc}",
                )
