from __future__ import annotations

import os
from unittest.mock import patch

from tooluse_bench.benchmarks.external import isolated_environment


def test_isolated_environment_does_not_inherit_unrelated_secrets() -> None:
    with patch.dict(
        os.environ,
        {
            "PATH": "/bin",
            "UNRELATED_API_KEY": "must-not-leak",
            "SII_HOLOS_OTHER_API_KEY": "must-not-leak-either",
        },
        clear=True,
    ):
        environment = isolated_environment({"SII_BENCH_API_KEY": "target"})
    assert environment == {"PATH": "/bin", "SII_BENCH_API_KEY": "target"}
