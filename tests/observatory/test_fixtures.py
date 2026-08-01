"""Tests for deterministic synthetic Agent fixtures (v5)."""

from __future__ import annotations

from maas_observatory.fixtures import (
    FIXTURE_IDS,
    all_metadata,
    fixture_hashes,
    get_payload,
    tier_for,
)
from maas_observatory.models import ContextTier


def test_fixture_ids_are_six_fixed_and_sorted() -> None:
    assert FIXTURE_IDS == [
        "agent-1k-a",
        "agent-16k-a",
        "agent-64k-a",
        "agent-1k-b",
        "agent-16k-b",
        "agent-64k-b",
    ]


def test_fixture_hashes_are_stable() -> None:
    hashes = fixture_hashes()
    assert len(hashes) == 6
    assert all(len(v) == 64 for v in hashes.values())
    # Determinism: two calls produce identical results
    assert hashes == fixture_hashes()


def test_metadata_is_deterministic() -> None:
    md1 = all_metadata()
    md2 = all_metadata()
    assert md1 == md2
    for fid in FIXTURE_IDS:
        assert md1[fid]["fixture_id"] == fid
        assert md1[fid]["reference_tokenizer"] == "cl100k_base@tiktoken-0.13.0"
        assert isinstance(md1[fid]["target_input_tokens"], int)
        assert isinstance(md1[fid]["ref_prompt_tokens"], int)
        assert md1[fid]["ref_prompt_tokens"] > 0
        assert isinstance(md1[fid]["payload_bytes"], int)
        assert md1[fid]["payload_bytes"] > 0
        assert len(md1[fid]["sha256"]) == 64


def test_payload_structure_is_valid_agent_format() -> None:
    tool_counts = {"1k": 3, "16k": 4, "64k": 5}
    history_counts = {"1k": 1, "16k": 2, "64k": 3}
    for fid in FIXTURE_IDS:
        payload = get_payload(fid)
        tier = all_metadata()[fid]["context_tier"]
        msgs = payload["messages"]
        assert msgs[0]["role"] == "system"
        assert isinstance(msgs[0]["content"], str)
        assert len(msgs[0]["content"]) > 100
        assert msgs[1] == {
            "role": "user",
            "content": "Review the available operational evidence.",
        }
        assistant_history = [msg for msg in msgs if msg["role"] == "assistant"]
        tool_history = [msg for msg in msgs if msg["role"] == "tool"]
        assert len(assistant_history) == history_counts[tier]
        assert len(tool_history) == history_counts[tier]
        assert all(msg["content"] is None for msg in assistant_history)
        assert all(len(msg["tool_calls"]) == 1 for msg in assistant_history)
        assert all(isinstance(msg["content"], str) for msg in tool_history)
        assert msgs[-1]["role"] == "user"
        assert isinstance(msgs[-1]["content"], str)
        assert "Do not call a tool." in msgs[-1]["content"]
        assert isinstance(payload["tools"], list)
        assert len(payload["tools"]) == tool_counts[tier]
        assert payload["max_tokens"] == 16384
        assert payload["temperature"] == 0


def test_context_grows_by_tier_and_is_append_only() -> None:
    """The primary synthetic evidence only grows across tiers."""
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")

    for variant in ("a", "b"):
        contexts = [
            get_payload(f"agent-{tier}-{variant}")["messages"][3]["content"]
            for tier in ("1k", "16k", "64k")
        ]
        sizes = [len(enc.encode(context)) for context in contexts]
        assert sizes[0] < sizes[1] < sizes[2], f"variant {variant}: {sizes}"
        assert contexts[1].startswith(contexts[0])
        assert contexts[2].startswith(contexts[1])


def test_context_uses_mixed_evidence_formats() -> None:
    """Even the smallest tier contains varied, task-like evidence structures."""
    for variant in ("a", "b"):
        context = get_payload(f"agent-1k-{variant}")["messages"][3]["content"]
        assert "format=mixed" in context
        assert '"record_id":' in context
        assert "level=INFO" in context
        assert "```yaml" in context
        assert "Observation " in context
        assert "| record | region | service |" in context


def test_reference_prompt_calibration_within_2_percent() -> None:
    """Each nominal tier calibrates the complete Agent prompt, not one message."""
    meta = all_metadata()

    for fid in FIXTURE_IDS:
        md = meta[fid]
        target = md["target_input_tokens"]
        actual = md["ref_prompt_tokens"]
        delta = abs(actual - target) / target
        assert delta <= 0.02, (
            f"{fid}: actual={actual} target={target} delta={delta:.2%}"
        )


def test_ref_prompt_tokens_match_encoder_within_2_percent() -> None:
    """The stored ref_prompt_tokens matches actual total prompt tokens within ±2%."""
    import json

    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    meta = all_metadata()

    for fid in FIXTURE_IDS:
        md = meta[fid]
        payload = get_payload(fid)
        messages = payload["messages"]

        msg_tokens = 3
        for msg in messages:
            msg_tokens += 4
            for _key, value in msg.items():
                if isinstance(value, str):
                    msg_tokens += len(enc.encode(value))
                elif value is None:
                    continue
                elif isinstance(value, list):
                    msg_tokens += len(enc.encode(json.dumps(value, sort_keys=True)))

        tools_tokens = len(enc.encode(json.dumps(payload["tools"], sort_keys=True)))
        framework_tokens = 8
        actual_ref = msg_tokens + tools_tokens + framework_tokens
        delta = abs(actual_ref - md["ref_prompt_tokens"]) / actual_ref * 100
        assert delta < 2.0, (
            f"{fid}: ref={md['ref_prompt_tokens']} "
            f"actual={actual_ref} delta={delta:.2f}%"
        )


def test_tier_for_maps_correctly() -> None:
    assert tier_for("agent-1k-a") == ContextTier.T1K
    assert tier_for("agent-16k-a") == ContextTier.T16K
    assert tier_for("agent-64k-a") == ContextTier.T64K
    assert tier_for("agent-1k-b") == ContextTier.T1K
    assert tier_for("agent-16k-b") == ContextTier.T16K
    assert tier_for("agent-64k-b") == ContextTier.T64K


def test_variants_have_different_content() -> None:
    """A and B variants produce different payloads."""
    payload_a = get_payload("agent-1k-a")
    payload_b = get_payload("agent-1k-b")
    assert payload_a["messages"][0]["content"] != payload_b["messages"][0]["content"]
    assert payload_a["tools"] != payload_b["tools"]


def test_payload_does_not_contain_synergy_or_secrets() -> None:
    import json

    for fid in FIXTURE_IDS:
        payload = get_payload(fid)
        serialized = json.dumps(payload).lower()
        assert "synergy" not in serialized
        assert "secret" not in serialized
        assert "test-secret" not in serialized
