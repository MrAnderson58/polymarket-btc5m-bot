"""Portfolio Intelligence DTOs (V7.0.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExposureSlice:
    """One bucket of portfolio exposure (sector / asset class)."""

    label: str
    weight_pct: float
    symbols: tuple[str, ...] = ()
    notional: float = 0.0


@dataclass(frozen=True)
class PositionWeight:
    symbol: str
    side: str
    weight_pct: float
    notional: float
    sector: str


@dataclass(frozen=True)
class PortfolioAdvice:
    """Template AI advice — no Claude / GPT."""

    headline: str
    bullets: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioIntelligence:
    """Analyzed portfolio view — risk, correlation, exposure, advice."""

    equity: float | None
    cash: float | None
    cash_pct: float | None
    open_risk: float | None
    open_risk_pct: float | None
    expected_dd_pct: float | None
    correlation: float | None
    correlation_note: str
    sector_exposure: tuple[ExposureSlice, ...] = ()
    crypto_exposure_pct: float = 0.0
    positions: tuple[PositionWeight, ...] = ()
    advice: PortfolioAdvice | None = None
    ai_summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ExposureSlice",
    "PortfolioAdvice",
    "PortfolioIntelligence",
    "PositionWeight",
]
