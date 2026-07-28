from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from tooluse_bench.cli import _resolve_directory, app, main

runner = CliRunner()


def test_catalog_and_benchmark_listing_commands() -> None:
    models = runner.invoke(app, ["models", "list"])
    benchmarks = runner.invoke(app, ["benchmarks", "list"])
    assert models.exit_code == 0
    assert "deepseek-v4-pro" in models.stdout
    assert benchmarks.exit_code == 0
    assert "bfcl-v4" in benchmarks.stdout
    assert "official" in benchmarks.stdout


def test_model_validation_modes() -> None:
    without_private_configuration = runner.invoke(app, ["models", "validate"])
    assert without_private_configuration.exit_code == 0

    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch.dict("os.environ", {}, clear=True),
    ):
        required = runner.invoke(
            app,
            ["models", "validate", "--require-endpoints"],
        )
    assert required.exit_code == 1
    assert "FAIL" in required.stdout


def test_benchmark_validation_reports_missing_infrastructure() -> None:
    with patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(app, ["benchmarks", "validate"])
    assert result.exit_code == 1
    assert "missing_toolathlon_server" in result.stdout


def test_run_command_delegates_to_runner(tmp_path: Path) -> None:
    completed = tmp_path / "runs" / "completed"
    completed.mkdir(parents=True)
    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch(
            "tooluse_bench.cli.run_experiment",
            return_value=completed,
        ) as run_experiment,
    ):
        result = runner.invoke(
            app,
            ["run", "--output-root", str(tmp_path / "runs")],
        )
    assert result.exit_code == 0
    assert "Run completed" in result.stdout
    run_experiment.assert_called_once()


def test_report_and_release_commands_delegate(tmp_path: Path) -> None:
    run_directory = tmp_path / "runs" / "run-id"
    report_directory = run_directory / "report"
    release_directory = tmp_path / "releases" / "run-id"
    archive = tmp_path / "releases" / "run-id.tar.gz"
    run_directory.mkdir(parents=True)
    release_directory.mkdir(parents=True)
    archive.touch()

    with patch(
        "tooluse_bench.cli.build_report",
        return_value=report_directory,
    ) as build_report:
        report = runner.invoke(
            app,
            [
                "report",
                "build",
                "run-id",
                "--runs-root",
                str(tmp_path / "runs"),
            ],
        )
    assert report.exit_code == 0
    build_report.assert_called_once_with(run_directory.resolve())

    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch(
            "tooluse_bench.cli.build_release",
            return_value=(release_directory, archive),
        ) as build_release,
    ):
        release = runner.invoke(
            app,
            [
                "release",
                "build",
                str(run_directory),
                "--output-root",
                str(tmp_path / "releases"),
            ],
        )
    assert release.exit_code == 0
    build_release.assert_called_once_with(
        run_directory.resolve(),
        output_root=tmp_path / "releases",
    )

    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch("tooluse_bench.cli.validate_release") as validate_release,
    ):
        validate = runner.invoke(
            app,
            [
                "release",
                "validate",
                "run-id",
                "--releases-root",
                str(tmp_path / "releases"),
            ],
        )
    assert validate.exit_code == 0
    validate_release.assert_called_once_with(release_directory.resolve())

    snapshot = tmp_path / "public" / "run-id"
    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch(
            "tooluse_bench.cli.build_public_snapshot",
            return_value=snapshot,
        ) as build_public_snapshot,
    ):
        publication = runner.invoke(
            app,
            [
                "publication",
                "build",
                "run-id",
                "--title",
                "Release candidate",
                "--releases-root",
                str(tmp_path / "releases"),
                "--archive",
                str(archive),
                "--output-root",
                str(tmp_path / "public"),
                "--note",
                "No remote is configured.",
            ],
        )
    assert publication.exit_code == 0
    build_public_snapshot.assert_called_once_with(
        release_directory.resolve(),
        archive,
        title="Release candidate",
        status="candidate",
        release_url=None,
        notes=("No remote is configured.",),
        root=tmp_path / "public",
    )

    with (
        patch("tooluse_bench.cli.load_dotenv"),
        patch(
            "tooluse_bench.cli.validate_public_results",
            return_value=(
                type("Index", (), {"latest_run_id": "run-id"})(),
                {"run-id": ()},
            ),
        ) as validate_public_results,
    ):
        publication_validate = runner.invoke(
            app,
            [
                "publication",
                "validate",
                "--root",
                str(tmp_path / "public"),
            ],
        )
    assert publication_validate.exit_code == 0
    assert "latest=run-id" in publication_validate.stdout
    validate_public_results.assert_called_once_with(tmp_path / "public")


def test_directory_resolution_and_main(tmp_path: Path) -> None:
    directory = tmp_path / "direct"
    directory.mkdir()
    assert _resolve_directory(str(directory), tmp_path) == directory.resolve()

    missing = runner.invoke(
        app,
        ["report", "build", "missing", "--runs-root", str(tmp_path)],
    )
    assert missing.exit_code == 2

    with patch("tooluse_bench.cli.app") as typer_app:
        main()
    typer_app.assert_called_once_with()
