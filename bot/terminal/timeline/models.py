"""Market Timeline models (V7.1.2) — read-only event log."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TimelineEvent:
    symbol: str
    kind: str
    message: str
    ts: str
    extra: dict[str, Any] = field(default_factory=dict)


def utc_hm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


__all__ = ["TimelineEvent", "utc_hm", "utc_iso"]
