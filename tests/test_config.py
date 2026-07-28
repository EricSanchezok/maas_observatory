import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydantic import ValidationError

from tooluse_bench.config import load_catalog, load_experiment, load_model_catalog


class CatalogTests(unittest.TestCase):
    def test_catalog_contains_all_nine_models(self) -> None:
        catalog = load_model_catalog()
        self.assertEqual(len(catalog.deployments), 9)
        self.assertEqual(len({model.alias for model in catalog.deployments}), 9)
        self.assertEqual(len({model.deployment_id for model in catalog.deployments}), 9)

    def test_missing_environment_is_reported(self) -> None:
        model = load_catalog()[0]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                model.configuration_errors(),
                [
                    f"missing {model.endpoint.base_url_env}",
                    f"missing {model.endpoint.api_key_env}",
                ],
            )

    def test_release_experiment_selects_all_models_and_three_trials(self) -> None:
        experiment = load_experiment()
        self.assertEqual(experiment.models, ["*"])
        self.assertEqual({item.trials for item in experiment.benchmarks}, {3})

    def test_duplicate_alias_is_rejected(self) -> None:
        source = Path("config/models.yaml").read_text(encoding="utf-8")
        duplicate = source.replace(
            "alias: deepseek-v4-flash", "alias: deepseek-v4-pro", 1
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "models.yaml"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_model_catalog(path)


if __name__ == "__main__":
    unittest.main()
