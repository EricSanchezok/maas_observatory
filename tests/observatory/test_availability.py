from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from maas_observatory.api import RuntimeHealth, create_app
from maas_observatory.database import isoformat
from tests.observatory.helpers import (
    close_database,
    configured_catalog,
    insert_probe,
    make_settings,
    open_database,
)


def _stamp(days_ago: int, hour: int) -> str:
    base = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    return isoformat(base - timedelta(days=days_ago))


def test_availability_sliding_window_with_maintenance_exclusion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        catalog = configured_catalog()
        database, writer = await open_database(settings, catalog)
        deployment_id = catalog.deployments[0].deployment_id
        try:
            # today: 2 successes + 1 maintenance-skipped
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=_stamp(0, 0),
            )
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=_stamp(0, 0),
            )
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="skipped",
                error_code="maintenance",
                finished_at=_stamp(0, 0),
            )
            # yesterday: success + failed + skipped-maintenance inside an
            # events maintenance interval (double-condition row)
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=_stamp(1, 9),
            )
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="failed",
                error_class="service_error",
                error_code="http_503",
                finished_at=_stamp(1, 10),
            )
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="skipped",
                error_code="maintenance",
                finished_at=_stamp(1, 12),
            )
            # 2 days ago: a success route fully covered by a maintenance event
            await insert_probe(
                database,
                deployment_id,
                kind="route",
                profile_id="",
                outcome="success",
                finished_at=_stamp(2, 12),
            )
            await database.write(
                """
                INSERT INTO events(
                    deployment_id, event_key, kind, severity, state,
                    title, detail_json, started_at, ended_at
                ) VALUES (?, ?, 'maintenance', 'info', 'closed',
                          'Maintenance', '{}', ?, ?)
                """,
                (deployment_id, "maintenance:day2", _stamp(2, 6), _stamp(2, 18)),
            )
            await database.write(
                """
                INSERT INTO events(
                    deployment_id, event_key, kind, severity, state,
                    title, detail_json, started_at, ended_at
                ) VALUES (?, ?, 'maintenance', 'info', 'closed',
                          'Maintenance', '{}', ?, ?)
                """,
                (deployment_id, "maintenance:day1", _stamp(1, 11), _stamp(1, 13)),
            )

            app = create_app(
                database, catalog, settings, RuntimeHealth(), frontend_dir=tmp_path
            )
            with TestClient(app) as client:
                assert client.get("/api/v1/availability?days=15").status_code == 400
                assert client.get("/api/v1/availability?days=3").status_code == 422
                response = client.get("/api/v1/availability?days=30")
                assert response.status_code == 200
                body = response.json()
                assert body["schema_version"] == "6"
                assert body["data_window"] == "30d"
                entry = next(
                    row for row in body["data"] if row["deployment_id"] == deployment_id
                )
                assert entry["days"] == 30
                assert len(entry["daily"]) == 30
                today, yesterday, two_days = (
                    entry["daily"][-1],
                    entry["daily"][-2],
                    entry["daily"][-3],
                )
                assert today["uptime_pct"] == 100.0
                assert today["samples"] == 2
                assert today["maintenance_excluded"] == 1
                assert yesterday["uptime_pct"] == 50.0
                assert yesterday["samples"] == 2
                assert yesterday["maintenance_excluded"] == 1  # no double count
                assert two_days["uptime_pct"] is None
                assert two_days["samples"] == 0
                assert two_days["maintenance_excluded"] == 1  # events branch
                assert entry["daily"][-4]["uptime_pct"] is None
                assert entry["daily"][-4]["samples"] == 0
                other = next(
                    row for row in body["data"] if row["deployment_id"] != deployment_id
                )
                assert all(day["samples"] == 0 for day in other["daily"])

                short = client.get("/api/v1/availability?days=7").json()
                assert short["data_window"] == "7d"
                short_entry = next(
                    row
                    for row in short["data"]
                    if row["deployment_id"] == deployment_id
                )
                assert len(short_entry["daily"]) == 7

                overview = client.get("/api/v1/experience/overview").json()["data"]
                item = next(
                    row for row in overview if row["deployment_id"] == deployment_id
                )
                # 24h: today rows only -> 2/2; 7d/30d: 4 successes / 4 samples
                # 24h window may include yesterday probes if within wall-clock 24h
                assert item["uptime_24h"] is not None and item["uptime_24h"] >= 50.0
                assert item["uptime_7d"] == 100.0
                assert item["uptime_30d"] == 100.0
        finally:
            await close_database(database, writer)

    asyncio.run(scenario())
