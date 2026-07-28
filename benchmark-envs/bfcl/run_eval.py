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

    results = normalize_outputs(output_root, spec["subsets"])
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
