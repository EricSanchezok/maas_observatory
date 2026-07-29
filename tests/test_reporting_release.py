from __future__ import annotations

import gzip
import json
import shutil
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
from tooluse_bench.config import PROJECT_ROOT, load_model_catalog
from tooluse_bench.domain import Lane
from tooluse_bench.publication import (
    build_public_snapshot,
    validate_public_results,
    write_public_results_markdown,
)
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
    RELEASE_FILES_V1,
    RELEASE_FILES_V2,
    RELEASE_FILES_V3,
    ReleaseMetadata,
    build_release,
    release_file_inventory,
    validate_release,
    validate_release_archive,
)
from tooluse_bench.reporting import aggregate_results, build_report, load_results
from tooluse_bench.store import RunStore, sha256_file
from tooluse_bench.visualization import FigureMetadata

RUN_ID = "run-report-test"
DEPLOYMENT_ID = "maas-deepseek-v4-pro-w4a8"


def baseline_registry(
    *,
    comparability: Comparability = Comparability.CONTEXTUAL,
) -> BaselineRegistry:
    compatible = (DEPLOYMENT_ID,) if comparability is Comparability.EXACT else ()
    compatible_configurations = (
        ("b" * 64,) if comparability is Comparability.EXACT else ()
    )
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
                compatible_configurations_sha256=compatible_configurations,
                settings=(
                    {"agent": "fixture", "reasoning_mode": "default"}
                    if comparability is Comparability.EXACT
                    else {}
                ),
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
    lane: Lane = Lane.STANDARDIZED,
):
    spec = RunSpec(
        run_id=RUN_ID,
        experiment_id="report-test",
        benchmark_id="probe",
        benchmark_version="1.0.0",
        profile="full",
        lane=lane,
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
        [
            item.model_copy(update={"lane": Lane.OFFICIAL_REPRODUCTION})
            for item in results
        ],
        baselines=baseline_registry(comparability=Comparability.EXACT),
        configuration_sha256="b" * 64,
    )[0]
    exact_second = aggregate_results(
        [
            item.model_copy(update={"lane": Lane.OFFICIAL_REPRODUCTION})
            for item in results
        ],
        baselines=baseline_registry(comparability=Comparability.EXACT),
        configuration_sha256="b" * 64,
    )[0]

    assert contextual.pass_at_1 == pytest.approx(1 / 3)
    assert contextual.pass_at_3 == 0.5
    assert contextual.pass_pow_3 == 0
    assert contextual.official_delta is None
    assert exact_first.official_delta == pytest.approx(-1 / 6)
    assert exact_first.pass_at_1_ci_low == exact_second.pass_at_1_ci_low
    assert exact_first.pass_at_1_ci_high == exact_second.pass_at_1_ci_high
    wrong_configuration = aggregate_results(
        [
            item.model_copy(update={"lane": Lane.OFFICIAL_REPRODUCTION})
            for item in results
        ],
        baselines=baseline_registry(comparability=Comparability.EXACT),
        configuration_sha256="f" * 64,
    )[0]
    assert wrong_configuration.official_delta is None

    one_trial = aggregate_results(
        [make_result(task_id="task-a", trial=1, status=TaskStatus.PASS)],
        baselines=baseline_registry(),
    )[0]
    assert one_trial.expected_trials == 1
    assert one_trial.pass_at_3 is None
    assert one_trial.pass_pow_3 is None


def test_bfcl_subset_metrics_preserve_partial_coverage() -> None:
    spec = RunSpec(
        run_id=RUN_ID,
        experiment_id="report-test",
        benchmark_id="bfcl-v4",
        benchmark_version="bfcl-test",
        profile="full-public",
        lane=Lane.STANDARDIZED,
        deployment_id=DEPLOYMENT_ID,
        model_alias="deepseek-v4-pro",
        trial=1,
        seed=1,
    )
    now = datetime(2026, 7, 28, tzinfo=UTC)

    def item(
        task_id: str,
        status: TaskStatus,
        category: ErrorCategory = ErrorCategory.NONE,
    ):
        return result_from_spec(
            spec,
            task_id=task_id,
            status=status,
            score=(
                float(status is TaskStatus.PASS)
                if status in {TaskStatus.PASS, TaskStatus.FAIL}
                else None
            ),
            started_at=now,
            finished_at=now,
            latency_seconds=None,
            error_category=category,
        )

    [aggregate] = aggregate_results(
        [
            item("simple_python/0", TaskStatus.PASS),
            item("simple_python/1", TaskStatus.FAIL),
            item("parallel/0", TaskStatus.ERROR, ErrorCategory.TIMEOUT),
            item(
                "__subset__/web_search_base",
                TaskStatus.ERROR,
                ErrorCategory.INFRASTRUCTURE,
            ),
        ],
        baselines=baseline_registry(),
    )

    assert aggregate.complete is False
    assert aggregate.task_count == 3
    assert aggregate.pass_at_1 == pytest.approx(1 / 3)
    groups = {group.group_id: group for group in aggregate.task_groups}
    assert groups["simple_python"].pass_at_1 == 0.5
    assert groups["simple_python"].error_categories == {"arguments": 1}
    assert groups["parallel"].error_rate == 1
    assert groups["web_search_base"].complete is False
    assert groups["web_search_base"].task_count == 0


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
    assert (
        (report_directory / "benchmark-overview.png")
        .read_bytes()
        .startswith(b"\x89PNG\r\n\x1a\n")
    )
    figure_metadata = FigureMetadata.model_validate_json(
        (report_directory / "figure-metadata.json").read_text(encoding="utf-8")
    )
    assert figure_metadata.source_metrics_sha256 == sha256_file(
        report_directory / "metrics.json"
    )
    figure_svg = (report_directory / "benchmark-overview.svg").read_text(
        encoding="utf-8"
    )
    assert "official ◇ 50.0" in figure_svg
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
    assert metadata["schema_version"] == 3
    assert metadata["report_builder_git_commit"]
    assert metadata["release_builder_git_commit"]
    assert metadata["baseline_registry_sha256"]
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


def test_release_build_and_archive_require_real_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not release-ready"):
        build_release(tmp_path / "incomplete", output_root=tmp_path / "release")
    with pytest.raises(ValueError, match="regular file"):
        validate_release_archive(tmp_path, tmp_path / "missing.tar.gz")


def test_public_snapshot_builder_derives_from_validated_release(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory)
    build_report(run_directory, baselines=baseline_registry())
    release_directory, archive = build_release(
        run_directory,
        output_root=tmp_path / "release",
    )
    public_root = tmp_path / "public-results"

    snapshot = build_public_snapshot(
        release_directory,
        archive,
        title="Fixture release candidate",
        root=public_root,
    )
    index, snapshots = validate_public_results(public_root)
    assert snapshot.name == RUN_ID
    assert index.latest_run_id == RUN_ID
    assert RUN_ID in snapshots
    results_page = write_public_results_markdown(
        public_root,
        destination=tmp_path / "results.md",
    )
    assert "Fixture release candidate" in results_page.read_text()
    assert "### Provenance" in results_page.read_text()

    with pytest.raises(ValueError, match="already exists"):
        build_public_snapshot(
            release_directory,
            archive,
            title="Duplicate",
            root=public_root,
        )

    invalid_archive = tmp_path / "invalid.tar.gz"
    invalid_archive.write_bytes(b"not a gzip tar")
    with pytest.raises(ValueError, match="valid gzip tar"):
        validate_release_archive(release_directory, invalid_archive)


def test_public_snapshot_can_initialize_an_empty_index(tmp_path: Path) -> None:
    run_directory = tmp_path / "private" / RUN_ID
    create_completed_run(run_directory)
    build_report(run_directory, baselines=baseline_registry())
    release_directory, archive = build_release(
        run_directory,
        output_root=tmp_path / "release",
    )
    public_root = tmp_path / "public-results"
    shutil.copytree(PROJECT_ROOT / "public-results", public_root)
    previous_index, _ = validate_public_results(public_root)
    assert previous_index.latest_run_id is None

    build_public_snapshot(
        release_directory,
        archive,
        title="Additional released result",
        status="released",
        release_url="https://example.com/releases/run-report-test",
        notes=("Immutable release archive.",),
        root=public_root,
        make_latest=False,
    )

    index, snapshots = validate_public_results(public_root)
    assert index.latest_run_id == RUN_ID
    assert snapshots[RUN_ID][0].release_url is not None


def test_release_file_inventory_rejects_unknown_schema_version() -> None:
    assert release_file_inventory(1) == RELEASE_FILES_V1
    assert release_file_inventory(2) == RELEASE_FILES_V2
    assert release_file_inventory(3) == RELEASE_FILES_V3
    with pytest.raises(ValueError, match="unsupported release schema version"):
        release_file_inventory(99)

    with pytest.raises(ValidationError, match="file inventory"):
        ReleaseMetadata(
            schema_version=1,
            run_id="run",
            git_commit="a" * 40,
            created_at="2026-07-28T00:00:00+00:00",
            files=("wrong",),
            source_results_sha256="b" * 64,
            published_results_sha256="c" * 64,
        )
    with pytest.raises(ValidationError, match="builder provenance"):
        ReleaseMetadata(
            schema_version=3,
            run_id="run",
            git_commit="a" * 40,
            created_at="2026-07-28T00:00:00+00:00",
            files=tuple(sorted(RELEASE_FILES_V3)),
            source_results_sha256="b" * 64,
            published_results_sha256="c" * 64,
        )


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
        "/Users/example/private known-secret"
    )
    assert redactor.findings(text) == [
        "absolute user path",
        "bearer token",
        "common API key",
        "known private environment value",
    ]
    assert redactor.findings(redactor.text(text)) == []


def test_redactor_includes_auxiliary_environment_secrets() -> None:
    values = {
        "SERPAPI_API_KEY": "serp-secret-value",
        "TOOLATHLON_SERVER_HOST": "toolathlon.internal",
    }
    with patch.dict("os.environ", values, clear=False):
        redactor = Redactor.from_catalog(load_model_catalog())

    rendered = redactor.text("serp-secret-value via toolathlon.internal")
    assert rendered == "[REDACTED] via [REDACTED]"
