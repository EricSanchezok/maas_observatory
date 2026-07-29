"""Prometheus/vLLM parsing and interval metric derivation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime

from prometheus_client.parser import text_string_to_metric_families

from maas_observatory.models import Histogram, IntervalMetrics, MetricSnapshot, Quality

COUNTERS = {
    "vllm:generation_tokens_total": "generation_tokens",
    "vllm:prompt_tokens_total": "prompt_tokens",
    "vllm:request_success_total": "request_success",
    "vllm:prefix_cache_hits_total": "prefix_cache_hits",
    "vllm:prefix_cache_queries_total": "prefix_cache_queries",
    "vllm:num_preemptions_total": "preemptions",
}

GAUGES = {
    "vllm:num_requests_running": "requests_running",
    "vllm:num_requests_waiting": "requests_waiting",
    "vllm:kv_cache_usage_perc": "kv_cache_usage",
}

HISTOGRAMS = {
    "vllm:time_to_first_token_seconds": "ttft",
    "vllm:inter_token_latency_seconds": "itl",
    "vllm:e2e_request_latency_seconds": "e2e",
    "vllm:request_queue_time_seconds": "queue",
    "vllm:request_prefill_time_seconds": "prefill",
    "vllm:request_decode_time_seconds": "decode",
    "vllm:request_generation_tokens": "output_tokens",
    "vllm:request_prompt_tokens": "input_tokens",
}


def _number(value: object) -> float:
    number = float(value)  # type: ignore[arg-type]
    if math.isnan(number):
        raise ValueError("NaN metric sample")
    return number


def parse_vllm_metrics(
    text: str,
    *,
    deployment_id: str,
    source_id: str = "legacy-primary",
    observed_at: datetime,
) -> MetricSnapshot:
    """Parse only the low-cardinality allowlist and merge worker label series."""

    counters = {name: 0.0 for name in COUNTERS.values()}
    gauges = {name: 0.0 for name in GAUGES.values()}
    hist_buckets: dict[str, dict[float, float]] = {
        name: {} for name in HISTOGRAMS.values()
    }
    hist_counts = {name: 0.0 for name in HISTOGRAMS.values()}
    hist_sums = {name: 0.0 for name in HISTOGRAMS.values()}
    seen_counters: set[str] = set()
    seen_gauges: set[str] = set()
    seen_histograms: set[str] = set()

    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            sample_name = sample.name
            if sample_name in COUNTERS:
                key = COUNTERS[sample_name]
                counters[key] += _number(sample.value)
                seen_counters.add(key)
                continue
            if sample_name in GAUGES:
                key = GAUGES[sample_name]
                gauges[key] += _number(sample.value)
                seen_gauges.add(key)
                continue
            for prometheus_name, key in HISTOGRAMS.items():
                if sample_name == f"{prometheus_name}_bucket":
                    upper = _number(sample.labels.get("le", "+Inf"))
                    hist_buckets[key][upper] = hist_buckets[key].get(
                        upper, 0.0
                    ) + _number(sample.value)
                    seen_histograms.add(key)
                    break
                if sample_name == f"{prometheus_name}_count":
                    hist_counts[key] += _number(sample.value)
                    seen_histograms.add(key)
                    break
                if sample_name == f"{prometheus_name}_sum":
                    hist_sums[key] += _number(sample.value)
                    seen_histograms.add(key)
                    break

    if not (seen_counters or seen_gauges or seen_histograms):
        raise ValueError("response contains no allowlisted vLLM metrics")
    histograms = {
        name: Histogram(
            buckets=hist_buckets[name],
            count=hist_counts[name],
            total=hist_sums[name],
        )
        for name in seen_histograms
    }
    return MetricSnapshot(
        deployment_id=deployment_id,
        source_id=source_id,
        observed_at=observed_at,
        counters={key: counters[key] for key in seen_counters},
        gauges={key: gauges[key] for key in seen_gauges},
        histograms=histograms,
    )


def histogram_delta(current: Histogram, previous: Histogram) -> Histogram | None:
    keys = set(current.buckets) | set(previous.buckets)
    buckets = {
        bound: current.buckets.get(bound, 0) - previous.buckets.get(bound, 0)
        for bound in keys
    }
    count = current.count - previous.count
    total = current.total - previous.total
    if count < 0 or total < -1e-9 or any(value < 0 for value in buckets.values()):
        return None
    return Histogram(buckets=buckets, count=count, total=max(total, 0))


def histogram_quantile(histogram: Histogram, quantile: float) -> float | None:
    if histogram.count <= 0 or not histogram.buckets:
        return None
    target = histogram.count * quantile
    previous_bound = 0.0
    previous_count = 0.0
    for bound, cumulative in sorted(histogram.buckets.items()):
        if cumulative >= target:
            if math.isinf(bound):
                return previous_bound if previous_bound > 0 else None
            bucket_count = cumulative - previous_count
            if bucket_count <= 0:
                return bound
            fraction = (target - previous_count) / bucket_count
            return previous_bound + (bound - previous_bound) * fraction
        previous_bound = bound
        previous_count = cumulative
    return None


def derive_interval(
    current: MetricSnapshot,
    previous: MetricSnapshot,
    *,
    p95_min_samples: int,
) -> IntervalMetrics:
    if (
        current.deployment_id != previous.deployment_id
        or current.source_id != previous.source_id
    ):
        raise ValueError("cannot derive metrics across deployments or sources")
    elapsed = (current.observed_at - previous.observed_at).total_seconds()
    if elapsed <= 0:
        return IntervalMetrics(
            deployment_id=current.deployment_id,
            source_id=current.source_id,
            started_at=previous.observed_at,
            ended_at=current.observed_at,
            values={},
            quality=Quality.INVALID,
            reason="non_positive_elapsed_time",
        )

    counter_keys = set(current.counters) & set(previous.counters)
    deltas = {
        key: current.counters[key] - previous.counters[key] for key in counter_keys
    }
    if any(value < 0 for value in deltas.values()):
        return IntervalMetrics(
            deployment_id=current.deployment_id,
            source_id=current.source_id,
            started_at=previous.observed_at,
            ended_at=current.observed_at,
            values={},
            quality=Quality.INVALID,
            reason="counter_reset",
        )

    histogram_deltas: dict[str, Histogram] = {}
    for name in set(current.histograms) & set(previous.histograms):
        delta = histogram_delta(current.histograms[name], previous.histograms[name])
        if delta is None:
            return IntervalMetrics(
                deployment_id=current.deployment_id,
                source_id=current.source_id,
                started_at=previous.observed_at,
                ended_at=current.observed_at,
                values={},
                quality=Quality.INVALID,
                reason=f"histogram_reset:{name}",
            )
        histogram_deltas[name] = delta

    generation = deltas.get("generation_tokens")
    prompt = deltas.get("prompt_tokens")
    successes = deltas.get("request_success", 0)
    values: dict[str, float | None] = {
        "aggregate_output_tps": (
            generation / elapsed if generation is not None else None
        ),
        "system_output_tps": generation / elapsed if generation is not None else None,
        "prompt_tps": prompt / elapsed if prompt is not None else None,
        "request_success_delta": successes,
        "requests_running": current.gauges.get("requests_running"),
        "requests_waiting": current.gauges.get("requests_waiting"),
        "kv_cache_usage": current.gauges.get("kv_cache_usage"),
        "preemptions_delta": deltas.get("preemptions"),
    }
    for name, histogram in histogram_deltas.items():
        values[f"{name}_p50"] = histogram_quantile(histogram, 0.50)
        values[f"{name}_p95"] = (
            histogram_quantile(histogram, 0.95)
            if histogram.count >= p95_min_samples
            else None
        )
    return IntervalMetrics(
        deployment_id=current.deployment_id,
        source_id=current.source_id,
        started_at=previous.observed_at,
        ended_at=current.observed_at,
        values=values,
        histograms=histogram_deltas,
        sample_count=int(successes),
        quality=Quality.EXACT,
    )


def merge_histograms(histograms: Iterable[Histogram]) -> Histogram:
    buckets: dict[float, float] = {}
    count = 0.0
    total = 0.0
    for histogram in histograms:
        count += histogram.count
        total += histogram.total
        for bound, value in histogram.buckets.items():
            buckets[bound] = buckets.get(bound, 0) + value
    return Histogram(buckets=buckets, count=count, total=total)
