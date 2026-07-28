from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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


def load_circuit_breaker() -> ModuleType:
    path = PROJECT_ROOT / "benchmark-envs" / "bfcl" / "circuit_breaker.py"
    spec = importlib.util.spec_from_file_location("bfcl_runtime_circuit_breaker", path)
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
    ("message", "traceback_text", "category"),
    [
        (
            "Connection error.",
            "openai.APIConnectionError caused by httpx.ConnectError",
            "transport",
        ),
        ("Request timed out.", "openai.APITimeoutError", "timeout"),
        ("Unexpected inference failure.", "RuntimeError", "infrastructure"),
    ],
)
def test_normalizer_separates_inference_errors_from_capability_failures(
    tmp_path: Path,
    message: str,
    traceback_text: str,
    category: str,
) -> None:
    module = load_normalizer()
    record = review_record(
        subset="simple_python",
        sample_id="simple_python_0",
        score=0,
    )
    record["sample_score"]["score"]["prediction"] = json.dumps(
        {"error": message, "error_message": traceback_text}
    )
    write_reviews(tmp_path, subset="simple_python", records=[record])

    [result] = module.normalize_outputs(tmp_path, ["simple_python"])

    assert result["score"] == 0
    assert result["error_category"] == category
    assert result["error_detail"] == message


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


def test_transport_circuit_breaker_ignores_model_scored_failures() -> None:
    module = load_circuit_breaker()
    records = [{"score": 0} for _ in range(100)]

    should_open, summary = module.should_open_transport_circuit(
        records,
        minimum_samples=50,
        failure_fraction=0.95,
    )

    assert should_open is False
    assert summary == {
        "sample_count": 100,
        "transport_failure_count": 0,
        "transport_failure_fraction": 0,
    }


def test_transport_circuit_breaker_opens_only_at_declared_thresholds() -> None:
    module = load_circuit_breaker()
    records = [
        {
            "score": 0,
            "error_category": "transport" if index < 48 else "timeout",
        }
        for index in range(50)
    ]

    should_open, summary = module.should_open_transport_circuit(
        records,
        minimum_samples=50,
        failure_fraction=0.95,
    )
    below_minimum, _ = module.should_open_transport_circuit(
        records[:49],
        minimum_samples=50,
        failure_fraction=0.95,
    )
    below_fraction, _ = module.should_open_transport_circuit(
        [*records[:47], {"score": 0}, {"score": 0}, {"score": 0}],
        minimum_samples=50,
        failure_fraction=0.95,
    )

    assert should_open is True
    assert below_minimum is False
    assert below_fraction is False
    assert summary["transport_failure_count"] == 50
    assert summary["transport_failure_fraction"] == 1


@pytest.mark.parametrize(
    ("minimum_samples", "failure_fraction", "message"),
    [
        (0, 0.95, "minimum_samples"),
        (50, 0, "failure_fraction"),
        (50, 1.01, "failure_fraction"),
    ],
)
def test_transport_circuit_breaker_validates_thresholds(
    minimum_samples: int,
    failure_fraction: float,
    message: str,
) -> None:
    module = load_circuit_breaker()
    with pytest.raises(ValueError, match=message):
        module.should_open_transport_circuit(
            [],
            minimum_samples=minimum_samples,
            failure_fraction=failure_fraction,
        )


def test_runtime_records_circuit_breaker_skips(tmp_path: Path) -> None:
    fake_modules = tmp_path / "fake-modules"
    handler_path = (
        fake_modules
        / "bfcl_eval"
        / "model_handler"
        / "api_inference"
        / "openai_completion.py"
    )
    handler_path.parent.mkdir(parents=True)
    for package in (
        fake_modules / "bfcl_eval",
        fake_modules / "bfcl_eval" / "model_handler",
        fake_modules / "bfcl_eval" / "model_handler" / "api_inference",
    ):
        (package / "__init__.py").write_text("", encoding="utf-8")
    handler_path.write_text(
        "\n".join(
            [
                "def _generate(self):",
                "    return None",
                "",
                "def _wrapped(self):",
                "    return _generate(self)",
                "",
                "_wrapped.__wrapped__ = _generate",
                "",
                "class OpenAICompletionsHandler:",
                "    generate_with_backoff = _wrapped",
                "",
                "    def _build_client_kwargs(self):",
                "        return {}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_modules / "evalscope.py").write_text(
        "\n".join(
            [
                "import json",
                "from pathlib import Path",
                "",
                "class TaskConfig:",
                "    def __init__(self, **kwargs):",
                "        self.kwargs = kwargs",
                "",
                "def run_task(*, task_cfg):",
                (
                    "    subset = task_cfg.kwargs['dataset_args']['bfcl_v4']"
                    "['subset_list'][0]"
                ),
                "    review = Path('cache/reviews/model') / f'bfcl_v4_{subset}.jsonl'",
                "    review.parent.mkdir(parents=True, exist_ok=True)",
                "    records = []",
                "    for index in range(50):",
                "        prediction = json.dumps({",
                "            'error': 'Connection error.',",
                "            'error_message': 'openai.APIConnectionError',",
                "        })",
                "        records.append({",
                "            'sample_score': {",
                "                'score': {",
                "                    'value': {'acc': 0},",
                "                    'prediction': prediction,",
                "                },",
                "                'sample_metadata': {",
                "                    'id': f'{subset}_{index}',",
                "                    'category': subset,",
                "                },",
                "            },",
                "        })",
                "    review.write_text(",
                "        '\\n'.join(json.dumps(item) for item in records) + '\\n',",
                "        encoding='utf-8',",
                "    )",
                "    return {'ok': True}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "batch_size": 1,
                "generation_config": {"seed": 1, "temperature": 0},
                "limit": None,
                "model_id": "fixture-model",
                "output_root": str(output_root),
                "request_timeout_seconds": 1,
                "sdk_max_retries": 0,
                "subsets": ["simple_python", "parallel", "irrelevance"],
                "transport_circuit_breaker_error_fraction": 0.95,
                "transport_circuit_breaker_min_samples": 50,
            }
        ),
        encoding="utf-8",
    )
    runtime = PROJECT_ROOT / "benchmark-envs" / "bfcl" / "run_eval.py"
    environment = {
        **os.environ,
        "PYTHONPATH": str(fake_modules),
        "SII_BENCH_API_KEY": "fixture",
        "SII_BENCH_BASE_URL": "https://fixture.invalid/v1",
    }

    completed = subprocess.run(
        [sys.executable, str(runtime), str(spec_path)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(
        (output_root / "adapter-summary.json").read_text(encoding="utf-8")
    )
    assert summary["completed_subsets"] == ["simple_python"]
    assert summary["skipped_subsets"] == ["parallel", "irrelevance"]
    assert summary["circuit_breaker"] == {
        "failure_fraction": 0.95,
        "minimum_samples": 50,
        "opened": True,
        "sample_count": 50,
        "transport_failure_count": 50,
        "transport_failure_fraction": 1,
        "trigger_subset": "simple_python",
    }
    errors = [
        json.loads(line)
        for line in (output_root / "adapter-errors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [error["subset"] for error in errors] == ["parallel", "irrelevance"]
    assert {error["error_type"] for error in errors} == {"TransportCircuitBreakerOpen"}
