"""Stable benchmark plugin protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tooluse_bench.domain import BenchmarkSelection, Lane, ModelDeployment
from tooluse_bench.records import (
    BenchmarkMetadata,
    RunSpec,
    TaskResult,
    ValidationIssue,
)
from tooluse_bench.transport import OpenAITransport


@dataclass(frozen=True)
class AdapterContext:
    spec: RunSpec
    deployment: ModelDeployment
    selection: BenchmarkSelection
    workspace: Path
    transport: OpenAITransport | None


class BenchmarkAdapter(ABC):
    @property
    @abstractmethod
    def metadata(self) -> BenchmarkMetadata:
        """Return immutable benchmark provenance."""

    def validate(
        self, selection: BenchmarkSelection, deployment: ModelDeployment
    ) -> tuple[ValidationIssue, ...]:
        if selection.profile not in self.metadata.supported_profiles:
            return (
                ValidationIssue(
                    level="error",
                    code="unknown_profile",
                    message=(
                        f"{selection.profile!r} is not supported by "
                        f"{self.metadata.benchmark_id}"
                    ),
                ),
            )
        return ()

    def supports_lane(
        self,
        lane: Lane,
        selection: BenchmarkSelection,
        deployment: ModelDeployment,
    ) -> tuple[bool, str | None]:
        del selection, deployment
        if lane is Lane.STANDARDIZED:
            return True, None
        return False, "no complete official reproduction protocol is registered"

    def needs_native_transport(self) -> bool:
        return False

    @abstractmethod
    def run(self, context: AdapterContext) -> Iterable[TaskResult]:
        """Execute one deployment/lane/trial and return normalized records."""
