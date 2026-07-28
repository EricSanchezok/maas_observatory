"""Build and validate deterministic, sanitized GitHub Release artifacts."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from tooluse_bench.config import PROJECT_ROOT, load_model_catalog
from tooluse_bench.domain import StrictModel
from tooluse_bench.records import RunCompletion, RunManifest, TaskResult
from tooluse_bench.redaction import Redactor
from tooluse_bench.store import sha256_file

EXPECTED_RELEASE_FILES = {
    "LICENSE-DATA",
    "checksums.sha256",
    "completion.json",
    "manifest.json",
    "metrics.csv",
    "metrics.json",
    "release-metadata.json",
    "report.md",
    "results.jsonl.gz",
}


class ReleaseMetadata(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    git_commit: str
    created_at: str
    data_license: Literal["CC-BY-4.0"] = "CC-BY-4.0"
    files: tuple[str, ...] = Field(min_length=1)
    source_results_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_results_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_sanitized_json(
    source: Path,
    destination: Path,
    redactor: Redactor,
) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    destination.write_text(
        _json_text(redactor.value(payload)),
        encoding="utf-8",
    )


def _write_sanitized_results(
    source: Path,
    destination: Path,
    redactor: Redactor,
) -> None:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            sanitized = redactor.value(payload)
            try:
                TaskResult.model_validate(sanitized)
            except ValueError as exc:
                raise ValueError(
                    f"redaction invalidated TaskResult at line {line_number}"
                ) from exc
            rendered = json.dumps(
                sanitized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            compressed.write(f"{rendered}\n".encode())
    destination.write_bytes(buffer.getvalue())


def _checksums(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256_file(path)
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "checksums.sha256"
    }


def _write_checksums(directory: Path) -> None:
    lines = [
        f"{digest}  {filename}"
        for filename, digest in sorted(_checksums(directory).items())
    ]
    (directory / "checksums.sha256").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _build_deterministic_tar(source: Path, destination: Path) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for path in sorted(source.iterdir()):
            info = archive.gettarinfo(str(path), arcname=f"{source.name}/{path.name}")
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    with (
        destination.open("wb") as destination_handle,
        gzip.GzipFile(fileobj=destination_handle, mode="wb", mtime=0) as compressed,
    ):
        compressed.write(raw.getvalue())


def build_release(
    run_directory: Path,
    *,
    output_root: Path | None = None,
) -> tuple[Path, Path]:
    report_directory = run_directory / "report"
    required = [
        run_directory / "manifest.json",
        run_directory / "completion.json",
        run_directory / "results.jsonl",
        report_directory / "metrics.json",
        report_directory / "metrics.csv",
        report_directory / "report.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"run is not release-ready; missing: {', '.join(missing)}")
    manifest = RunManifest.model_validate_json(required[0].read_text(encoding="utf-8"))
    completion = RunCompletion.model_validate_json(
        required[1].read_text(encoding="utf-8")
    )
    if completion.results_sha256 != sha256_file(required[2]):
        raise ValueError("private results checksum does not match completion record")

    catalog = load_model_catalog()
    redactor = Redactor.from_catalog(catalog)
    destination_root = output_root or PROJECT_ROOT / "release-staging"
    destination = destination_root / manifest.run_id
    destination.mkdir(parents=True, exist_ok=False)

    _write_sanitized_json(required[0], destination / "manifest.json", redactor)
    _write_sanitized_json(required[1], destination / "completion.json", redactor)
    _write_sanitized_json(required[3], destination / "metrics.json", redactor)
    (destination / "metrics.csv").write_text(
        redactor.text(required[4].read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    (destination / "report.md").write_text(
        redactor.text(required[5].read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    _write_sanitized_results(
        required[2],
        destination / "results.jsonl.gz",
        redactor,
    )
    shutil.copyfile(PROJECT_ROOT / "LICENSE-DATA", destination / "LICENSE-DATA")
    metadata = ReleaseMetadata(
        run_id=manifest.run_id,
        git_commit=manifest.git_commit,
        created_at=manifest.created_at.isoformat(),
        files=tuple(sorted(EXPECTED_RELEASE_FILES)),
        source_results_sha256=completion.results_sha256,
        published_results_sha256=sha256_file(destination / "results.jsonl.gz"),
    )
    (destination / "release-metadata.json").write_text(
        _json_text(metadata.model_dump(mode="json")),
        encoding="utf-8",
    )
    _write_checksums(destination)
    validate_release(destination, redactor=redactor)

    archive = destination_root / f"{manifest.run_id}.tar.gz"
    _build_deterministic_tar(destination, archive)
    return destination, archive


def validate_release(
    directory: Path,
    *,
    redactor: Redactor | None = None,
) -> None:
    filenames = {path.name for path in directory.iterdir() if path.is_file()}
    if filenames != EXPECTED_RELEASE_FILES:
        missing = sorted(EXPECTED_RELEASE_FILES - filenames)
        extra = sorted(filenames - EXPECTED_RELEASE_FILES)
        raise ValueError(f"invalid release files; missing={missing}, extra={extra}")
    scanner = redactor or Redactor.from_catalog(load_model_catalog())
    for path in sorted(directory.iterdir()):
        if path.suffix == ".gz":
            text = gzip.decompress(path.read_bytes()).decode("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        findings = scanner.findings(text)
        if findings:
            raise ValueError(f"{path.name} contains: {', '.join(findings)}")

    expected: dict[str, str] = {}
    for line in (directory / "checksums.sha256").read_text().splitlines():
        digest, filename = line.split("  ", 1)
        expected[filename] = digest
    actual = _checksums(directory)
    if expected != actual:
        raise ValueError("release checksums do not match")

    manifest = RunManifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    completion = RunCompletion.model_validate_json(
        (directory / "completion.json").read_text(encoding="utf-8")
    )
    metadata = ReleaseMetadata.model_validate_json(
        (directory / "release-metadata.json").read_text(encoding="utf-8")
    )
    if not (manifest.run_id == completion.run_id == metadata.run_id):
        raise ValueError("release run IDs do not match")
    if metadata.files != tuple(sorted(EXPECTED_RELEASE_FILES)):
        raise ValueError("release metadata file inventory does not match")
    if metadata.source_results_sha256 != completion.results_sha256:
        raise ValueError("release source result hashes do not match")
    if metadata.published_results_sha256 != sha256_file(directory / "results.jsonl.gz"):
        raise ValueError("published result hash does not match release metadata")
    for line_number, line in enumerate(
        gzip.decompress((directory / "results.jsonl.gz").read_bytes())
        .decode("utf-8")
        .splitlines(),
        start=1,
    ):
        try:
            result = TaskResult.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(
                f"results.jsonl.gz:{line_number}: invalid TaskResult"
            ) from exc
        if result.run_id != manifest.run_id:
            raise ValueError("release result run_id does not match manifest")
