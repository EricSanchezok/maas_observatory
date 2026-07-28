"""Run identity and environment provenance."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tooluse_bench import __version__
from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.domain import ExperimentPlan, ModelDeployment
from tooluse_bench.records import BenchmarkMetadata, RunManifest
from tooluse_bench.store import sha256_file


def _git(*args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def git_state() -> tuple[str, bool]:
    try:
        commit = _git("rev-parse", "HEAD")
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unavailable", True
    return commit, dirty


def combined_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            identity = path.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            identity = Path(path.name)
        digest.update(str(identity).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def create_manifest(
    *,
    experiment: ExperimentPlan,
    experiment_path: Path,
    catalog_path: Path,
    deployments: list[ModelDeployment],
    benchmarks: list[BenchmarkMetadata],
    output_root: Path,
    now: datetime | None = None,
) -> RunManifest:
    created_at = now or datetime.now(UTC)
    commit, dirty = git_state()
    config_digest = combined_sha256([catalog_path, experiment_path])
    short_commit = commit[:8] if commit != "unavailable" else "nogit"
    timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    run_id = (
        f"{timestamp}-{experiment.experiment_id}-{short_commit}-{config_digest[:8]}"
    )
    output_directory = output_root / run_id
    lock_paths = [
        PROJECT_ROOT / "uv.lock",
        PROJECT_ROOT / "benchmark-envs" / "bfcl" / "uv.lock",
        PROJECT_ROOT / "benchmark-envs" / "toolathlon" / "uv.lock",
    ]
    existing_locks = [path for path in lock_paths if path.exists()]
    lock_digests = {_display_path(path): sha256_file(path) for path in existing_locks}
    lock_digest = combined_sha256(existing_locks) if existing_locks else "unavailable"
    return RunManifest(
        run_id=run_id,
        experiment_id=experiment.experiment_id,
        created_at=created_at,
        git_commit=commit,
        git_dirty=dirty,
        package_version=__version__,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        configuration_sha256=config_digest,
        dependency_lock_sha256=lock_digest,
        dependency_locks_sha256=lock_digests,
        catalog_path=_display_path(catalog_path),
        experiment_path=_display_path(experiment_path),
        output_directory=output_directory,
        benchmarks=tuple(benchmarks),
        selected_deployments=tuple(item.deployment_id for item in deployments),
        lanes=tuple(experiment.lanes),
    )
