"""Runtime access to checked-in synthetic Agent fixtures."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from maas_observatory.models import ContextTier

_RESOURCE_PATH = "resources/agent_fixtures_v5.json"


@lru_cache(maxsize=1)
def _resource() -> dict[str, Any]:
    resource = files("maas_observatory").joinpath(_RESOURCE_PATH)
    data = cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))
    if data.get("suite_version") != "response-suite-v5":
        raise RuntimeError("invalid Agent fixture resource suite")
    return data


FIXTURE_IDS: list[str] = [
    "agent-1k-a",
    "agent-16k-a",
    "agent-64k-a",
    "agent-1k-b",
    "agent-16k-b",
    "agent-64k-b",
]


def _fixture(fixture_id: str) -> dict[str, Any]:
    if fixture_id not in FIXTURE_IDS:
        raise ValueError(f"unknown fixture_id: {fixture_id}")
    return cast(dict[str, Any], _resource()["fixtures"][fixture_id])


def get_payload(fixture_id: str) -> dict[str, Any]:
    """Return an isolated OpenAI-compatible payload for one fixture."""
    return copy.deepcopy(_fixture(fixture_id)["payload"])


def get_metadata(fixture_id: str) -> dict[str, Any]:
    """Return stable reference metadata for one fixture."""
    return copy.deepcopy(_fixture(fixture_id)["metadata"])


def all_metadata() -> dict[str, dict[str, Any]]:
    """Return stable metadata for all fixtures in schedule order."""
    return {fixture_id: get_metadata(fixture_id) for fixture_id in FIXTURE_IDS}


def fixture_hashes() -> dict[str, str]:
    """Return the checked-in canonical payload digest for every fixture."""
    return {
        fixture_id: str(_fixture(fixture_id)["metadata"]["sha256"])
        for fixture_id in FIXTURE_IDS
    }


def tier_for(fixture_id: str) -> ContextTier:
    """Return the context tier recorded for one fixture."""
    return ContextTier(str(_fixture(fixture_id)["metadata"]["context_tier"]))
