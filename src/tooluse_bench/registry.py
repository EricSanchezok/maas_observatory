"""Benchmark discovery through standard Python entry points."""

from __future__ import annotations

from importlib.metadata import entry_points

from tooluse_bench.benchmarks.base import BenchmarkAdapter

ENTRY_POINT_GROUP = "tooluse_bench.benchmarks"


class BenchmarkRegistry:
    def __init__(self, adapters: list[BenchmarkAdapter]) -> None:
        self._adapters: dict[str, BenchmarkAdapter] = {}
        for adapter in adapters:
            benchmark_id = adapter.metadata.benchmark_id
            if benchmark_id in self._adapters:
                raise ValueError(f"duplicate benchmark adapter: {benchmark_id}")
            self._adapters[benchmark_id] = adapter

    @classmethod
    def discover(cls) -> BenchmarkRegistry:
        adapters: list[BenchmarkAdapter] = []
        for entry_point in sorted(
            entry_points(group=ENTRY_POINT_GROUP), key=lambda item: item.name
        ):
            adapter_type = entry_point.load()
            adapter = adapter_type()
            if not isinstance(adapter, BenchmarkAdapter):
                raise TypeError(
                    f"entry point {entry_point.name} is not a BenchmarkAdapter"
                )
            adapters.append(adapter)
        return cls(adapters)

    def get(self, benchmark_id: str) -> BenchmarkAdapter:
        try:
            return self._adapters[benchmark_id]
        except KeyError as exc:
            raise KeyError(
                f"benchmark adapter is not installed: {benchmark_id}"
            ) from exc

    def all(self) -> tuple[BenchmarkAdapter, ...]:
        return tuple(self._adapters[key] for key in sorted(self._adapters))
