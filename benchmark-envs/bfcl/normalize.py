"""Strictly normalize EvalScope BFCL per-sample review artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _object(value: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _sample_score(record: dict[str, Any], *, location: str) -> float:
    sample_score = _object(
        record.get("sample_score"), location=f"{location}.sample_score"
    )
    score = _object(
        sample_score.get("score"), location=f"{location}.sample_score.score"
    )
    value = _object(
        score.get("value"),
        location=f"{location}.sample_score.score.value",
    )
    accuracy = value.get("acc")
    if isinstance(accuracy, bool):
        return float(accuracy)
    if (
        not isinstance(accuracy, int | float)
        or not math.isfinite(float(accuracy))
        or not 0 <= float(accuracy) <= 1
    ):
        raise ValueError(f"{location} has an invalid acc score")
    return float(accuracy)


def _sample_id(
    record: dict[str, Any],
    *,
    subset: str,
    location: str,
) -> str:
    sample_score = _object(
        record.get("sample_score"),
        location=f"{location}.sample_score",
    )
    metadata = _object(
        sample_score.get("sample_metadata"),
        location=f"{location}.sample_score.sample_metadata",
    )
    category = metadata.get("category")
    if category != subset:
        raise ValueError(
            f"{location} category {category!r} does not match subset {subset!r}"
        )
    value = metadata.get("id")
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError(f"{location} has no stable sample metadata ID")
    return f"{subset}/{value}"


def normalize_outputs(
    output_root: Path,
    expected_subsets: list[str],
) -> list[dict[str, Any]]:
    """Read only per-sample reviews; reports and predictions are never scored."""

    review_root = output_root / "cache" / "reviews"
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for subset in expected_subsets:
        review_files = sorted(review_root.rglob(f"bfcl_v4_{subset}.jsonl"))
        if len(review_files) != 1:
            raise ValueError(
                f"expected one BFCL review file for {subset}, found {len(review_files)}"
            )
        path = review_files[0]
        subset_records: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            location = f"{path}:{line_number}"
            record = _object(json.loads(line), location=location)
            task_id = _sample_id(
                record,
                subset=subset,
                location=location,
            )
            if task_id in seen:
                raise ValueError(f"duplicate BFCL task identity: {task_id}")
            seen.add(task_id)
            subset_records.append(
                {
                    "task_id": task_id,
                    "score": _sample_score(record, location=location),
                    "source_path": str(path.relative_to(output_root)),
                    "record": record,
                }
            )
        if not subset_records:
            raise ValueError(f"BFCL review file is empty: {path}")
        normalized.extend(sorted(subset_records, key=lambda item: item["task_id"]))
    return normalized
