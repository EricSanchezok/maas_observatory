"""Deterministic transport-failure circuit breaker for BFCL subsets."""

from __future__ import annotations

from typing import Any

TRANSPORT_FAILURE_CATEGORIES = frozenset({"timeout", "transport"})


def transport_failure_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize endpoint-level failures without treating score failures as errors."""

    sample_count = len(records)
    transport_failure_count = sum(
        record.get("error_category") in TRANSPORT_FAILURE_CATEGORIES
        for record in records
    )
    fraction = transport_failure_count / sample_count if sample_count else 0.0
    return {
        "sample_count": sample_count,
        "transport_failure_count": transport_failure_count,
        "transport_failure_fraction": fraction,
    }


def should_open_transport_circuit(
    records: list[dict[str, Any]],
    *,
    minimum_samples: int,
    failure_fraction: float,
) -> tuple[bool, dict[str, Any]]:
    """Open only after a sufficiently large completed subset shows mass failure."""

    if minimum_samples < 1:
        raise ValueError("minimum_samples must be at least 1")
    if not 0 < failure_fraction <= 1:
        raise ValueError("failure_fraction must be in (0, 1]")
    summary = transport_failure_summary(records)
    should_open = (
        summary["sample_count"] >= minimum_samples
        and summary["transport_failure_fraction"] >= failure_fraction
    )
    return should_open, summary
