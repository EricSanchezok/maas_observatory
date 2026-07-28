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

from circuit_breaker import should_open_transport_circuit
from evalscope import TaskConfig, run_task
from normalize import normalize_outputs


def _configure_bounded_bfcl_transport(spec: dict[str, Any]) -> None:
    """Bound the pinned BFCL handler's otherwise unbounded retry policy."""

    from bfcl_eval.model_handler.api_inference.openai_completion import (
        OpenAICompletionsHandler,
    )

    original_build_client_kwargs = OpenAICompletionsHandler._build_client_kwargs
    original_generate = getattr(
        OpenAICompletionsHandler.generate_with_backoff,
        "__wrapped__",
        None,
    )
    if original_generate is None:
        raise RuntimeError("pinned BFCL retry wrapper is not inspectable")

    def bounded_client_kwargs(self: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = original_build_client_kwargs(self)
        kwargs["max_retries"] = spec["sdk_max_retries"]
        kwargs["timeout"] = spec["request_timeout_seconds"]
        return kwargs

    OpenAICompletionsHandler._build_client_kwargs = bounded_client_kwargs
    # The upstream handler wraps RateLimitError in a stop-never Tenacity policy.
    # The OpenAI SDK already retries rate limits and transport failures, so use
    # the unwrapped method and enforce the explicit SDK maximum above.
    OpenAICompletionsHandler.generate_with_backoff = original_generate


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
        model_args={"max_retries": spec["sdk_max_retries"]},
        eval_type="openai_api",
        datasets=["bfcl_v4"],
        eval_batch_size=spec["batch_size"],
        dataset_args={
            "bfcl_v4": {
                "subset_list": [subset],
                "extra_params": extra_params,
            }
        },
        generation_config={
            **spec["generation_config"],
            "timeout": spec["request_timeout_seconds"],
        },
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
    _configure_bounded_bfcl_transport(spec)
    output_root = Path(spec["output_root"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    result_path = output_root / "adapter-results.jsonl"
    error_path = output_root / "adapter-errors.jsonl"
    result_path.write_text("", encoding="utf-8")
    error_path.write_text("", encoding="utf-8")

    previous_directory = Path.cwd()
    completed_subsets: list[str] = []
    failed_subsets: list[str] = []
    skipped_subsets: list[str] = []
    normalized_result_count = 0
    circuit_breaker: dict[str, Any] = {
        "opened": False,
        "minimum_samples": spec["transport_circuit_breaker_min_samples"],
        "failure_fraction": spec["transport_circuit_breaker_error_fraction"],
    }
    for subset_index, subset in enumerate(spec["subsets"]):
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
            should_open, failure_summary = should_open_transport_circuit(
                results,
                minimum_samples=spec["transport_circuit_breaker_min_samples"],
                failure_fraction=spec["transport_circuit_breaker_error_fraction"],
            )
            if should_open:
                skipped_subsets = list(spec["subsets"][subset_index + 1 :])
                circuit_breaker.update(
                    {
                        "opened": True,
                        "trigger_subset": subset,
                        **failure_summary,
                    }
                )
                _append_jsonl(
                    error_path,
                    [
                        {
                            "subset": skipped_subset,
                            "error_type": "TransportCircuitBreakerOpen",
                            "error_detail": (
                                "Skipped after endpoint transport/timeout failures "
                                f"reached {failure_summary['transport_failure_count']}/"
                                f"{failure_summary['sample_count']} in {subset}; "
                                "no capability score was assigned"
                            ),
                        }
                        for skipped_subset in skipped_subsets
                    ],
                )
                break
        finally:
            os.chdir(previous_directory)

    summary = {
        "circuit_breaker": circuit_breaker,
        "completed_subsets": completed_subsets,
        "failed_subsets": failed_subsets,
        "normalized_result_count": normalized_result_count,
        "skipped_subsets": skipped_subsets,
        "subsets": spec["subsets"],
    }
    (output_root / "adapter-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if completed_subsets else 3


if __name__ == "__main__":
    raise SystemExit(main())
