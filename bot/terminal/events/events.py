"""Terminal domain events (declarations only — no business logic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignalViewed:
    symbol: str | None = None
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionRequested:
    symbol: str
    side: str
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionOpened:
    symbol: str
    side: str
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionClosed:
    symbol: str
    side: str | None = None
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskChanged:
    symbol: str | None = None
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StopModified:
    symbol: str
    stop: float
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TakeProfitModified:
    symbol: str
    take_profit: float
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioViewed:
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MorningBriefGenerated:
    source: str = "terminal"
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "MorningBriefGenerated",
    "PortfolioViewed",
    "PositionClosed",
    "PositionOpened",
    "PositionRequested",
    "RiskChanged",
    "SignalViewed",
    "StopModified",
    "TakeProfitModified",
]
