"""Recursive release redaction and leak detection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tooluse_bench.config import PROJECT_ROOT
from tooluse_bench.domain import ModelCatalog

SENSITIVE_KEY = re.compile(
    r"(authorization|api[_-]?key|access[_-]?token|secret|password)",
    re.IGNORECASE,
)
BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
COMMON_KEY_TOKEN = re.compile(r"\b(?:sk|key)-[A-Za-z0-9_-]{12,}\b")
ABSOLUTE_USER_PATH = re.compile(r"(?:/Users|/home)/[^/\s\"']+")


@dataclass(frozen=True)
class Redactor:
    replacements: tuple[tuple[str, str], ...]

    @classmethod
    def from_catalog(cls, catalog: ModelCatalog) -> Redactor:
        values: set[str] = {
            str(PROJECT_ROOT),
            str(Path.home()),
        }
        for deployment in catalog.deployments:
            for name in (
                deployment.endpoint.base_url_env,
                deployment.endpoint.api_key_env,
            ):
                if value := os.getenv(name):
                    values.add(value)
        replacements = []
        for value in sorted(values, key=len, reverse=True):
            if value == str(PROJECT_ROOT):
                replacement = "$PROJECT_ROOT"
            elif value == str(Path.home()):
                replacement = "$HOME"
            else:
                replacement = "[REDACTED]"
            replacements.append((value, replacement))
        return cls(tuple(replacements))

    def text(self, value: str) -> str:
        output = value
        for secret, replacement in self.replacements:
            if secret:
                output = output.replace(secret, replacement)
        output = BEARER_TOKEN.sub("Bearer [REDACTED]", output)
        output = COMMON_KEY_TOKEN.sub("[REDACTED]", output)
        output = ABSOLUTE_USER_PATH.sub("$HOME", output)
        return output

    def value(self, value: Any, *, key: str | None = None) -> Any:
        if key and SENSITIVE_KEY.search(key):
            return "[REDACTED]" if value not in (None, "") else value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(item_key): self.value(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        return value

    def findings(self, text: str) -> list[str]:
        findings: list[str] = []
        for secret, replacement in self.replacements:
            if replacement == "[REDACTED]" and secret and secret in text:
                findings.append("known private environment value")
        patterns = {
            "bearer token": BEARER_TOKEN,
            "common API key": COMMON_KEY_TOKEN,
            "absolute user path": ABSOLUTE_USER_PATH,
        }
        for name, pattern in patterns.items():
            if pattern.search(text):
                findings.append(name)
        return sorted(set(findings))
