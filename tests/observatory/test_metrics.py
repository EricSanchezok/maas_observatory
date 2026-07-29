from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maas_observatory.metrics import (
    derive_interval,
    histogram_delta,
    histogram_quantile,
    merge_histograms,
    parse_vllm_metrics,
)
from maas_observatory.models import Histogram, MetricSnapshot, Quality


def prometheus_fixture(
    generation: int = 100,
    prompt: int = 200,
    requests: int = 20,
    *,
    bucket_count: int = 20,
) -> str:
    return f"""
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{{worker="0"}} {generation - 10}
vllm:generation_tokens_total{{worker="1"}} 10
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total {prompt}
# TYPE vllm:request_success_total counter
vllm:request_success_total{{finished_reason="stop"}} {requests}
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running 0
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting 0
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc 0.25
# TYPE vllm:request_decode_time_seconds histogram
vllm:request_decode_time_seconds_bucket{{le="0.1"}} {bucket_count // 2}
vllm:request_decode_time_seconds_bucket{{le="0.5"}} {bucket_count}
vllm:request_decode_time_seconds_bucket{{le="+Inf"}} {bucket_count}
vllm:request_decode_time_seconds_count {bucket_count}
vllm:request_decode_time_seconds_sum {bucket_count / 5}
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{{le="0.1"}} {bucket_count // 2}
vllm:time_to_first_token_seconds_bucket{{le="1"}} {bucket_count}
vllm:time_to_first_token_seconds_bucket{{le="+Inf"}} {bucket_count}
vllm:time_to_first_token_seconds_count {bucket_count}
vllm:time_to_first_token_seconds_sum {bucket_count / 2}
"""


def test_parser_merges_workers_and_keeps_allowlist() -> None:
    snapshot = parse_vllm_metrics(
        prometheus_fixture(), deployment_id="deployment", observed_at=datetime.now(UTC)
    )
    assert snapshot.counters["generation_tokens"] == 100
    assert snapshot.gauges["kv_cache_usage"] == 0.25
    assert snapshot.histograms["decode"].count == 20
    assert "process_cpu_seconds" not in snapshot.counters


def test_parser_rejects_non_vllm_payload() -> None:
    with pytest.raises(ValueError, match="no allowlisted"):
        parse_vllm_metrics(
            'python_info{version="3"} 1\n',
            deployment_id="deployment",
            observed_at=datetime.now(UTC),
        )


def test_interval_rates_histograms_and_low_sample_semantics() -> None:
    then = datetime.now(UTC)
    previous = parse_vllm_metrics(
        prometheus_fixture(100, 200, 20, bucket_count=20),
        deployment_id="deployment",
        observed_at=then,
    )
    current = parse_vllm_metrics(
        prometheus_fixture(140, 260, 25, bucket_count=25),
        deployment_id="deployment",
        observed_at=then + timedelta(seconds=20),
    )
    interval = derive_interval(current, previous, p95_min_samples=20)
    assert interval.quality == Quality.EXACT
    assert interval.values["system_output_tps"] == 2
    assert interval.values["prompt_tps"] == 3
    assert interval.values["aggregate_output_tps"] == 2
    assert "observed_decode_tps" not in interval.values
    assert interval.values["ttft_p50"] is not None
    assert interval.values["ttft_p95"] is None
    assert interval.sample_count == 5


def test_counter_and_histogram_resets_are_invalid() -> None:
    now = datetime.now(UTC)
    previous = MetricSnapshot(
        deployment_id="d",
        observed_at=now,
        counters={"generation_tokens": 10},
    )
    current = MetricSnapshot(
        deployment_id="d",
        observed_at=now + timedelta(seconds=1),
        counters={"generation_tokens": 9},
    )
    interval = derive_interval(current, previous, p95_min_samples=1)
    assert interval.reason == "counter_reset"
    assert (
        histogram_delta(
            Histogram(buckets={1.0: 2}, count=2, total=2),
            Histogram(buckets={1.0: 3}, count=3, total=3),
        )
        is None
    )


def test_histogram_merge_then_quantile() -> None:
    merged = merge_histograms(
        [
            Histogram(buckets={1.0: 5, 2.0: 10}, count=10, total=12),
            Histogram(buckets={1.0: 5, 2.0: 10}, count=10, total=12),
        ]
    )
    assert merged.count == 20
    assert histogram_quantile(merged, 0.5) == 1
    assert histogram_quantile(Histogram(), 0.95) is None


def test_non_positive_clock_interval_is_invalid() -> None:
    now = datetime.now(UTC)
    snapshot = MetricSnapshot(deployment_id="d", observed_at=now)
    interval = derive_interval(snapshot, snapshot, p95_min_samples=1)
    assert interval.quality == Quality.INVALID
    assert interval.reason == "non_positive_elapsed_time"
