from __future__ import annotations

from collections.abc import Iterable

import pytest

from tooluse_bench.benchmarks.base import AdapterContext, BenchmarkAdapter
from tooluse_bench.records import BenchmarkMetadata, TaskResult
from tooluse_bench.registry import BenchmarkRegistry


class FakeAdapter(BenchmarkAdapter):
    @property
    def metadata(self) -> BenchmarkMetadata:
        return BenchmarkMetadata(
            benchmark_id="fake",
            display_name="Fake",
            version="1",
            source_url="https://example.invalid",
            revision="abc",
            hermetic_default=True,
            supported_profiles=("full",),
        )

    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        del context
        return ()


def test_registry_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate benchmark"):
        BenchmarkRegistry([FakeAdapter(), FakeAdapter()])


def test_installed_entry_points_include_all_builtins() -> None:
    ids = {
        adapter.metadata.benchmark_id for adapter in BenchmarkRegistry.discover().all()
    }
    assert ids == {"probe", "bfcl-v4", "toolathlon-verified"}
