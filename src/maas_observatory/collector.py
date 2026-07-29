"""Bounded, non-retrying vLLM telemetry collection and rollups."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx

from maas_common.catalog import ModelCatalog, ModelDeployment
from maas_observatory.database import Database, isoformat
from maas_observatory.metrics import (
    derive_interval,
    histogram_quantile,
    merge_histograms,
    parse_vllm_metrics,
)
from maas_observatory.models import (
    ErrorClass,
    Histogram,
    IntervalMetrics,
    MetricSnapshot,
    Quality,
)
from maas_observatory.settings import MetricsSourceSettings, ScrapeSettings


def metrics_url(base_url: str, metrics_path: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, metrics_path, "", ""))


def classify_transport_error(error: BaseException) -> tuple[ErrorClass, str]:
    if isinstance(error, httpx.TimeoutException):
        return ErrorClass.TRANSPORT, "timeout"
    if isinstance(error, httpx.ConnectError):
        detail = str(error).lower()
        if "certificate" in detail or "tls" in detail or "ssl" in detail:
            return ErrorClass.TRANSPORT, "tls"
        if "name" in detail or "dns" in detail:
            return ErrorClass.TRANSPORT, "dns"
        return ErrorClass.TRANSPORT, "connect"
    if isinstance(error, httpx.HTTPStatusError):
        return ErrorClass.SERVICE, f"http_{error.response.status_code}"
    if isinstance(error, (ValueError, UnicodeDecodeError)):
        return ErrorClass.MEASUREMENT, "parse"
    return ErrorClass.MEASUREMENT, "collector"


@dataclass(frozen=True)
class MetricsTarget:
    deployment: ModelDeployment
    source_id: str
    url: str | None
    api_key: str | None


class VLLMMetricsCollector:
    def __init__(
        self,
        catalog: ModelCatalog,
        settings: ScrapeSettings,
        database: Database,
        metrics_sources: dict[str, list[MetricsSourceSettings]] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.database = database
        self.targets: list[MetricsTarget] = []
        for deployment in catalog.deployments:
            configured = (metrics_sources or {}).get(deployment.alias)
            if configured:
                self.targets.extend(
                    MetricsTarget(
                        deployment=deployment,
                        source_id=source.source_id,
                        url=source.url,
                        api_key=source.api_key,
                    )
                    for source in configured
                )
            else:
                self.targets.append(
                    MetricsTarget(
                        deployment=deployment,
                        source_id="legacy-primary",
                        url=deployment.base_url,
                        api_key=deployment.api_key,
                    )
                )
        self._client = client
        self._owns_client = client is None
        self._previous: dict[tuple[str, str], MetricSnapshot] = {}
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def __aenter__(self) -> VLLMMetricsCollector:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.timeout_seconds),
                follow_redirects=False,
            )
        await self.restore_accumulators()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def restore_accumulators(self) -> None:
        rows = await self.database.query("SELECT * FROM metric_accumulators")
        for row in rows:
            key = (row["deployment_id"], row["source_id"])
            self._previous[key] = MetricSnapshot(
                deployment_id=row["deployment_id"],
                source_id=row["source_id"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                counters=json.loads(row["counters_json"]),
                histograms={
                    name: Histogram.model_validate(value)
                    for name, value in json.loads(row["histograms_json"]).items()
                },
            )

    async def fetch(
        self,
        deployment: ModelDeployment,
        source: MetricsTarget | None = None,
    ) -> MetricSnapshot:
        observed_at = datetime.now(UTC)
        target = source or next(
            item
            for item in self.targets
            if item.deployment.deployment_id == deployment.deployment_id
        )
        if target.url is None or target.api_key is None:
            return MetricSnapshot(
                deployment_id=deployment.deployment_id,
                source_id=target.source_id,
                observed_at=observed_at,
                quality=Quality.UNAVAILABLE,
                error_class=ErrorClass.MEASUREMENT,
                error_code="configuration",
            )
        assert self._client is not None
        try:
            async with (
                self._semaphore,
                self._client.stream(
                    "GET",
                    metrics_url(target.url, self.settings.metrics_path),
                    headers={"Authorization": f"Bearer {target.api_key}"},
                ) as response,
            ):
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.settings.max_response_bytes:
                        raise ValueError("metrics response exceeds size limit")
            return parse_vllm_metrics(
                content.decode("utf-8"),
                deployment_id=deployment.deployment_id,
                source_id=target.source_id,
                observed_at=observed_at,
            )
        except Exception as exc:
            error_class, code = classify_transport_error(exc)
            return MetricSnapshot(
                deployment_id=deployment.deployment_id,
                source_id=target.source_id,
                observed_at=observed_at,
                quality=Quality.UNAVAILABLE,
                error_class=error_class,
                error_code=code,
            )

    async def collect_one(
        self,
        deployment: ModelDeployment,
        source: MetricsTarget | None = None,
    ) -> MetricSnapshot:
        target = source or next(
            item
            for item in self.targets
            if item.deployment.deployment_id == deployment.deployment_id
        )
        snapshot = await self.fetch(deployment, target)
        key = (deployment.deployment_id, target.source_id)
        previous = self._previous.get(key)
        if previous is not None:
            snapshot = snapshot.model_copy(
                update={
                    "elapsed_seconds": (
                        snapshot.observed_at - previous.observed_at
                    ).total_seconds()
                }
            )
        interval = (
            derive_interval(
                snapshot,
                previous,
                p95_min_samples=self.settings.p95_min_samples,
            )
            if previous is not None and snapshot.quality == Quality.EXACT
            else None
        )
        await self._persist_snapshot(snapshot, interval)
        if snapshot.quality == Quality.EXACT:
            self._previous[key] = snapshot
            await self.database.write(
                """
                INSERT INTO metric_accumulators(
                    deployment_id, source_id, observed_at,
                    counters_json, histograms_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(deployment_id, source_id) DO UPDATE SET
                    observed_at=excluded.observed_at,
                    counters_json=excluded.counters_json,
                    histograms_json=excluded.histograms_json
                """,
                (
                    deployment.deployment_id,
                    target.source_id,
                    isoformat(snapshot.observed_at),
                    json.dumps(snapshot.counters, sort_keys=True),
                    json.dumps(
                        {
                            key: value.model_dump(mode="json")
                            for key, value in snapshot.histograms.items()
                        },
                        sort_keys=True,
                    ),
                ),
            )
        return snapshot

    async def _persist_snapshot(
        self, snapshot: MetricSnapshot, interval: IntervalMetrics | None
    ) -> None:
        interval_json = (
            json.dumps(interval.model_dump(mode="json"), sort_keys=True)
            if interval is not None
            else None
        )
        await self.database.write(
            """
            INSERT INTO scrape_snapshots(
                deployment_id, source_id, observed_at, elapsed_seconds, quality,
                error_class, error_code, counters_json, gauges_json,
                histograms_json, interval_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.deployment_id,
                snapshot.source_id,
                isoformat(snapshot.observed_at),
                snapshot.elapsed_seconds,
                snapshot.quality,
                snapshot.error_class,
                snapshot.error_code,
                json.dumps(snapshot.counters, sort_keys=True),
                json.dumps(snapshot.gauges, sort_keys=True),
                json.dumps(
                    {
                        key: value.model_dump(mode="json")
                        for key, value in snapshot.histograms.items()
                    },
                    sort_keys=True,
                ),
                interval_json,
            ),
        )

    async def collect_cycle(self) -> list[MetricSnapshot]:
        return list(
            await asyncio.gather(
                *(
                    self.collect_one(target.deployment, target)
                    for target in self.targets
                )
            )
        )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            await self.collect_cycle()
            remaining = self.settings.interval_seconds - (
                asyncio.get_running_loop().time() - started
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=max(remaining, 0.01))


def floor_bucket(value: datetime, seconds: int) -> datetime:
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)


class RollupEngine:
    def __init__(self, database: Database, *, p95_min_samples: int) -> None:
        self.database = database
        self.p95_min_samples = p95_min_samples

    async def aggregate(
        self, deployment_id: str, resolution: str, bucket_at: datetime
    ) -> bool:
        seconds = {"1m": 60, "5m": 300, "1h": 3600}[resolution]
        end = bucket_at + timedelta(seconds=seconds)
        rows = await self.database.query(
            """
            SELECT source_id, interval_json, quality, error_class FROM scrape_snapshots
            WHERE deployment_id=? AND observed_at>=? AND observed_at<?
              AND interval_json IS NOT NULL
            ORDER BY observed_at
            """,
            (deployment_id, isoformat(bucket_at), isoformat(end)),
        )
        intervals = [
            IntervalMetrics.model_validate(json.loads(row["interval_json"]))
            for row in rows
        ]
        valid = [item for item in intervals if item.quality == Quality.EXACT]
        if not valid:
            return False
        expected = int(
            await self.database.scalar(
                """
                SELECT COUNT(*) FROM metrics_sources
                WHERE deployment_id=? AND active=1
                """,
                (deployment_id,),
            )
            or len({item.source_id for item in valid})
        )
        observed_sources = {item.source_id for item in valid}
        quality = (
            Quality.EXACT
            if expected > 0 and len(observed_sources) == expected
            else Quality.INCOMPLETE
        )
        metric_names = set().union(*(item.values.keys() for item in valid))
        values: dict[str, float | None] = {}
        for name in metric_names:
            samples: list[tuple[float, float]] = []
            for item in valid:
                value = item.values.get(name)
                if value is not None:
                    duration = max((item.ended_at - item.started_at).total_seconds(), 0)
                    samples.append((float(value), duration))
            if not samples:
                values[name] = None
            elif name.endswith("_tps"):
                source_rates: list[float] = []
                for source_id in observed_sources:
                    source_samples: list[tuple[float, float]] = []
                    for item in valid:
                        if item.source_id != source_id:
                            continue
                        value = item.values.get(name)
                        duration = max(
                            (item.ended_at - item.started_at).total_seconds(), 0
                        )
                        if value is not None and duration > 0:
                            source_samples.append((float(value), duration))
                    if source_samples:
                        source_rates.append(
                            sum(value * duration for value, duration in source_samples)
                            / sum(duration for _, duration in source_samples)
                        )
                values[name] = sum(source_rates) if source_rates else None
            elif name in {"requests_running", "requests_waiting"}:
                latest_by_source: dict[str, float] = {}
                for item in valid:
                    value = item.values.get(name)
                    if value is not None:
                        latest_by_source[item.source_id] = float(value)
                values[name] = (
                    sum(latest_by_source.values()) if latest_by_source else None
                )
            elif name == "kv_cache_usage":
                present: list[float] = []
                for item in valid:
                    value = item.values.get(name)
                    if value is not None:
                        present.append(float(value))
                values[name] = max(present) if present else None
            else:
                values[name] = sum(value for value, _ in samples) / len(samples)
        histogram_names = set().union(*(item.histograms.keys() for item in valid))
        merged = {
            name: merge_histograms(
                item.histograms[name] for item in valid if name in item.histograms
            )
            for name in histogram_names
        }
        for name, histogram in merged.items():
            values[f"{name}_p50"] = histogram_quantile(histogram, 0.50)
            values[f"{name}_p95"] = (
                histogram_quantile(histogram, 0.95)
                if histogram.count >= self.p95_min_samples
                else None
            )
        sample_count = sum(item.sample_count for item in valid)
        payload = {
            "values": values,
            "sample_count": sample_count,
            "quality": quality,
            "expected_source_count": expected,
            "observed_source_count": len(observed_sources),
            "source_seconds_coverage": (
                len(observed_sources) / expected if expected else None
            ),
        }
        await self.database.write(
            """
            INSERT INTO rollups(
                deployment_id, resolution, bucket_at, payload_json,
                sample_count, quality, source_mix_json, histogram_delta_json
                , expected_source_count, observed_source_count,
                source_seconds_coverage, reset_count, transport_failure_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deployment_id, resolution, bucket_at) DO UPDATE SET
                payload_json=excluded.payload_json,
                sample_count=excluded.sample_count,
                quality=excluded.quality,
                source_mix_json=excluded.source_mix_json,
                histogram_delta_json=excluded.histogram_delta_json,
                expected_source_count=excluded.expected_source_count,
                observed_source_count=excluded.observed_source_count,
                source_seconds_coverage=excluded.source_seconds_coverage,
                reset_count=excluded.reset_count,
                transport_failure_count=excluded.transport_failure_count
            """,
            (
                deployment_id,
                resolution,
                isoformat(bucket_at),
                json.dumps(payload, sort_keys=True),
                sample_count,
                quality,
                json.dumps(
                    {
                        "passive_metrics": len(valid),
                        "expected_sources": expected,
                        "observed_sources": len(observed_sources),
                    }
                ),
                json.dumps(
                    {
                        name: histogram.model_dump(mode="json")
                        for name, histogram in merged.items()
                    },
                    sort_keys=True,
                ),
                expected,
                len(observed_sources),
                len(observed_sources) / expected if expected else None,
                sum(item.reason == "counter_reset" for item in intervals),
                sum(row["error_class"] == "transport_error" for row in rows),
            ),
        )
        return True

    async def run(self, stop: asyncio.Event, deployment_ids: list[str]) -> None:
        completed: dict[str, datetime] = {}
        while not stop.is_set():
            now = datetime.now(UTC)
            for resolution, seconds in (("1m", 60), ("5m", 300), ("1h", 3600)):
                bucket = floor_bucket(now, seconds) - timedelta(seconds=seconds)
                if completed.get(resolution) == bucket:
                    continue
                for deployment_id in deployment_ids:
                    await self.aggregate(deployment_id, resolution, bucket)
                completed[resolution] = bucket
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=15)
