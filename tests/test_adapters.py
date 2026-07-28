from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from tooluse_bench.benchmarks.bfcl import ALL_PUBLIC_SUBSETS, BFCLAdapter
from tooluse_bench.benchmarks.toolathlon import (
    ToolathlonAdapter,
    _find_eval_results,
)
from tooluse_bench.config import load_catalog
from tooluse_bench.domain import BenchmarkSelection


def test_bfcl_full_profile_covers_all_22_subsets() -> None:
    assert len(ALL_PUBLIC_SUBSETS) == 22
    assert len(set(ALL_PUBLIC_SUBSETS)) == 22
    assert "web_search_base" in ALL_PUBLIC_SUBSETS
    assert "memory_vector" in ALL_PUBLIC_SUBSETS


def test_bfcl_warns_when_full_profile_lacks_serpapi() -> None:
    selection = BenchmarkSelection(
        benchmark_id="bfcl-v4",
        profile="full-public",
        trials=3,
    )
    with patch.dict(os.environ, {}, clear=True):
        issues = BFCLAdapter().validate(selection, load_catalog()[0])
    assert [issue.code for issue in issues] == ["missing_serpapi_key"]
    assert issues[0].level == "warning"


def test_toolathlon_self_hosted_backend_requires_server() -> None:
    selection = BenchmarkSelection(
        benchmark_id="toolathlon-verified",
        profile="official",
        trials=3,
        options={"backend": "self-hosted"},
    )
    with patch.dict(os.environ, {}, clear=True):
        issues = ToolathlonAdapter().validate(selection, load_catalog()[0])
    assert [issue.code for issue in issues] == ["missing_toolathlon_server"]
    assert issues[0].level == "error"


def test_toolathlon_collector_reads_only_valid_eval_results(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "finalpool" / "task-a" / "eval_res.json"
    valid.parent.mkdir(parents=True)
    valid.write_text(json.dumps({"pass": True}), encoding="utf-8")
    invalid = tmp_path / "finalpool" / "task-b" / "eval_res.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("not-json", encoding="utf-8")

    results = _find_eval_results(tmp_path)
    assert results == [(valid, {"pass": True})]
