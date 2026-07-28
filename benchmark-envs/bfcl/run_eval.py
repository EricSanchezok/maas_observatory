"""Isolated EvalScope BFCL runner.

Secrets are read from environment variables. The JSON spec is safe to retain as
private run provenance and never contains an endpoint or API key.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from evalscope import TaskConfig, run_task


def _walk_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_records(nested)


def _score(record: dict[str, Any]) -> float | None:
    for key in ("score", "accuracy", "acc", "correct", "pass"):
        value = record.get(key)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float) and 0 <= float(value) <= 1:
            return float(value)
    metrics = record.get("metrics")
    if isinstance(metrics, dict):
        return _score(metrics)
    return None


def _task_id(record: dict[str, Any], fallback: str) -> str:
    metadata = record.get("metadata")
    candidates = [
        record.get("task_id"),
        record.get("sample_id"),
        record.get("id"),
        metadata.get("id") if isinstance(metadata, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str | int):
            return str(value)
    return fallback


def _normalize_outputs(output_root: Path) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(output_root.rglob("*")):
        if not path.is_file() or path.name == "adapter-results.jsonl":
            continue
        payloads: list[Any] = []
        try:
            if path.suffix == ".jsonl":
                payloads.extend(
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            elif path.suffix == ".json":
                payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        for payload_index, payload in enumerate(payloads):
            for record_index, record in enumerate(_walk_records(payload)):
                score = _score(record)
                if score is None:
                    continue
                fallback = f"{path.stem}-{payload_index}-{record_index}"
                task_id = _task_id(record, fallback)
                identity = (str(path.relative_to(output_root)), task_id)
                if identity in seen:
                    continue
                seen.add(identity)
                normalized.append(
                    {
                        "task_id": task_id,
                        "score": score,
                        "source_path": str(path.relative_to(output_root)),
                        "record": record,
                    }
                )
    return normalized


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_eval.py SPEC.json")
    spec_path = Path(sys.argv[1]).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    output_root = Path(spec["output_root"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    api_url = os.environ.get("SII_BENCH_BASE_URL")
    api_key = os.environ.get("SII_BENCH_API_KEY")
    if not api_url or not api_key:
        raise SystemExit("SII_BENCH_BASE_URL and SII_BENCH_API_KEY are required")

    extra_params: dict[str, Any] = {"is_fc_model": True}
    if serpapi_key := os.environ.get("SERPAPI_API_KEY"):
        extra_params["SERPAPI_API_KEY"] = serpapi_key
    task = TaskConfig(
        model=spec["model_id"],
        api_url=api_url,
        api_key=api_key,
        eval_type="openai_api",
        datasets=["bfcl_v4"],
        eval_batch_size=spec["batch_size"],
        dataset_args={
            "bfcl_v4": {
                "subset_list": spec["subsets"],
                "extra_params": extra_params,
            }
        },
        generation_config=spec["generation_config"],
        use_cache=str(output_root / "cache"),
        limit=spec.get("limit"),
    )
    previous_directory = Path.cwd()
    os.chdir(output_root)
    try:
        returned = run_task(task_cfg=task)
    finally:
        os.chdir(previous_directory)
    (output_root / "run-return.txt").write_text(f"{returned!r}\n", encoding="utf-8")

    results = _normalize_outputs(output_root)
    result_path = output_root / "adapter-results.jsonl"
    with result_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "normalized_result_count": len(results),
        "subsets": spec["subsets"],
    }
    (output_root / "adapter-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if results else 3


if __name__ == "__main__":
    raise SystemExit(main())
