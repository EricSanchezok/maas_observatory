from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tooluse_bench.config import PROJECT_ROOT


def load_normalizer() -> ModuleType:
    path = PROJECT_ROOT / "benchmark-envs" / "bfcl" / "normalize.py"
    spec = importlib.util.spec_from_file_location("bfcl_runtime_normalize", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_record(
    *,
    subset: str,
    sample_id: str,
    score: float,
) -> dict:
    return {
        "index": int(sample_id.rsplit("_", 1)[-1]),
        "sample_score": {
            "score": {"value": {"acc": score}},
            "sample_metadata": {"id": sample_id, "category": subset},
        },
        "input": "fixture input",
        "target": "fixture target",
    }


def write_reviews(
    root: Path,
    *,
    subset: str,
    records: list[dict],
    model: str = "fixture-model",
) -> Path:
    path = root / "cache" / "reviews" / model / f"bfcl_v4_{subset}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def test_normalizer_reads_only_stable_per_sample_reviews(tmp_path: Path) -> None:
    module = load_normalizer()
    write_reviews(
        tmp_path,
        subset="simple_python",
        records=[
            review_record(
                subset="simple_python",
                sample_id="simple_python_1",
                score=0,
            ),
            review_record(
                subset="simple_python",
                sample_id="simple_python_0",
                score=1,
            ),
        ],
    )
    report = tmp_path / "cache" / "reports" / "fixture-model" / "bfcl_v4.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"score": 0.5, "name": "aggregate"}\n', encoding="utf-8")

    results = module.normalize_outputs(tmp_path, ["simple_python"])

    assert [item["task_id"] for item in results] == [
        "simple_python/simple_python_0",
        "simple_python/simple_python_1",
    ]
    assert [item["score"] for item in results] == [1.0, 0.0]
    assert all("reviews/" in item["source_path"] for item in results)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda record: record["sample_score"]["sample_metadata"].update(
                {"category": "wrong"}
            ),
            "does not match subset",
        ),
        (
            lambda record: record["sample_score"]["score"]["value"].update({"acc": 2}),
            "invalid acc score",
        ),
    ],
)
def test_normalizer_rejects_malformed_reviews(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    module = load_normalizer()
    record = review_record(
        subset="parallel",
        sample_id="parallel_0",
        score=1,
    )
    mutate(record)
    write_reviews(tmp_path, subset="parallel", records=[record])

    with pytest.raises(ValueError, match=message):
        module.normalize_outputs(tmp_path, ["parallel"])


def test_normalizer_requires_one_nonempty_file_per_subset(tmp_path: Path) -> None:
    module = load_normalizer()
    with pytest.raises(ValueError, match="found 0"):
        module.normalize_outputs(tmp_path, ["irrelevance"])

    write_reviews(tmp_path, subset="irrelevance", records=[])
    with pytest.raises(ValueError, match="empty"):
        module.normalize_outputs(tmp_path, ["irrelevance"])

    write_reviews(
        tmp_path,
        subset="irrelevance",
        records=[
            review_record(
                subset="irrelevance",
                sample_id="irrelevance_0",
                score=1,
            )
        ],
        model="second-model",
    )
    with pytest.raises(ValueError, match="found 2"):
        module.normalize_outputs(tmp_path, ["irrelevance"])
