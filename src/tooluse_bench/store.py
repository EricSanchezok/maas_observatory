"""Append-only private run storage."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tooluse_bench.records import RunCompletion, RunManifest, TaskResult, TaskStatus


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunStore:
    """A run directory whose identity and manifest can never be overwritten."""

    def __init__(self, directory: Path, manifest: RunManifest) -> None:
        self.directory = directory
        self.manifest = manifest
        self.results_path = directory / "results.jsonl"
        self._status_counts: Counter[TaskStatus] = Counter()
        self._result_count = 0
        self._identities: set[tuple[str, str, str, str, str, int]] = set()
        self._closed = False

    @classmethod
    def create(cls, directory: Path, manifest: RunManifest) -> RunStore:
        directory.mkdir(parents=True, exist_ok=False)
        manifest_path = directory / "manifest.json"
        manifest_payload = manifest.model_dump(mode="json")
        rendered_manifest = json.dumps(
            manifest_payload, ensure_ascii=False, indent=2, sort_keys=True
        )
        manifest_path.write_text(
            f"{rendered_manifest}\n",
            encoding="utf-8",
        )
        (directory / "artifacts").mkdir()
        return cls(directory, manifest)

    def append(self, result: TaskResult) -> None:
        if self._closed:
            raise RuntimeError("run store is finalized")
        if result.run_id != self.manifest.run_id:
            raise ValueError("result run_id does not match manifest")
        identity = (
            result.benchmark_id,
            result.profile,
            result.lane.value,
            result.deployment_id,
            result.task_id,
            result.trial,
        )
        if identity in self._identities:
            raise ValueError(f"duplicate task result identity: {identity}")
        line = canonical_json(result.model_dump(mode="json"))
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._result_count += 1
        self._identities.add(identity)
        self._status_counts[result.status] += 1

    def finalize(self) -> RunCompletion:
        if self._closed:
            raise RuntimeError("run store is already finalized")
        if not self.results_path.exists():
            self.results_path.touch()
        completion = RunCompletion(
            run_id=self.manifest.run_id,
            finished_at=datetime.now(UTC),
            result_count=self._result_count,
            status_counts=dict(self._status_counts),
            results_sha256=sha256_file(self.results_path),
        )
        path = self.directory / "completion.json"
        rendered_completion = json.dumps(
            completion.model_dump(mode="json"), indent=2, sort_keys=True
        )
        path.write_text(
            f"{rendered_completion}\n",
            encoding="utf-8",
        )
        self._closed = True
        return completion
