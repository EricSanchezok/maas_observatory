from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.publication import (
    EXPECTED_SNAPSHOT_FILES,
    PublicResultIndex,
    PublicSnapshotMetadata,
    render_public_results_markdown,
    validate_public_results,
)
from tooluse_bench.store import sha256_file

SOURCE_ROOT = PROJECT_ROOT / "public-results"


def copy_public_results(tmp_path: Path) -> Path:
    destination = tmp_path / "public-results"
    shutil.copytree(SOURCE_ROOT, destination)
    return destination


def snapshot_directory(root: Path) -> Path:
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    return root / index["snapshots"][0]["path"]


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_checksums(directory: Path) -> None:
    entries = [
        f"{sha256_file(directory / name)}  {name}"
        for name in sorted(EXPECTED_SNAPSHOT_FILES - {"checksums.sha256"})
    ]
    (directory / "checksums.sha256").write_text(
        "\n".join(entries) + "\n",
        encoding="utf-8",
    )


def mutate_json(directory: Path, filename: str, mutate: object) -> None:
    path = directory / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload.update(mutate)
    write_json(path, payload)
    refresh_checksums(directory)


def test_committed_public_results_are_valid_and_rendered() -> None:
    index, snapshots = validate_public_results()

    assert index.latest_run_id in snapshots
    assert render_public_results_markdown() == (
        PROJECT_ROOT / "docs" / "results.md"
    ).read_text(encoding="utf-8")


def test_public_models_reject_invalid_release_and_index_metadata() -> None:
    with pytest.raises(ValidationError, match="release_url"):
        PublicSnapshotMetadata.model_validate(
            {
                "run_id": "run",
                "title": "title",
                "status": "released",
                "archive_sha256": "a" * 64,
                "created_at": "2026-07-28T00:00:00Z",
            }
        )

    reference = {
        "run_id": "run",
        "path": "run",
        "title": "title",
        "status": "candidate",
    }
    with pytest.raises(ValidationError, match="run IDs"):
        PublicResultIndex(
            latest_run_id="run",
            snapshots=[reference, {**reference, "path": "other"}],
        )
    with pytest.raises(ValidationError, match="paths"):
        PublicResultIndex(
            latest_run_id="run",
            snapshots=[reference, {**reference, "run_id": "other"}],
        )
    with pytest.raises(ValidationError, match="latest_run_id"):
        PublicResultIndex(latest_run_id="missing", snapshots=[reference])

    released = PublicSnapshotMetadata.model_validate(
        {
            "run_id": "run",
            "title": "title",
            "status": "released",
            "release_url": "https://example.com/releases/run",
            "archive_sha256": "a" * 64,
            "created_at": "2026-07-28T00:00:00Z",
        }
    )
    assert released.release_url is not None


@pytest.mark.parametrize(
    ("filename", "mutation", "message"),
    [
        ("manifest.json", {"run_id": "other"}, "source release checksum"),
        (
            "release-metadata.json",
            {"git_commit": "f" * 40},
            "source release checksum",
        ),
        (
            "snapshot.json",
            {"created_at": "2026-07-29T00:00:00Z"},
            "timestamps",
        ),
        ("completion.json", {"result_count": 999}, "source release checksum"),
    ],
)
def test_semantic_tampering_is_rejected(
    tmp_path: Path,
    filename: str,
    mutation: object,
    message: str,
) -> None:
    root = copy_public_results(tmp_path)
    mutate_json(snapshot_directory(root), filename, mutation)

    with pytest.raises(ValueError, match=message):
        validate_public_results(root)


def test_metric_tampering_is_rejected(tmp_path: Path) -> None:
    root = copy_public_results(tmp_path)
    directory = snapshot_directory(root)
    metrics_path = directory / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics[0]["deployment_id"] = "unknown"
    write_json(metrics_path, metrics)
    refresh_checksums(directory)

    with pytest.raises(ValueError, match="faithful release-metric projection"):
        validate_public_results(root)

    metrics[0] = metrics[1]
    write_json(metrics_path, metrics)
    refresh_checksums(directory)
    with pytest.raises(ValueError, match="duplicate metric"):
        validate_public_results(root)


def test_structural_and_checksum_tampering_is_rejected(tmp_path: Path) -> None:
    root = copy_public_results(tmp_path)
    directory = snapshot_directory(root)
    (directory / "report.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksums"):
        validate_public_results(root)

    refresh_checksums(directory)
    (directory / "extra.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid public snapshot files"):
        validate_public_results(root)


def test_source_release_provenance_tampering_is_rejected(tmp_path: Path) -> None:
    root = copy_public_results(tmp_path)
    directory = snapshot_directory(root)
    (directory / "release-report.md").write_text("tampered\n", encoding="utf-8")
    refresh_checksums(directory)

    with pytest.raises(ValueError, match="source release checksum"):
        validate_public_results(root)


def test_malformed_and_incomplete_release_checksums_are_rejected(
    tmp_path: Path,
) -> None:
    root = copy_public_results(tmp_path)
    directory = snapshot_directory(root)
    release_checksums = directory / "release-checksums.sha256"
    release_checksums.write_text("malformed\n", encoding="utf-8")
    refresh_checksums(directory)
    with pytest.raises(ValueError, match="malformed checksum"):
        validate_public_results(root)

    source_lines = (
        (SOURCE_ROOT / directory.name / "release-checksums.sha256")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    release_checksums.write_text("\n".join(source_lines[1:]) + "\n", encoding="utf-8")
    refresh_checksums(directory)
    with pytest.raises(ValueError, match="source release checksum inventory"):
        validate_public_results(root)


def test_symlinked_public_results_root_is_rejected(tmp_path: Path) -> None:
    linked_root = tmp_path / "linked-results"
    linked_root.symlink_to(SOURCE_ROOT, target_is_directory=True)

    with pytest.raises(ValueError, match="not a real directory"):
        validate_public_results(linked_root)


def test_index_inventory_and_metadata_mismatches_are_rejected(
    tmp_path: Path,
) -> None:
    root = copy_public_results(tmp_path)
    (root / "extra.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"only index\.json"):
        validate_public_results(root)

    (root / "extra.txt").unlink()
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["snapshots"][0]["title"] = "wrong"
    write_json(index_path, index)
    with pytest.raises(ValueError, match="metadata"):
        validate_public_results(root)

    index["snapshots"][0]["title"] = json.loads(
        (snapshot_directory(root) / "snapshot.json").read_text(encoding="utf-8")
    )["title"]
    write_json(index_path, index)
    (root / "unreferenced").mkdir()
    with pytest.raises(ValueError, match="does not match snapshot directories"):
        validate_public_results(root)
