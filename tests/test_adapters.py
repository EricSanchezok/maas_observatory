from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from tooluse_bench.benchmarks.bfcl import ALL_PUBLIC_SUBSETS, BFCLAdapter
from tooluse_bench.benchmarks.toolathlon import (
    TOOLATHLON_REVISION,
    TOOLATHLON_TASK_IMAGE,
    TOOLATHLON_TASK_IMAGE_DIGEST,
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


def test_bfcl_rejects_invalid_resource_and_retry_options() -> None:
    selection = BenchmarkSelection(
        benchmark_id="bfcl-v4",
        profile="smoke",
        trials=1,
        options={
            "batch_size": 0,
            "sdk_max_retries": 3,
            "request_timeout_seconds": 0,
            "transport_circuit_breaker_min_samples": 0,
            "transport_circuit_breaker_error_fraction": 1.1,
        },
    )
    issues = BFCLAdapter().validate(selection, load_catalog()[0])
    assert {issue.code for issue in issues} == {
        "invalid_batch_size",
        "invalid_sdk_max_retries",
        "invalid_request_timeout_seconds",
        "invalid_transport_circuit_breaker_min_samples",
        "invalid_transport_circuit_breaker_error_fraction",
    }
    assert all(issue.level == "error" for issue in issues)


def test_toolathlon_self_hosted_backend_requires_server() -> None:
    selection = BenchmarkSelection(
        benchmark_id="toolathlon-verified",
        profile="official",
        trials=3,
        options={"backend": "self-hosted"},
    )
    with patch.dict(os.environ, {}, clear=True):
        issues = ToolathlonAdapter().validate(selection, load_catalog()[0])
    assert [issue.code for issue in issues] == [
        "missing_toolathlon_server",
        "unpinned_toolathlon_server_revision",
        "unpinned_toolathlon_task_image",
        "invalid_toolathlon_attestation",
        "invalid_toolathlon_attestation",
    ]
    assert all(issue.level == "error" for issue in issues)

    pinned = selection.model_copy(
        update={
            "options": {
                "backend": "self-hosted",
                "server_revision": TOOLATHLON_REVISION,
                "task_image": TOOLATHLON_TASK_IMAGE,
            }
        }
    )
    with patch.dict(
        os.environ,
        {
            "TOOLATHLON_SERVER_HOST": "server.internal",
            "TOOLATHLON_SERVER_REVISION": TOOLATHLON_REVISION,
            "TOOLATHLON_TASK_IMAGE_DIGEST": TOOLATHLON_TASK_IMAGE_DIGEST,
        },
        clear=True,
    ):
        assert ToolathlonAdapter().validate(pinned, load_catalog()[0]) == ()


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
