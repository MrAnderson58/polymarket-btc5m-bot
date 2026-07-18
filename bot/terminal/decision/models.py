"""DecisionCard DTOs — trader-facing idea, not a raw score."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.terminal.instruments.models import AssetClass
from bot.terminal.instruments.profiles import MarketProfile
from bot.terminal.models.dto import SignalCard
from bot.terminal.scanner.models import ScannerResult


@dataclass(frozen=True)
class LearningHint:
    """Soft learning context (no model calls)."""

    confirms: bool | None = None
    winrate_pct: float | None = None
    note: str = ""


@dataclass(frozen=True)
class DecisionInputs:
    """Inputs the builder may consume."""

    scan: ScannerResult
    profile: MarketProfile | None = None
    signal: SignalCard | None = None
    learning: LearningHint | None = None
    price: float | None = None


@dataclass(frozen=True)
class DecisionCard:
    """Actionable idea card derived from Scanner + Profile + Signal + Learning."""

    symbol: str
    direction: str
    confidence: float | None
    score: float
    entry: float | None
    stop: float | None
    tp1: float | None
    tp2: float | None
    risk: str
    holding_time: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    ai_summary: str = ""
    asset_class: AssetClass | None = None
    provider: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def headline(self) -> str:
        return f"{self.symbol} {self.direction}".strip()


__all__ = [
    "DecisionCard",
    "DecisionInputs",
    "LearningHint",
]
