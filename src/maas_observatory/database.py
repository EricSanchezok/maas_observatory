"""SQLite persistence with migrations and a single asynchronous writer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from maas_common.catalog import ModelCatalog
from maas_observatory.settings import StorageSettings

SCHEMA_VERSION = 2

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    document_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id TEXT PRIMARY KEY,
    alias TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    family TEXT NOT NULL,
    upstream_model TEXT NOT NULL,
    precision TEXT NOT NULL,
    model_id TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    config_snapshot_id INTEGER NOT NULL REFERENCES config_snapshots(id),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics_sources (
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    source_id TEXT NOT NULL DEFAULT 'legacy-primary',
    active INTEGER NOT NULL DEFAULT 1,
    config_snapshot_id INTEGER NOT NULL REFERENCES config_snapshots(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(deployment_id, source_id)
);
CREATE TABLE IF NOT EXISTS scrape_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    source_id TEXT NOT NULL DEFAULT 'legacy-primary',
    observed_at TEXT NOT NULL,
    elapsed_seconds REAL,
    quality TEXT NOT NULL,
    error_class TEXT NOT NULL,
    error_code TEXT,
    counters_json TEXT NOT NULL,
    gauges_json TEXT NOT NULL,
    histograms_json TEXT NOT NULL,
    interval_json TEXT,
    UNIQUE(deployment_id, source_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_scrape_deployment_time
    ON scrape_snapshots(deployment_id, source_id, observed_at);
CREATE TABLE IF NOT EXISTS metric_accumulators (
    deployment_id TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT 'legacy-primary',
    observed_at TEXT NOT NULL,
    counters_json TEXT NOT NULL,
    histograms_json TEXT NOT NULL,
    PRIMARY KEY(deployment_id, source_id)
);
CREATE TABLE IF NOT EXISTS rollups (
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    resolution TEXT NOT NULL CHECK(resolution IN ('1m', '5m', '1h')),
    bucket_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    quality TEXT NOT NULL,
    source_mix_json TEXT NOT NULL,
    histogram_delta_json TEXT NOT NULL,
    expected_source_count INTEGER NOT NULL DEFAULT 0,
    observed_source_count INTEGER NOT NULL DEFAULT 0,
    source_seconds_coverage REAL,
    reset_count INTEGER NOT NULL DEFAULT 0,
    transport_failure_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(deployment_id, resolution, bucket_at)
);
CREATE INDEX IF NOT EXISTS idx_rollup_resolution_time
    ON rollups(resolution, bucket_at);
CREATE TABLE IF NOT EXISTS probe_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    kind TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error_class TEXT NOT NULL,
    error_code TEXT,
    profile_id TEXT,
    definition_version TEXT NOT NULL,
    vantage_id TEXT,
    confirmation_of INTEGER REFERENCES probe_runs(id),
    measurement_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_probe_deployment_kind_time
    ON probe_runs(deployment_id, kind, finished_at);
CREATE TABLE IF NOT EXISTS probe_measurements (
    probe_run_id INTEGER NOT NULL REFERENCES probe_runs(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    quality TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY(probe_run_id, metric)
);
CREATE TABLE IF NOT EXISTS current_states (
    deployment_id TEXT PRIMARY KEY REFERENCES deployments(deployment_id),
    service_state TEXT NOT NULL,
    telemetry_state TEXT NOT NULL,
    experience_state TEXT NOT NULL DEFAULT 'experience_collecting',
    reasons_json TEXT NOT NULL,
    telemetry_at TEXT,
    evaluated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    service_state TEXT NOT NULL,
    telemetry_state TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    state TEXT NOT NULL,
    title TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(deployment_id, event_key, started_at)
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(started_at);
CREATE TABLE IF NOT EXISTS scheduler_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_usage (
    deployment_id TEXT NOT NULL REFERENCES deployments(deployment_id),
    budget_date TEXT NOT NULL,
    short_requests INTEGER NOT NULL DEFAULT 0,
    context_requests INTEGER NOT NULL DEFAULT 0,
    canary_requests INTEGER NOT NULL DEFAULT 0,
    experience_requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    speed_requests INTEGER NOT NULL DEFAULT 0,
    inference_requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(deployment_id, budget_date)
);
CREATE TABLE IF NOT EXISTS experience_profiles (
    profile_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    fixture_sha256 TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, definition_version)
);
CREATE TABLE IF NOT EXISTS collection_epochs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""


def isoformat(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat()


@dataclass
class WriteCommand:
    sql: str
    params: Sequence[Any]
    future: asyncio.Future[int]


class Database:
    """Own the SQLite file and serialize all runtime writes."""

    def __init__(self, settings: StorageSettings) -> None:
        self.settings = settings
        self.root = settings.root_path()
        self.path = self.root / settings.database
        self.backup_dir = self.root / "backups"
        self.export_dir = self.root / "exports"
        self.queue: asyncio.Queue[WriteCommand | None] = asyncio.Queue(
            maxsize=settings.writer_queue_size
        )
        self._writer_ready = asyncio.Event()

    def prepare_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    async def _configure(connection: aiosqlite.Connection) -> None:
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute("PRAGMA synchronous=NORMAL")

    async def migrate(self) -> None:
        self.prepare_directories()
        connection = await aiosqlite.connect(self.path)
        try:
            await self._configure(connection)
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            row = await (await connection.execute("PRAGMA user_version")).fetchone()
            version = int(row[0]) if row else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    "database schema "
                    f"{version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 1:
                raise RuntimeError(
                    "schema v1 data uses deployment-scoped counters; run "
                    "'maas-observatory db backup' then "
                    "'maas-observatory db reset --confirm metrics-source-v2'"
                )
            if version < 2:
                await connection.executescript(MIGRATION_2)
                await connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, isoformat()),
                )
                await connection.execute(
                    """
                    INSERT INTO collection_epochs(schema_version, started_at, reason)
                    VALUES (2, ?, 'metrics-source-v2')
                    """,
                    (isoformat(),),
                )
                await connection.execute("PRAGMA user_version=2")
            await connection.commit()
        finally:
            await connection.close()

    async def quick_check(self) -> tuple[bool, str]:
        if not self.path.exists():
            return False, "database_missing"
        connection = await aiosqlite.connect(self.path)
        try:
            await self._configure(connection)
            row = await (await connection.execute("PRAGMA quick_check")).fetchone()
            detail = str(row[0]) if row else "no_result"
            return detail == "ok", detail
        finally:
            await connection.close()

    async def writer_loop(self) -> None:
        connection = await aiosqlite.connect(self.path)
        try:
            await self._configure(connection)
            self._writer_ready.set()
            while True:
                command = await self.queue.get()
                try:
                    if command is None:
                        return
                    cursor = await connection.execute(command.sql, command.params)
                    await connection.commit()
                    if not command.future.done():
                        command.future.set_result(cursor.lastrowid or 0)
                except BaseException as exc:
                    await connection.rollback()
                    if command is not None and not command.future.done():
                        command.future.set_exception(exc)
                    if not isinstance(exc, Exception):
                        raise
                finally:
                    self.queue.task_done()
        finally:
            self._writer_ready.clear()
            await connection.close()

    async def wait_writer(self) -> None:
        await self._writer_ready.wait()

    async def stop_writer(self) -> None:
        await self.queue.put(None)

    async def write(self, sql: str, params: Sequence[Any] = ()) -> int:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[int] = loop.create_future()
        await self.queue.put(WriteCommand(sql=sql, params=params, future=future))
        return await future

    async def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        try:
            await self._configure(connection)
            cursor = await connection.execute(sql, params)
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await connection.close()

    async def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = await self.query(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    async def synchronize_catalog(self, catalog: ModelCatalog) -> None:
        public_document = {
            "schema_version": catalog.schema_version,
            "deployments": [
                deployment.model_dump(
                    mode="json",
                    exclude={
                        "endpoint",
                        "request_defaults",
                        "profiles",
                        "serving",
                    },
                )
                for deployment in catalog.deployments
            ],
        }
        serialized = json.dumps(public_document, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        await self.write(
            """
            INSERT OR IGNORE INTO config_snapshots(created_at, sha256, document_json)
            VALUES (?, ?, ?)
            """,
            (isoformat(), digest, serialized),
        )
        snapshot_id = await self.scalar(
            "SELECT id FROM config_snapshots WHERE sha256=?", (digest,)
        )
        now = isoformat()
        active_ids: list[str] = []
        for deployment in catalog.deployments:
            active_ids.append(deployment.deployment_id)
            await self.write(
                """
                INSERT INTO deployments(
                    deployment_id, alias, display_name, provider, family,
                    upstream_model, precision, model_id, active,
                    config_snapshot_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(deployment_id) DO UPDATE SET
                    alias=excluded.alias,
                    display_name=excluded.display_name,
                    provider=excluded.provider,
                    family=excluded.family,
                    upstream_model=excluded.upstream_model,
                    precision=excluded.precision,
                    model_id=excluded.model_id,
                    active=1,
                    config_snapshot_id=excluded.config_snapshot_id,
                    updated_at=excluded.updated_at
                """,
                (
                    deployment.deployment_id,
                    deployment.alias,
                    deployment.name,
                    deployment.provider,
                    deployment.family,
                    deployment.upstream_model,
                    str(deployment.precision),
                    deployment.model_id,
                    snapshot_id,
                    now,
                ),
            )
        placeholders = ",".join("?" for _ in active_ids)
        deactivate_sql = (
            "UPDATE deployments SET active=0 "
            f"WHERE deployment_id NOT IN ({placeholders})"
        )
        await self.write(
            deactivate_sql,
            tuple(active_ids),
        )

    async def synchronize_metrics_sources(
        self, catalog: ModelCatalog, sources: dict[str, list[Any]]
    ) -> None:
        """Persist only stable source identifiers; never URLs or credentials."""

        now = isoformat()
        snapshot_id = await self.scalar(
            "SELECT id FROM config_snapshots ORDER BY id DESC LIMIT 1"
        )
        if snapshot_id is None:
            raise RuntimeError("catalog must be synchronized before metrics sources")
        by_alias = {item.alias: item for item in catalog.deployments}
        for alias, configured in sources.items():
            deployment = by_alias.get(alias)
            if deployment is None:
                raise ValueError(f"metrics sources reference unknown model: {alias}")
            active: list[str] = []
            for source in configured:
                active.append(source.source_id)
                await self.write(
                    """
                    INSERT INTO metrics_sources(
                        deployment_id, source_id, active,
                        config_snapshot_id, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(deployment_id, source_id) DO UPDATE SET
                        active=1, config_snapshot_id=excluded.config_snapshot_id,
                        updated_at=excluded.updated_at
                    """,
                    (deployment.deployment_id, source.source_id, snapshot_id, now),
                )
            if active:
                placeholders = ",".join("?" for _ in active)
                await self.write(
                    f"""
                    UPDATE metrics_sources SET active=0
                    WHERE deployment_id=? AND source_id NOT IN ({placeholders})
                    """,
                    (deployment.deployment_id, *active),
                )

    def reset_v2(self, confirmation: str) -> None:
        if confirmation != "metrics-source-v2":
            raise ValueError("confirmation must be exactly metrics-source-v2")
        for path in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            if path.exists():
                path.unlink()

    async def backup(self, *, now: datetime | None = None) -> Path:
        self.prepare_directories()
        timestamp = now or datetime.now(UTC)
        destination = self.backup_dir / (
            f"observatory-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        )

        def copy() -> None:
            source = sqlite3.connect(self.path)
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

        await asyncio.to_thread(copy)
        return destination

    async def prune_backups(self, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        files = sorted(self.backup_dir.glob("observatory-*.sqlite3"), reverse=True)
        daily: set[str] = set()
        weekly: set[str] = set()
        keep: set[Path] = set()
        for path in files:
            try:
                stamp = datetime.strptime(
                    path.stem.removeprefix("observatory-"), "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=UTC)
            except ValueError:
                continue
            age = timestamp - stamp
            day_key = stamp.strftime("%Y-%m-%d")
            week_key = stamp.strftime("%G-W%V")
            within_daily = age <= timedelta(days=self.settings.daily_backups)
            if within_daily and day_key not in daily:
                daily.add(day_key)
                keep.add(path)
            if (
                age <= timedelta(weeks=self.settings.weekly_backups)
                and week_key not in weekly
            ):
                weekly.add(week_key)
                keep.add(path)
        for path in files:
            if path not in keep:
                path.unlink()

    async def apply_retention(self, *, now: datetime | None = None) -> None:
        timestamp = now or datetime.now(UTC)
        thresholds = {
            "scrape_snapshots": timestamp
            - timedelta(days=self.settings.raw_retention_days),
            "probe_runs": timestamp
            - timedelta(days=self.settings.probe_retention_days),
        }
        await self.write(
            "DELETE FROM scrape_snapshots WHERE observed_at < ?",
            (isoformat(thresholds["scrape_snapshots"]),),
        )
        await self.write(
            "DELETE FROM probe_runs WHERE finished_at < ?",
            (isoformat(thresholds["probe_runs"]),),
        )
        await self.write(
            "DELETE FROM rollups WHERE resolution='1m' AND bucket_at < ?",
            (
                isoformat(
                    timestamp - timedelta(days=self.settings.minute_retention_days)
                ),
            ),
        )
        await self.write(
            "DELETE FROM rollups WHERE resolution='5m' AND bucket_at < ?",
            (
                isoformat(
                    timestamp - timedelta(days=self.settings.five_minute_retention_days)
                ),
            ),
        )
        await self.write("PRAGMA incremental_vacuum(200)")
