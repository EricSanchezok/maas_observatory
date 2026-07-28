"""Isolated EvalScope BFCL runner.

Secrets are read from environment variables. The JSON spec is safe to retain as
private run provenance and never contains an endpoint or API key.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from evalscope import TaskConfig, run_task
from normalize import normalize_outputs


def _task_config(
    spec: dict[str, Any],
    *,
    subset: str,
    subset_root: Path,
) -> TaskConfig:
    api_url = os.environ.get("SII_BENCH_BASE_URL")
    api_key = os.environ.get("SII_BENCH_API_KEY")
    if not api_url or not api_key:
        raise SystemExit("SII_BENCH_BASE_URL and SII_BENCH_API_KEY are required")

    extra_params: dict[str, Any] = {"is_fc_model": True}
    if serpapi_key := os.environ.get("SERPAPI_API_KEY"):
        extra_params["SERPAPI_API_KEY"] = serpapi_key
    return TaskConfig(
        model=spec["model_id"],
        api_url=api_url,
        api_key=api_key,
        eval_type="openai_api",
        datasets=["bfcl_v4"],
        eval_batch_size=spec["batch_size"],
        dataset_args={
            "bfcl_v4": {
                "subset_list": [subset],
                "extra_params": extra_params,
            }
        },
        generation_config=spec["generation_config"],
        use_cache=str(subset_root / "cache"),
        limit=spec.get("limit"),
    )


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_eval.py SPEC.json")
    spec_path = Path(sys.argv[1]).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    output_root = Path(spec["output_root"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    result_path = output_root / "adapter-results.jsonl"
    error_path = output_root / "adapter-errors.jsonl"
    result_path.write_text("", encoding="utf-8")
    error_path.write_text("", encoding="utf-8")

    previous_directory = Path.cwd()
    completed_subsets: list[str] = []
    failed_subsets: list[str] = []
    normalized_result_count = 0
    for subset in spec["subsets"]:
        subset_root = output_root / "subsets" / subset
        subset_root.mkdir(parents=True, exist_ok=True)
        task = _task_config(spec, subset=subset, subset_root=subset_root)
        try:
            os.chdir(subset_root)
            returned = run_task(task_cfg=task)
            (subset_root / "run-return.txt").write_text(
                f"{returned!r}\n",
                encoding="utf-8",
            )
            results = normalize_outputs(subset_root, [subset])
        except Exception as exc:
            failed_subsets.append(subset)
            _append_jsonl(
                error_path,
                [
                    {
                        "subset": subset,
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:4000],
                    }
                ],
            )
        else:
            completed_subsets.append(subset)
            normalized_result_count += len(results)
            for result in results:
                result["source_path"] = str(
                    (subset_root / result["source_path"]).relative_to(output_root)
                )
            _append_jsonl(result_path, results)
        finally:
            os.chdir(previous_directory)

    summary = {
        "completed_subsets": completed_subsets,
        "failed_subsets": failed_subsets,
        "normalized_result_count": normalized_result_count,
        "subsets": spec["subsets"],
    }
    (output_root / "adapter-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if completed_subsets else 3


if __name__ == "__main__":
    raise SystemExit(main())
