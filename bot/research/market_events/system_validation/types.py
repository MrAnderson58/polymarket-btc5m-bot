"""Phase E.5.1 — validation result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    name: str
    status: str  # PASS | WARN | FAIL | SKIP
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)


def status_rank(status: str) -> int:
    return {"PASS": 0, "SKIP": 1, "WARN": 2, "FAIL": 3}.get(status, 4)
