"""Validated lightweight snapshots used by the public Pages leaderboard."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, TypeAdapter, model_validator

from tooluse_bench.config import PROJECT_ROOT, load_model_catalog
from tooluse_bench.domain import StrictModel
from tooluse_bench.records import RunCompletion, RunManifest
from tooluse_bench.redaction import Redactor
from tooluse_bench.release import ReleaseMetadata, release_file_inventory
from tooluse_bench.reporting import AggregateResult
from tooluse_bench.store import sha256_file

PUBLIC_RESULTS_ROOT = PROJECT_ROOT / "public-results"
EXPECTED_SNAPSHOT_FILES = {
    "checksums.sha256",
    "completion.json",
    "manifest.json",
    "metrics.json",
    "release-checksums.sha256",
    "release-metadata.json",
    "release-metrics.json",
    "release-report.md",
    "report.md",
    "snapshot.json",
}
MAX_PUBLIC_FILE_BYTES = 2 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SNAPSHOT_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
AGGREGATE_LIST = TypeAdapter(list[AggregateResult])


class SnapshotStatus(StrEnum):
    CANDIDATE = "candidate"
    RELEASED = "released"


class PublicSnapshotMetadata(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    title: str
    status: SnapshotStatus
    release_url: HttpUrl | None = None
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def released_snapshots_require_a_url(self) -> PublicSnapshotMetadata:
        if self.status is SnapshotStatus.RELEASED and self.release_url is None:
            raise ValueError("released snapshots must include release_url")
        return self


class PublicSnapshotReference(StrictModel):
    run_id: str
    path: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    title: str
    status: SnapshotStatus


class PublicResultIndex(StrictModel):
    schema_version: Literal[1] = 1
    latest_run_id: str
    snapshots: list[PublicSnapshotReference] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> PublicResultIndex:
        run_ids = [item.run_id for item in self.snapshots]
        paths = [item.path for item in self.snapshots]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("public snapshot run IDs must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("public snapshot paths must be unique")
        if self.latest_run_id not in run_ids:
            raise ValueError("latest_run_id must reference a public snapshot")
        return self


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_regular_text_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"public result path is not a regular file: {path}")
    if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
        raise ValueError(f"public result file exceeds size limit: {path}")
    path.read_text(encoding="utf-8")


def _load_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            raise ValueError(f"{path}:{line_number}: malformed checksum")
        digest, filename = parts
        if filename in checksums:
            raise ValueError(f"{path}:{line_number}: duplicate filename")
        checksums[filename] = digest
    return checksums


def validate_public_snapshot(
    directory: Path,
    *,
    redactor: Redactor | None = None,
) -> tuple[PublicSnapshotMetadata, list[AggregateResult]]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"public snapshot is not a real directory: {directory}")
    files = {path.name for path in directory.iterdir() if path.is_file()}
    if files != EXPECTED_SNAPSHOT_FILES:
        missing = sorted(EXPECTED_SNAPSHOT_FILES - files)
        extra = sorted(files - EXPECTED_SNAPSHOT_FILES)
        raise ValueError(
            f"invalid public snapshot files; missing={missing}, extra={extra}"
        )
    if any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("public snapshot must not contain symlinks")

    scanner = redactor or Redactor.from_catalog(load_model_catalog())
    for path in sorted(directory.iterdir()):
        _validate_regular_text_file(path)
        findings = scanner.findings(path.read_text(encoding="utf-8"))
        if findings:
            raise ValueError(f"{path} contains: {', '.join(findings)}")

    expected = _load_checksums(directory / "checksums.sha256")
    expected_names = EXPECTED_SNAPSHOT_FILES - {"checksums.sha256"}
    if set(expected) != expected_names:
        raise ValueError("public snapshot checksum inventory does not match files")
    actual = {
        path.name: sha256_file(path)
        for path in sorted(directory.iterdir())
        if path.name != "checksums.sha256"
    }
    if expected != actual:
        raise ValueError("public snapshot checksums do not match")

    release = ReleaseMetadata.model_validate(
        _load_json(directory / "release-metadata.json")
    )
    release_files = release_file_inventory(release.schema_version)
    release_checksums = _load_checksums(directory / "release-checksums.sha256")
    if set(release_checksums) != release_files - {"checksums.sha256"}:
        raise ValueError("source release checksum inventory does not match files")
    release_sources = {
        "completion.json": "completion.json",
        "manifest.json": "manifest.json",
        "release-metadata.json": "release-metadata.json",
        "release-metrics.json": "metrics.json",
        "release-report.md": "report.md",
    }
    for snapshot_name, release_name in release_sources.items():
        if sha256_file(directory / snapshot_name) != release_checksums[release_name]:
            raise ValueError(
                f"{snapshot_name} does not match the source release checksum"
            )

    manifest = RunManifest.model_validate(_load_json(directory / "manifest.json"))
    completion = RunCompletion.model_validate(_load_json(directory / "completion.json"))
    snapshot = PublicSnapshotMetadata.model_validate(
        _load_json(directory / "snapshot.json")
    )
    metrics = AGGREGATE_LIST.validate_python(_load_json(directory / "metrics.json"))
    release_metrics_payload = _load_json(directory / "release-metrics.json")
    AGGREGATE_LIST.validate_python(release_metrics_payload)
    if not isinstance(release_metrics_payload, list):
        raise ValueError("source release metrics must be a list")
    metric_identity = (
        "benchmark_id",
        "benchmark_version",
        "profile",
        "lane",
        "deployment_id",
    )
    release_metrics_by_key = {
        tuple(item.get(field) for field in metric_identity): item
        for item in release_metrics_payload
        if isinstance(item, dict)
    }
    public_metrics_payload = _load_json(directory / "metrics.json")
    if not isinstance(public_metrics_payload, list):
        raise ValueError("public metrics must be a list")
    for item in public_metrics_payload:
        if not isinstance(item, dict):
            raise ValueError("public metric must be an object")
        source = release_metrics_by_key.get(
            tuple(item.get(field) for field in metric_identity)
        )
        if source is None or any(
            source.get(key) != value for key, value in item.items()
        ):
            raise ValueError(
                "public metric is not a faithful release-metric projection"
            )
    run_ids = {manifest.run_id, completion.run_id, release.run_id, snapshot.run_id}
    if len(run_ids) != 1:
        raise ValueError("public snapshot run IDs do not match")
    if release.git_commit != manifest.git_commit:
        raise ValueError("public snapshot Git commits do not match")
    if set(release.files) != release_files:
        raise ValueError("public snapshot release file inventory does not match")
    release_created_at = datetime.fromisoformat(release.created_at)
    if (
        snapshot.created_at != manifest.created_at
        or release_created_at != manifest.created_at
    ):
        raise ValueError("public snapshot creation timestamps do not match")
    if completion.result_count != sum(item.record_count for item in metrics):
        raise ValueError(
            "public snapshot metric record count does not match completion"
        )
    selected = set(manifest.selected_deployments)
    if any(item.deployment_id not in selected for item in metrics):
        raise ValueError("public snapshot metric references an unknown deployment")
    metric_keys = [
        (
            item.benchmark_id,
            item.benchmark_version,
            item.profile,
            item.lane,
            item.deployment_id,
        )
        for item in metrics
    ]
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("public snapshot contains duplicate metric groups")
    return snapshot, metrics


def validate_public_results(
    root: Path = PUBLIC_RESULTS_ROOT,
) -> tuple[
    PublicResultIndex, dict[str, tuple[PublicSnapshotMetadata, list[AggregateResult]]]
]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"public results root is not a real directory: {root}")
    root_entries = list(root.iterdir())
    if any(path.is_symlink() for path in root_entries):
        raise ValueError("public results root must not contain symlinks")
    root_files = {path.name for path in root_entries if path.is_file()}
    if root_files != {"index.json"}:
        raise ValueError(
            "public results root may contain only index.json and snapshots"
        )
    _validate_regular_text_file(root / "index.json")
    index = PublicResultIndex.model_validate(_load_json(root / "index.json"))
    referenced_paths = {item.path for item in index.snapshots}
    actual_paths = {
        path.name for path in root.iterdir() if path.is_dir() and not path.is_symlink()
    }
    if referenced_paths != actual_paths:
        raise ValueError("public result index does not match snapshot directories")

    snapshots: dict[str, tuple[PublicSnapshotMetadata, list[AggregateResult]]] = {}
    for reference in index.snapshots:
        if not SNAPSHOT_PATH_PATTERN.fullmatch(reference.path):
            raise ValueError(f"invalid public snapshot path: {reference.path}")
        snapshot, metrics = validate_public_snapshot(root / reference.path)
        if (
            snapshot.run_id != reference.run_id
            or snapshot.title != reference.title
            or snapshot.status is not reference.status
        ):
            raise ValueError("public result index metadata does not match snapshot")
        snapshots[reference.run_id] = (snapshot, metrics)
    return index, snapshots


def render_public_results_markdown(root: Path = PUBLIC_RESULTS_ROOT) -> str:
    index, _ = validate_public_results(root)
    sections = ["# Results", ""]
    for reference in index.snapshots:
        report = (root / reference.path / "report.md").read_text(encoding="utf-8")
        demoted = re.sub(
            r"(?m)^(#+) ",
            lambda match: f"#{match.group(1)} ",
            report.rstrip(),
        )
        sections.extend((demoted, ""))
    return "\n".join(sections)
