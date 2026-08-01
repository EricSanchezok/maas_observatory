"""Generate the checked-in MaaS Observatory Agent fixture resource."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/maas_observatory/resources/agent_fixtures_v5.json"
ENCODING = tiktoken.get_encoding("cl100k_base")
FIXTURE_ORDER = (
    "agent-1k-a",
    "agent-16k-a",
    "agent-64k-a",
    "agent-1k-b",
    "agent-16k-b",
    "agent-64k-b",
)
TARGETS = {"1k": 1_000, "16k": 16_000, "64k": 64_000}
REFERENCE_TOKENIZER = "cl100k_base@tiktoken-0.13.0"

SYSTEMS = {
    "a": (
        "You are a precise research assistant. Review the synthetic reference data "
        "already present in the conversation before answering. The lookup_reference "
        "tool is available only when required information is absent. The final request "
        "is fully answerable from the supplied context, so answer directly without "
        "calling another tool. Use two or three concise plain-text sentences."
    ),
    "b": (
        "You are a careful operations analyst. Review the synthetic metrics already "
        "present in the conversation before answering. The query_metrics tool is "
        "available only when required information is absent. The final request is "
        "fully answerable from the supplied context, so answer directly without "
        "calling another tool. Use two or three concise plain-text sentences."
    ),
}
TOOLS = {
    "a": [
        {
            "type": "function",
            "function": {
                "name": "lookup_reference",
                "description": "Look up one synthetic reference entry by topic.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_incident_logs",
                "description": (
                    "Search synthetic incident logs for a service and region."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "region": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["service", "region"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_service_config",
                "description": "Read one synthetic service configuration snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "revision": {"type": "string"},
                    },
                    "required": ["service"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "summarize_timeseries",
                "description": (
                    "Summarize a synthetic metric series over a fixed window."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "window_hours": {"type": "integer"},
                    },
                    "required": ["metric", "window_hours"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_capacity_plan",
                "description": "Read synthetic capacity assumptions for one region.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "quarter": {"type": "string"},
                    },
                    "required": ["region", "quarter"],
                },
            },
        },
    ],
    "b": [
        {
            "type": "function",
            "function": {
                "name": "query_metrics",
                "description": "Query one synthetic operational metric series.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "window_hours": {"type": "integer"},
                    },
                    "required": ["metric"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_change_event",
                "description": "Read a synthetic deployment change event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "since": {"type": "string"},
                    },
                    "required": ["service", "since"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_queue_state",
                "description": "Read a synthetic scheduler queue snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "queue": {"type": "string"},
                    },
                    "required": ["region", "queue"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_deployment_config",
                "description": "Read a synthetic deployment configuration snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "environment": {"type": "string"},
                    },
                    "required": ["service", "environment"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_alert_policy",
                "description": "Read a synthetic alert policy and threshold set.",
                "parameters": {
                    "type": "object",
                    "properties": {"policy": {"type": "string"}},
                    "required": ["policy"],
                },
            },
        },
    ],
}
TOOL_CALLS = {
    "a": [
        {
            "id": "call_reference_a",
            "type": "function",
            "function": {
                "name": "lookup_reference",
                "arguments": '{"query":"operational latency thresholds"}',
            },
        },
        {
            "id": "call_incidents_a",
            "type": "function",
            "function": {
                "name": "search_incident_logs",
                "arguments": '{"service":"inference","region":"central","limit":20}',
            },
        },
        {
            "id": "call_config_a",
            "type": "function",
            "function": {
                "name": "read_service_config",
                "arguments": '{"service":"inference","revision":"stable"}',
            },
        },
    ],
    "b": [
        {
            "id": "call_metrics_b",
            "type": "function",
            "function": {
                "name": "query_metrics",
                "arguments": '{"metric":"request_latency_p99","window_hours":24}',
            },
        },
        {
            "id": "call_change_b",
            "type": "function",
            "function": {
                "name": "lookup_change_event",
                "arguments": '{"service":"gateway","since":"2026-01-01T00:00:00Z"}',
            },
        },
        {
            "id": "call_queue_b",
            "type": "function",
            "function": {
                "name": "query_queue_state",
                "arguments": '{"region":"central","queue":"inference"}',
            },
        },
    ],
}
TIER_TOOL_COUNTS = {"1k": 3, "16k": 4, "64k": 5}
TIER_HISTORY_COUNTS = {"1k": 1, "16k": 2, "64k": 3}
SUPPLEMENTAL_RESULTS = {
    "a": [
        '{"incident_count":3,"dominant_signal":"queue_depth","status":"resolved"}',
        '{"batch_limit":24,"admission_control":"enabled","revision":"stable"}',
    ],
    "b": [
        '{"change":"batch-window-adjustment","status":"completed","risk":"low"}',
        '{"queued_requests":41,"oldest_age_seconds":3.8,"capacity_state":"constrained"}',
    ],
}
FINAL_USERS = {
    "a": (
        "Check {nonce}. Based only on the supplied references, state the main finding "
        "about operational latency and its likely cause. Do not call a tool."
    ),
    "b": (
        "Check {nonce}. Based only on the supplied metrics, state the observed trend "
        "and the most likely operational implication. Do not call a tool."
    ),
}


def count_text(text: str) -> int:
    return len(ENCODING.encode(text))


def count_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    tokens = 3
    for message in messages:
        tokens += 4
        for value in message.values():
            if isinstance(value, str):
                tokens += count_text(value)
            elif isinstance(value, list):
                tokens += count_text(json.dumps(value, sort_keys=True))
    tokens += count_text(json.dumps(tools, sort_keys=True))
    return tokens + 8


def record_stream(variant: str) -> str:
    regions = ("north", "south", "east", "west", "central")
    services = ("gateway", "scheduler", "inference", "cache", "telemetry")
    records: list[str] = []
    for index in range(2_400):
        region = regions[index % len(regions)]
        service = services[(index * 3 + (0 if variant == "a" else 2)) % len(services)]
        observed_at = (
            f"2026-{(index % 12) + 1:02d}-{(index % 28) + 1:02d}"
            f"T{index % 24:02d}:00:00Z"
        )
        record_id = f"{'ref' if variant == 'a' else 'metric'}-{index:05d}"
        latency_p99 = (
            119 + (index * 17) % 291 if variant == "a" else 132 + (index * 29) % 318
        )
        throughput = (
            820 + (index * 37) % 1_900 if variant == "a" else 760 + (index * 31) % 2_400
        )
        queue_depth = 3 + (index * 11) % 91
        if variant == "a":
            assessment = "queue-bound" if index % 3 else "network-sensitive"
        else:
            assessment = "rising" if index % 4 in (0, 1) else "stable"

        format_index = index % 5
        if format_index == 0:
            record = {
                "record_id": record_id,
                "observed_at": observed_at,
                "region": region,
                "service": service,
                "latency_p99_ms": latency_p99,
                "throughput_rps": throughput,
                "queue_depth": queue_depth,
                "assessment": assessment,
            }
            rendered = json.dumps(record, sort_keys=True, separators=(",", ":"))
        elif format_index == 1:
            rendered = (
                f"{observed_at} level=INFO source=synthetic-observer "
                f"record_id={record_id} region={region} service={service} "
                f"latency_p99_ms={latency_p99} throughput_rps={throughput} "
                f"queue_depth={queue_depth} assessment={assessment}"
            )
        elif format_index == 2:
            rendered = (
                "```yaml\n"
                f"record_id: {record_id}\n"
                f"observed_at: {observed_at}\n"
                f"scope: {{region: {region}, service: {service}}}\n"
                f"signals: {{latency_p99_ms: {latency_p99}, "
                f"throughput_rps: {throughput}, queue_depth: {queue_depth}}}\n"
                f"assessment: {assessment}\n"
                "```"
            )
        elif format_index == 3:
            rendered = (
                f"Observation {record_id}: the {service} service in {region} handled "
                f"{throughput} requests per second while p99 latency reached "
                f"{latency_p99} ms. Queue depth was {queue_depth}; the synthetic "
                f"assessment is {assessment}."
            )
        else:
            rendered = (
                "| record | region | service | p99 ms | throughput | queue | "
                "assessment |\n"
                "|---|---|---|---:|---:|---:|---|\n"
                f"| {record_id} | {region} | {service} | {latency_p99} | "
                f"{throughput} | {queue_depth} | {assessment} |"
            )
        records.append(rendered)
    return (
        "SYNTHETIC_AGENT_CONTEXT format=mixed variant="
        + variant
        + "\nThe evidence alternates JSON, logs, YAML, narrative, and tables.\n"
        + "\n\n".join(records)
    )


def tools_for(variant: str, tier: str) -> list[dict[str, Any]]:
    return TOOLS[variant][: TIER_TOOL_COUNTS[tier]]


def build_messages(variant: str, tier: str, context: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEMS[variant]},
        {"role": "user", "content": "Review the available operational evidence."},
    ]
    history_count = TIER_HISTORY_COUNTS[tier]
    for index, tool_call in enumerate(TOOL_CALLS[variant][:history_count]):
        tool_result = (
            context if index == 0 else SUPPLEMENTAL_RESULTS[variant][index - 1]
        )
        messages.extend(
            [
                {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": tool_result,
                },
            ]
        )
    messages.append({"role": "user", "content": FINAL_USERS[variant]})
    return messages


def build_resource() -> dict[str, Any]:
    fixtures: dict[str, Any] = {}
    for variant in ("a", "b"):
        full_context_tokens = ENCODING.encode(record_stream(variant))
        contexts: dict[str, str] = {}
        for tier in ("1k", "16k", "64k"):
            tools = tools_for(variant, tier)
            empty_messages = build_messages(variant, tier, "")
            base_tokens = count_prompt(empty_messages, tools)
            context_budget = TARGETS[tier] - base_tokens
            if context_budget <= 0 or context_budget > len(full_context_tokens):
                raise RuntimeError(f"invalid context budget for {tier}-{variant}")
            context = ENCODING.decode(full_context_tokens[:context_budget])
            if contexts and not context.startswith(next(reversed(contexts.values()))):
                raise RuntimeError(
                    f"tier context is not append-only for {tier}-{variant}"
                )
            contexts[tier] = context

            fixture_id = f"agent-{tier}-{variant}"
            messages = build_messages(variant, tier, context)
            ref_prompt_tokens = count_prompt(messages, tools)
            payload = {
                "model": "PLACEHOLDER",
                "messages": messages,
                "tools": tools,
                "max_tokens": 16_384,
                "stream": True,
                "stream_options": {"include_usage": True},
                "temperature": 0,
            }
            canonical = json.dumps(
                {
                    "messages": messages,
                    "tools": tools,
                    "max_tokens": 16_384,
                    "temperature": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            fixtures[fixture_id] = {
                "payload": payload,
                "metadata": {
                    "fixture_id": fixture_id,
                    "variant": variant,
                    "context_tier": tier,
                    "target_input_tokens": TARGETS[tier],
                    "ref_prompt_tokens": ref_prompt_tokens,
                    "payload_bytes": len(
                        json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ),
                    "context_bytes": len(context.encode()),
                    "reference_tokenizer": REFERENCE_TOKENIZER,
                    "sha256": hashlib.sha256(canonical).hexdigest(),
                },
            }
            delta = abs(ref_prompt_tokens - TARGETS[tier]) / TARGETS[tier]
            if delta > 0.02:
                raise RuntimeError(
                    f"{fixture_id} calibration delta {delta:.2%} exceeds 2%"
                )

    if tuple(fixtures) != FIXTURE_ORDER:
        ordered = {fixture_id: fixtures[fixture_id] for fixture_id in FIXTURE_ORDER}
        fixtures = ordered
    return {
        "resource_version": 1,
        "suite_version": "response-suite-v5",
        "reference_tokenizer": REFERENCE_TOKENIZER,
        "fixture_order": list(FIXTURE_ORDER),
        "fixtures": fixtures,
    }


def rendered_resource() -> bytes:
    return (
        json.dumps(build_resource(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = rendered_resource()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != rendered:
            raise SystemExit("fixture resource is stale")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(rendered)


if __name__ == "__main__":
    main()
