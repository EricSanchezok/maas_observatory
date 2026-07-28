from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from tooluse_bench.config import PROJECT_ROOT, load_catalog, load_dotenv, resolve_models
from tooluse_bench.probe import CASES, run_case


def _list_models() -> int:
    models = load_catalog()
    headers = ("ALIAS", "MODEL ID", "FAMILY", "INPUT")
    rows = [
        (
            model.alias,
            model.model_id,
            model.family,
            ",".join(model.input_modalities),
        )
        for model in models
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return 0


def _validate() -> int:
    load_dotenv()
    failed = False
    for model in load_catalog():
        errors = model.configuration_errors()
        if errors:
            failed = True
            print(f"FAIL  {model.alias}: {'; '.join(errors)}")
        else:
            print(f"OK    {model.alias}: {model.model_id}")
    return 1 if failed else 0


def _probe(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        models = resolve_models(args.model, args.all)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    case_by_name = {case.name: case for case in CASES}
    selected_cases = CASES
    if args.case:
        unknown = sorted(set(args.case) - case_by_name.keys())
        if unknown:
            print(
                f"error: unknown case(s): {', '.join(unknown)}; "
                f"available: {', '.join(case_by_name)}",
                file=sys.stderr,
            )
            return 2
        selected_cases = [case_by_name[name] for name in args.case]

    config_errors = {
        model.alias: model.configuration_errors()
        for model in models
        if model.configuration_errors()
    }
    if config_errors:
        for alias, errors in config_errors.items():
            print(f"FAIL  {alias}: {'; '.join(errors)}", file=sys.stderr)
        return 2

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT / "results" / f"probe-{timestamp}.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, object]] = []
    with output.open("w", encoding="utf-8") as handle:
        for model in models:
            for case in selected_cases:
                result = run_case(model, case, args.timeout)
                results.append(result)
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(
                    f"{str(result['status']).upper():5} "
                    f"{model.alias:24} {case.name:30} "
                    f"{result['latency_seconds']}s"
                )

    passed = sum(result["status"] == "pass" for result in results)
    failed = sum(result["status"] == "fail" for result in results)
    error_count = sum(result["status"] == "error" for result in results)
    print(f"\npass={passed} fail={failed} error={error_count} output={output}")
    return 0 if failed == 0 and error_count == 0 else 1


def _bfcl(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        model = resolve_models([args.model], all_models=False)[0]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    errors = model.configuration_errors()
    if errors:
        print(f"FAIL  {model.alias}: {'; '.join(errors)}", file=sys.stderr)
        return 2

    try:
        from evalscope import TaskConfig, run_task
    except ImportError:
        print(
            "error: EvalScope is not installed; run: pip install -e '.[evalscope]'",
            file=sys.stderr,
        )
        return 2

    output = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT / "results" / "bfcl" / model.alias
    )
    output.mkdir(parents=True, exist_ok=True)
    extra_params: dict[str, object] = {"is_fc_model": True}
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    if serpapi_key:
        extra_params["SERPAPI_API_KEY"] = serpapi_key

    task = TaskConfig(
        model=model.model_id,
        api_url=model.base_url,
        api_key=model.api_key,
        eval_type="openai_api",
        datasets=["bfcl_v4"],
        eval_batch_size=args.batch_size,
        dataset_args={
            "bfcl_v4": {
                "subset_list": args.subset,
                "extra_params": extra_params,
            }
        },
        generation_config={"temperature": 0},
        use_cache=str(output / "cache"),
        limit=args.limit,
    )
    run_task(task_cfg=task)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tooluse-bench",
        description="Probe SII Holos OpenAI-compatible tool-calling endpoints.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list configured model aliases")
    subparsers.add_parser("validate", help="validate local model configuration")

    probe = subparsers.add_parser("probe", help="run a small native tool-calling probe")
    probe.add_argument(
        "--model", action="append", help="model alias; repeat to select several"
    )
    probe.add_argument(
        "--all", action="store_true", help="probe every configured model"
    )
    probe.add_argument(
        "--case", action="append", help="case name; repeat to select several"
    )
    probe.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    probe.add_argument("--output", help="JSONL output path")

    bfcl = subparsers.add_parser(
        "bfcl",
        help="run selected BFCL V4 subsets through EvalScope",
    )
    bfcl.add_argument("--model", required=True, help="one configured model alias")
    bfcl.add_argument(
        "--subset",
        action="append",
        default=None,
        help="BFCL V4 subset; repeat to select several",
    )
    bfcl.add_argument("--limit", type=int, default=10, help="examples per subset")
    bfcl.add_argument("--batch-size", type=int, default=1)
    bfcl.add_argument("--output", help="cache/output directory")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "list":
        code = _list_models()
    elif args.command == "validate":
        code = _validate()
    elif args.command == "probe":
        code = _probe(args)
    else:
        if args.subset is None:
            args.subset = [
                "simple_python",
                "multiple",
                "parallel",
                "parallel_multiple",
                "irrelevance",
                "multi_turn_base",
                "multi_turn_miss_func",
                "multi_turn_miss_param",
            ]
        code = _bfcl(args)
    raise SystemExit(code)
