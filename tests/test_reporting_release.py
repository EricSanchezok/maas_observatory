from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from tooluse_bench.baselines import (
    BaselineRecord,
    BaselineRegistry,
    BaselineSourceKind,
    Comparability,
)
from tooluse_bench.config import load_model_catalog
from tooluse_bench.domain import Lane
from tooluse_bench.records import (
    BenchmarkMetadata,
    ErrorCategory,
    ExecutionAudit,
    RunManifest,
    RunSpec,
    TaskStatus,
    result_from_spec,
)
from tooluse_bench.redaction import Redactor
from tooluse_bench.release import (
    build_release,
    release_file_inventory,
    validate_release,
)
from tooluse_bench.reporting import aggregate_results, build_report, load_results
from tooluse_bench.store import RunStore, sha256_file

RUN_ID = "run-report-test"
DEPLOYMENT_ID = "sii-holos-deepseek-v4-pro"


def baseline_registry(
    *,
    comparability: Comparability = Comparability.CONTEXTUAL,
) -> BaselineRegistry:
    compatible = (DEPLOYMENT_ID,) if comparability is Comparability.EXACT else ()
    return BaselineRegistry(
        schema_version=1,
        baselines=[
            BaselineRecord(
                baseline_id=f"test-baseline-{comparability.value}",
                upstream_model="DeepSeek V4 Pro",
                benchmark_id="probe",
                benchmark_release="1.0.0",
                metric="pass_at_1",
                score=50,
                precision="W8A8",
                reasoning_mode="default",
                source_kind=BaselineSourceKind.VENDOR_REPORT,
                source_url="https://example.com/report",
                accessed_at=date(2026, 7, 28),
                comparability=comparability,
                compatible_deployments=compatible,
            )
        ],
    )


def make_manifest(run_directory: Path) -> RunManifest:
    return RunManifest(
        run_id=RUN_ID,
        experiment_id="report-test",
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        git_commit="a" * 40,
        git_dirty=False,
        package_version="0.2.0",
        python_version="3.12.0",
        platform="test",
        configuration_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        catalog_path="config/models.yaml",
        experiment_path="config/experiments/test.yaml",
        output_directory=run_directory,
        benchmarks=(
            BenchmarkMetadata(
                benchmark_id="probe",
                display_name="Protocol probe",
                version="1.0.0",
                source_url="https://example.com/probe",
                revision="1",
                hermetic_default=True,
                supported_profiles=("full",),
            ),
        ),
        selected_deployments=(DEPLOYMENT_ID,),
        lanes=(Lane.STANDARDIZED,),
    )


def make_result(
    *,
    task_id: str,
    trial: int,
    status: TaskStatus,
    secret: str = "",
):
    spec = RunSpec(
        run_id=RUN_ID,
        experiment_id="report-test",
        benchmark_id="probe",
        benchmark_version="1.0.0",
        profile="full",
        lane=Lane.STANDARDIZED,
        deployment_id=DEPLOYMENT_ID,
        model_alias="deepseek-v4-pro",
        trial=trial,
        seed=trial,
    )
    now = datetime(2026, 7, 28, tzinfo=UTC)
    return result_from_spec(
        spec,
        task_id=task_id,
        status=status,
        score=float(status is TaskStatus.PASS),
        metrics={"turns": 1, "tool_calls": 1},
        started_at=now,
        finished_at=now,
        latency_seconds=float(trial),
        request={"authorization": secret} if secret else None,
        response={"content": f"trace {secret}"} if secret else None,
        usage={"total_tokens": 10 * trial},
        error_category=(
            ErrorCategory.PROTOCOL if status is TaskStatus.ERROR else ErrorCategory.NONE
        ),
        error_detail=f"failed with {secret}" if secret else None,
    )


def create_completed_run(run_directory: Path, *, secret: str = "") -> None:
    store = RunStore.create(run_directory, make_manifest(run_directory))
    audit = ExecutionAudit(
        run_id=RUN_ID,
        benchmark_id="probe",
        deployment_id=DEPLOYMENT_ID,
        lane=Lane.STANDARDIZED,
        trial=1,
        started_at=datetime(2026, 7, 28, tzinfo=UTC),
        finished_at=datetime(2026, 7, 28, tzinfo=UTC),
        resource_controls={"max_retries": 2, "request_timeout_seconds": 90},
        observations={"note": secret or "no retries", "observed_retry_count": 0},
    )
    audit_path = run_directory / "artifacts" / "probe" / "execution-audit.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        json.dumps(audit.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    statuses = {
        "task-a": (TaskStatus.PASS, TaskStatus.FAIL, TaskStatus.PASS),
        "task-b": (TaskStatus.ERROR, TaskStatus.FAIL, TaskStatus.FAIL),
    }
    for task_id, task_statuses in statuses.items():
        for trial, status in enumerate(task_statuses, start=1):
            store.append(
                make_result(
                    task_id=task_id,
                    trial=trial,
                    status=status,
                    secret=secret,
                )
            )
    store.finalize()


def test_aggregate_statistics_are_deterministic_and_exact_delta_is_guarded() -> None:
    results = [
        make_result(task_id="task-a", trial=1, status=TaskStatus.PASS),
        make_result(task_id="task-a", trial=2, status=TaskStatus.FAIL),
        make_result(task_id="task-a", trial=3, status=TaskStatus.PASS),
        make_result(task_id="task-b", trial=1, status=TaskStatus.FAIL),
        make_result(task_id="task-b", trial=2, status=TaskStatus.FAIL),
        make_result(task_id="task-b", trial=3, status=TaskStatus.FAIL),
    ]
    contextual = aggregate_results(results, baselines=baseline_registry())[0]
    exact_first = aggregate_results(
        results, baselines=baseline_registry(comparability=Comparability.EXACT)
    )[0]
    exact_second = aggregate_results(
        results, baselines=baseline_registry(comparability=Comparability.EXACT)
    )[0]

    assert contextual.pass_at_1 == pytest.approx(1 / 3)
    assert contextual.pass_at_3 == 0.5
    assert contextual.pass_pow_3 == 0
    assert contextual.official_delta is None
    assert exact_first.official_delta == pytest.approx(-1 / 6)
    assert exact_first.pass_at_1_ci_low == exact_second.pass_at_1_ci_low
    assert exact_first.pass_at_1_ci_high == exact_second.pass_at_1_ci_high

    one_trial = aggregate_results(
        [make_result(task_id="task-a", trial=1, status=TaskStatus.PASS)],
        baselines=baseline_registry(),
    )[0]
    assert one_trial.expected_trials == 1
    assert one_trial.pass_at_3 is None
    assert one_trial.pass_pow_3 is None


def test_baseline_registry_rejects_false_exact_and_duplicate_ids() -> None:
    exact_record = (
        baseline_registry(comparability=Comparability.EXACT).baselines[0].model_dump()
    )
    exact_record["compatible_deployments"] = ()
    with pytest.raises(ValidationError, match="compatible deployment"):
        BaselineRecord.model_validate(exact_record)

    record = baseline_registry().baselines[0]
    with pytest.raises(ValidationError, match="duplicate baseline"):
        BaselineRegistry(schema_version=1, baselines=[record, record])


def test_report_and_release_are_valid_sanitized_and_deterministic(
    tmp_path: Path,
) -> None:
    secret = "sk-" + "private-value-123456789"
    endpoint = "https://" + "private-endpoint.invalid/v1"
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory, secret=secret)
    report_directory = build_report(
        run_directory,
        baselines=baseline_registry(),
    )
    assert (
        "No cross-benchmark composite" in (report_directory / "report.md").read_text()
    )
    assert len(load_results(run_directory / "results.jsonl")) == 6

    deployment = load_model_catalog().deployments[0]
    environment = {
        deployment.endpoint.api_key_env: secret,
        deployment.endpoint.base_url_env: endpoint,
    }
    with patch.dict("os.environ", environment, clear=False):
        release_one, archive_one = build_release(
            run_directory,
            output_root=tmp_path / "release-one",
        )
        release_two, archive_two = build_release(
            run_directory,
            output_root=tmp_path / "release-two",
        )
        validate_release(release_one)

    assert sha256_file(archive_one) == sha256_file(archive_two)
    assert {path.name for path in release_one.iterdir() if path.is_file()} == {
        path.name for path in release_two.iterdir() if path.is_file()
    }
    released_results = gzip.decompress(
        (release_one / "results.jsonl.gz").read_bytes()
    ).decode()
    assert secret not in released_results
    assert "private-endpoint.invalid" not in released_results
    assert "[REDACTED]" in released_results
    released_audits = (release_one / "execution-audits.json").read_text()
    assert secret not in released_audits
    assert "[REDACTED]" in released_audits
    assert json.loads(released_audits)[0]["resource_controls"] == {
        "max_retries": 2,
        "request_timeout_seconds": 90,
    }

    metadata = json.loads((release_one / "release-metadata.json").read_text())
    assert metadata["schema_version"] == 2
    assert metadata["source_results_sha256"] != metadata["published_results_sha256"]


def test_release_rejects_execution_audit_with_wrong_run_id(tmp_path: Path) -> None:
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory)
    audit_path = next(run_directory.glob("artifacts/**/execution-audit.json"))
    payload = json.loads(audit_path.read_text())
    payload["run_id"] = "wrong-run"
    audit_path.write_text(json.dumps(payload), encoding="utf-8")
    build_report(run_directory, baselines=baseline_registry())
    with pytest.raises(ValueError, match="execution audit run_id"):
        build_release(run_directory, output_root=tmp_path / "release")


def test_release_validation_detects_tampering(tmp_path: Path) -> None:
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory)
    build_report(run_directory, baselines=baseline_registry())
    release_directory, _ = build_release(
        run_directory,
        output_root=tmp_path / "release",
    )
    (release_directory / "report.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksums"):
        validate_release(release_directory)


def test_release_file_inventory_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="unsupported release schema version"):
        release_file_inventory(99)


def test_report_rejects_incomplete_and_tampered_runs(tmp_path: Path) -> None:
    incomplete = tmp_path / "incomplete" / RUN_ID
    create_completed_run(incomplete)
    (incomplete / "completion.json").unlink()
    with pytest.raises(ValueError, match="run is incomplete"):
        build_report(incomplete, baselines=baseline_registry())

    tampered = tmp_path / "tampered" / RUN_ID
    create_completed_run(tampered)
    with (tampered / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(ValueError, match=r"invalid TaskResult|checksum"):
        build_report(tampered, baselines=baseline_registry())


def test_release_validation_rejects_malformed_checksums(tmp_path: Path) -> None:
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory)
    build_report(run_directory, baselines=baseline_registry())
    release_directory, _ = build_release(
        run_directory,
        output_root=tmp_path / "release",
    )
    checksum_path = release_directory / "checksums.sha256"
    checksum_path.write_text("not-a-checksum\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed line"):
        validate_release(release_directory)


@given(st.text(min_size=1).filter(lambda value: "\x00" not in value))
def test_redactor_recursively_removes_values_under_sensitive_keys(secret: str) -> None:
    redactor = Redactor((("catalog-secret", "[REDACTED]"),))
    payload = {
        "authorization": secret,
        "nested": [{"api_key": secret}, {"safe": "catalog-secret"}],
    }
    sanitized = redactor.value(payload)
    rendered = json.dumps(sanitized, ensure_ascii=False)
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["nested"][0]["api_key"] == "[REDACTED]"
    assert "catalog-secret" not in rendered


def test_redactor_detects_known_leak_patterns() -> None:
    redactor = Redactor((("known-secret", "[REDACTED]"),))
    text = (
        "Bearer " + "abcdefghijklmnop sk-" + "abcdefghijklmnop "
        "/Users/eric/private known-secret"
    )
    assert redactor.findings(text) == [
        "absolute user path",
        "bearer token",
        "common API key",
        "known private environment value",
    ]
    assert redactor.findings(redactor.text(text)) == []
