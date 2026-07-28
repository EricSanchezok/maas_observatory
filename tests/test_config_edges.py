from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from tooluse_bench.config import (
    load_baselines,
    load_dotenv,
    load_model_catalog,
    resolve_models,
)
from tooluse_bench.domain import (
    EndpointReference,
    ExperimentPlan,
    ModelCatalog,
    ModelDeployment,
)


def test_dotenv_conservative_parser_and_existing_value_precedence(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n# comment\nFIRST=value\nSECOND='quoted value'\nTHIRD=\"third\"\n",
        encoding="utf-8",
    )
    with patch.dict(os.environ, {"FIRST": "existing"}, clear=True):
        load_dotenv(dotenv)
        assert os.environ["FIRST"] == "existing"
        assert os.environ["SECOND"] == "quoted value"
        assert os.environ["THIRD"] == "third"

    load_dotenv(tmp_path / "missing")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("NO_EQUALS", "expected KEY=VALUE"),
        ("BAD-NAME=value", "invalid environment name"),
        ("=value", "invalid environment name"),
    ],
)
def test_dotenv_rejects_unsupported_syntax(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_dotenv(dotenv)


@pytest.mark.parametrize(
    "content",
    [
        "[not, a, mapping]",
        "models: [",
    ],
)
def test_yaml_loader_rejects_invalid_documents(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_model_catalog(path)
    with pytest.raises(ValueError, match="does not exist"):
        load_model_catalog(tmp_path / "absent.yaml")


def test_baselines_load_and_model_resolution() -> None:
    catalog = load_model_catalog()
    assert load_baselines().schema_version == 1
    assert resolve_models(None, True, catalog=catalog) == catalog.deployments
    selected = resolve_models(
        [catalog.deployments[1].alias, catalog.deployments[0].alias],
        False,
        catalog=catalog,
    )
    assert [item.alias for item in selected] == [
        catalog.deployments[1].alias,
        catalog.deployments[0].alias,
    ]
    with pytest.raises(ValueError, match="select at least"):
        resolve_models(None, False, catalog=catalog)
    with pytest.raises(ValueError, match="unknown model"):
        resolve_models(["unknown"], False, catalog=catalog)


def test_domain_validation_guards_ambiguous_configuration() -> None:
    with pytest.raises(ValidationError, match="invalid environment"):
        EndpointReference(base_url_env="lowercase", api_key_env="VALID_KEY")
    with pytest.raises(ValidationError, match="different variables"):
        EndpointReference(base_url_env="SAME", api_key_env="SAME")

    deployment_payload = load_model_catalog().deployments[0].model_dump()
    deployment_payload["input_modalities"] = ["text", "text"]
    with pytest.raises(ValidationError, match="duplicates"):
        ModelDeployment.model_validate(deployment_payload)

    catalog_payload = load_model_catalog().model_dump()
    catalog_payload["deployments"][1]["deployment_id"] = catalog_payload["deployments"][
        0
    ]["deployment_id"]
    with pytest.raises(ValidationError, match="duplicate deployment_id"):
        ModelCatalog.model_validate(catalog_payload)


@pytest.mark.parametrize(
    ("models", "lanes", "benchmarks", "message"),
    [
        (["*", "model"], ["standardized"], None, "only model selector"),
        (["model", "model"], ["standardized"], None, "models must not"),
        (["model"], ["standardized", "standardized"], None, "lanes must not"),
        (
            ["model"],
            ["standardized"],
            [
                {"benchmark_id": "probe", "profile": "full", "trials": 1},
                {"benchmark_id": "probe", "profile": "full", "trials": 1},
            ],
            "must be unique",
        ),
    ],
)
def test_experiment_plan_rejects_ambiguous_selections(
    models: list[str],
    lanes: list[str],
    benchmarks: list[dict] | None,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ExperimentPlan.model_validate(
            {
                "schema_version": 1,
                "experiment_id": "invalid",
                "description": "invalid plan",
                "models": models,
                "lanes": lanes,
                "benchmarks": benchmarks
                or [{"benchmark_id": "probe", "profile": "full", "trials": 1}],
            }
        )
