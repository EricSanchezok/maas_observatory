"""Build and validate deterministic, sanitized GitHub Release artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from tooluse_bench.config import PROJECT_ROOT, load_model_catalog
from tooluse_bench.domain import StrictModel
from tooluse_bench.provenance import git_state
from tooluse_bench.records import (
    ExecutionAudit,
    RunCompletion,
    RunManifest,
    TaskResult,
    TaskStatus,
)
from tooluse_bench.redaction import Redactor
from tooluse_bench.reporting import ReportMetadata, load_completed_run
from tooluse_bench.store import sha256_file
from tooluse_bench.visualization import FIGURE_FILES, FigureMetadata

RELEASE_FILES_V1 = frozenset(
    {
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
)
RELEASE_FILES_V2 = RELEASE_FILES_V1 | {"execution-audits.json"}
RELEASE_FILES_V3 = RELEASE_FILES_V2 | {
    "benchmark-overview.png",
    "benchmark-overview.svg",
    "figure-metadata.json",
    "report-metadata.json",
}
EXPECTED_RELEASE_FILES = RELEASE_FILES_V3
EXECUTION_AUDIT_LIST = TypeAdapter(list[ExecutionAudit])
MAX_RELEASE_TEXT_FILE_BYTES = 64 * 1024 * 1024
MAX_RESULT_LINE_CHARACTERS = 64 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def release_file_inventory(schema_version: int) -> frozenset[str]:
    if schema_version == 1:
        return RELEASE_FILES_V1
    if schema_version == 2:
        return RELEASE_FILES_V2
    if schema_version == 3:
        return RELEASE_FILES_V3
    raise ValueError(f"unsupported release schema version: {schema_version}")


class ReleaseMetadata(StrictModel):
    schema_version: Literal[1, 2, 3] = 3
    run_id: str
    git_commit: str
    created_at: str
    data_license: Literal["CC-BY-4.0"] = "CC-BY-4.0"
    files: tuple[str, ...] = Field(min_length=1)
    source_results_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_results_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    release_builder_git_commit: str | None = None
    release_builder_git_dirty: bool | None = None
    report_builder_git_commit: str | None = None
    baseline_registry_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def validate_file_inventory(self) -> ReleaseMetadata:
        expected = tuple(sorted(release_file_inventory(self.schema_version)))
        if self.files != expected:
            raise ValueError("release metadata file inventory does not match version")
        if self.schema_version >= 3 and any(
            value is None
            for value in (
                self.release_builder_git_commit,
                self.release_builder_git_dirty,
                self.report_builder_git_commit,
                self.baseline_registry_sha256,
            )
        ):
            raise ValueError("release metadata v3 requires builder provenance")
        return self


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_execution_audits(
    run_directory: Path,
    destination: Path,
    redactor: Redactor,
    *,
    run_id: str,
) -> None:
    audits: list[ExecutionAudit] = []
    for source in sorted(run_directory.glob("artifacts/**/execution-audit.json")):
        sanitized = redactor.value(json.loads(source.read_text(encoding="utf-8")))
        audit = ExecutionAudit.model_validate(sanitized)
        if audit.run_id != run_id:
            raise ValueError(f"execution audit run_id does not match: {source}")
        audits.append(audit)
    identities = [
        (item.benchmark_id, item.deployment_id, item.lane, item.trial)
        for item in audits
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate execution audit identity")
    destination.write_text(
        _json_text([item.model_dump(mode="json") for item in audits]),
        encoding="utf-8",
    )


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
    with (
        source.open(mode="r", encoding="utf-8") as source_handle,
        destination.open(mode="wb") as destination_handle,
        gzip.GzipFile(fileobj=destination_handle, mode="wb", mtime=0) as compressed,
    ):
        for line_number, line in enumerate(source_handle, start=1):
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
    with (
        destination.open("wb") as destination_handle,
        gzip.GzipFile(fileobj=destination_handle, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        for path in sorted(source.iterdir()):
            info = archive.gettarinfo(str(path), arcname=f"{source.name}/{path.name}")
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with path.open("rb") as handle:
                archive.addfile(info, handle)


def validate_release_archive(directory: Path, archive_path: Path) -> None:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("release archive must be a regular file")
    expected = {
        f"{directory.name}/{path.name}": sha256_file(path)
        for path in directory.iterdir()
        if path.is_file()
    }
    actual: dict[str, str] = {}
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive:
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("release archive may contain only regular files")
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.uname
                    or member.gname
                    or member.mtime != 0
                ):
                    raise ValueError(
                        "release archive contains non-deterministic metadata"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("release archive member cannot be read")
                digest = hashlib.sha256()
                while chunk := extracted.read(1024 * 1024):
                    digest.update(chunk)
                if member.name in actual:
                    raise ValueError("release archive contains duplicate paths")
                actual[member.name] = digest.hexdigest()
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("release archive is not a valid gzip tar") from exc
    if actual != expected:
        raise ValueError("release archive contents do not match release directory")


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
        report_directory / "report-metadata.json",
        report_directory / "figure-metadata.json",
        report_directory / "benchmark-overview.svg",
        report_directory / "benchmark-overview.png",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"run is not release-ready; missing: {', '.join(missing)}")
    manifest, completion, _ = load_completed_run(run_directory)

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
    _write_sanitized_json(
        required[6],
        destination / "report-metadata.json",
        redactor,
    )
    _write_sanitized_json(
        required[7],
        destination / "figure-metadata.json",
        redactor,
    )
    shutil.copyfile(required[8], destination / "benchmark-overview.svg")
    shutil.copyfile(required[9], destination / "benchmark-overview.png")
    _write_sanitized_results(
        required[2],
        destination / "results.jsonl.gz",
        redactor,
    )
    _write_execution_audits(
        run_directory,
        destination / "execution-audits.json",
        redactor,
        run_id=manifest.run_id,
    )
    shutil.copyfile(PROJECT_ROOT / "LICENSE-DATA", destination / "LICENSE-DATA")
    report_metadata = ReportMetadata.model_validate_json(
        required[6].read_text(encoding="utf-8")
    )
    builder_commit, builder_dirty = git_state()
    metadata = ReleaseMetadata(
        run_id=manifest.run_id,
        git_commit=manifest.git_commit,
        created_at=manifest.created_at.isoformat(),
        files=tuple(sorted(RELEASE_FILES_V3)),
        source_results_sha256=completion.results_sha256,
        published_results_sha256=sha256_file(destination / "results.jsonl.gz"),
        release_builder_git_commit=builder_commit,
        release_builder_git_dirty=builder_dirty,
        report_builder_git_commit=report_metadata.report_builder_git_commit,
        baseline_registry_sha256=report_metadata.baseline_registry_sha256,
    )
    (destination / "release-metadata.json").write_text(
        _json_text(metadata.model_dump(mode="json")),
        encoding="utf-8",
    )
    _write_checksums(destination)
    validate_release(destination, redactor=redactor)

    archive = destination_root / f"{manifest.run_id}.tar.gz"
    _build_deterministic_tar(destination, archive)
    validate_release_archive(destination, archive)
    return destination, archive


def validate_release(
    directory: Path,
    *,
    redactor: Redactor | None = None,
) -> None:
    metadata_path = directory / "release-metadata.json"
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError("release-metadata.json must be a regular file")
    metadata = ReleaseMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    )
    expected_files = release_file_inventory(metadata.schema_version)
    filenames = {path.name for path in directory.iterdir() if path.is_file()}
    if filenames != expected_files:
        missing = sorted(expected_files - filenames)
        extra = sorted(filenames - expected_files)
        raise ValueError(f"invalid release files; missing={missing}, extra={extra}")
    scanner = redactor or Redactor.from_catalog(load_model_catalog())
    for path in sorted(directory.iterdir()):
        if path.is_symlink():
            raise ValueError(f"release file must not be a symlink: {path.name}")
        if path.name == "results.jsonl.gz":
            try:
                with gzip.open(path, mode="rt", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if len(line) > MAX_RESULT_LINE_CHARACTERS:
                            raise ValueError(
                                "results.jsonl.gz:"
                                f"{line_number}: line exceeds safety limit"
                            )
                        findings = scanner.findings(line)
                        if findings:
                            raise ValueError(
                                f"{path.name}:{line_number} contains: "
                                f"{', '.join(findings)}"
                            )
            except (gzip.BadGzipFile, UnicodeDecodeError, OSError) as exc:
                raise ValueError("results.jsonl.gz is not valid UTF-8 gzip") from exc
            continue
        if path.name == "benchmark-overview.png":
            if path.stat().st_size > MAX_RELEASE_TEXT_FILE_BYTES:
                raise ValueError("release PNG exceeds safety limit")
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("benchmark-overview.png is not a valid PNG")
            continue
        if path.stat().st_size > MAX_RELEASE_TEXT_FILE_BYTES:
            raise ValueError(f"release text file exceeds safety limit: {path.name}")
        text = path.read_text(encoding="utf-8")
        findings = scanner.findings(text)
        if findings:
            raise ValueError(f"{path.name} contains: {', '.join(findings)}")

    expected: dict[str, str] = {}
    checksum_lines = (directory / "checksums.sha256").read_text().splitlines()
    for line_number, line in enumerate(checksum_lines, start=1):
        parts = line.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"checksums.sha256:{line_number}: malformed line")
        digest, filename = parts
        if not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"checksums.sha256:{line_number}: invalid SHA-256")
        if filename in expected:
            raise ValueError(f"checksums.sha256:{line_number}: duplicate filename")
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
    if not (manifest.run_id == completion.run_id == metadata.run_id):
        raise ValueError("release run IDs do not match")
    if metadata.git_commit != manifest.git_commit:
        raise ValueError("release Git commits do not match")
    if metadata.created_at != manifest.created_at.isoformat():
        raise ValueError("release creation timestamps do not match")
    if metadata.files != tuple(sorted(expected_files)):
        raise ValueError("release metadata file inventory does not match")
    if metadata.source_results_sha256 != completion.results_sha256:
        raise ValueError("release source result hashes do not match")
    if metadata.published_results_sha256 != sha256_file(directory / "results.jsonl.gz"):
        raise ValueError("published result hash does not match release metadata")
    if metadata.schema_version >= 2:
        audits_payload = json.loads(
            (directory / "execution-audits.json").read_text(encoding="utf-8")
        )
        audits = EXECUTION_AUDIT_LIST.validate_python(audits_payload)
        if any(item.run_id != manifest.run_id for item in audits):
            raise ValueError("execution audit run_id does not match manifest")
        identities = [
            (item.benchmark_id, item.deployment_id, item.lane, item.trial)
            for item in audits
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate execution audit identity")
    if metadata.schema_version >= 3:
        report_metadata = ReportMetadata.model_validate_json(
            (directory / "report-metadata.json").read_text(encoding="utf-8")
        )
        if report_metadata.run_id != manifest.run_id:
            raise ValueError("report metadata run_id does not match manifest")
        if report_metadata.run_git_commit != manifest.git_commit:
            raise ValueError("report metadata run Git commit does not match")
        if report_metadata.source_results_sha256 != completion.results_sha256:
            raise ValueError("report metadata result hash does not match completion")
        if (
            metadata.report_builder_git_commit
            != report_metadata.report_builder_git_commit
        ):
            raise ValueError("report builder Git commits do not match")
        if (
            metadata.baseline_registry_sha256
            != report_metadata.baseline_registry_sha256
        ):
            raise ValueError("baseline registry hashes do not match")
        figure_metadata = FigureMetadata.model_validate_json(
            (directory / "figure-metadata.json").read_text(encoding="utf-8")
        )
        if figure_metadata.run_id != manifest.run_id:
            raise ValueError("figure metadata run_id does not match manifest")
        if figure_metadata.source_metrics_sha256 != sha256_file(
            directory / "metrics.json"
        ):
            raise ValueError("figure source metric hash does not match")
        if (
            figure_metadata.figure_builder_git_commit
            != report_metadata.report_builder_git_commit
        ):
            raise ValueError("figure and report builder Git commits do not match")
        if (
            figure_metadata.baseline_registry_sha256
            != report_metadata.baseline_registry_sha256
        ):
            raise ValueError("figure and report baseline registry hashes do not match")
        for filename in FIGURE_FILES:
            if figure_metadata.files[filename] != sha256_file(directory / filename):
                raise ValueError(f"figure hash does not match: {filename}")
    result_count = 0
    status_counts: Counter[TaskStatus] = Counter()
    with gzip.open(
        directory / "results.jsonl.gz", mode="rt", encoding="utf-8"
    ) as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                result = TaskResult.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(
                    f"results.jsonl.gz:{line_number}: invalid TaskResult"
                ) from exc
            if result.run_id != manifest.run_id:
                raise ValueError("release result run_id does not match manifest")
            result_count += 1
            status_counts[result.status] += 1
    if result_count != completion.result_count:
        raise ValueError("release result count does not match completion")
    if dict(status_counts) != completion.status_counts:
        raise ValueError("release status counts do not match completion")
