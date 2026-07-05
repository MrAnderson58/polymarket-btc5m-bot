"""Data models for multi-timeframe context research."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BtcSpotContext:
    timestamp: int
    price: float | None
    return_5m: float | None = None
    return_15m: float | None = None
    return_30m: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    dist_from_high_4h_pct: float | None = None
    dist_from_low_4h_pct: float | None = None
    realized_vol_1h: float | None = None
    trend_slope_1h: float | None = None
    acceleration_15m: float | None = None


@dataclass
class PolymarketTfContext:
    timeframe: str  # 15m | 1h | daily
    market_slug: str | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    midpoint: float | None = None
    spread: float | None = None
    strike: float | None = None
    seconds_left: int | None = None
    prob_yes: float | None = None  # implied from midpoint or ask
    prob_direction: str | None = None  # UP | DOWN | NEUTRAL
    quote_ts: int | None = None
    available: bool = False


@dataclass
class TradeContext:
    trade_id: int
    market_slug: str
    entry_ts: int
    side: str
    entry_price: float
    pnl_pct: float | None
    btc: BtcSpotContext
    pm_15m: PolymarketTfContext = field(default_factory=lambda: PolymarketTfContext("15m"))
    pm_1h: PolymarketTfContext = field(default_factory=lambda: PolymarketTfContext("1h"))
    pm_daily: PolymarketTfContext = field(default_factory=lambda: PolymarketTfContext("daily"))
    htf_label: str = "HTF_MIXED"
    alignment_label: str = "NEUTRAL"


@dataclass
class ContextMetrics:
    label: str
    n: int = 0
    yes_n: int = 0
    no_n: int = 0
    pf: float = 0.0
    yes_pf: float = 0.0
    no_pf: float = 0.0
    wr: float = 0.0
    avg_pnl: float = 0.0
    max_dd: float = 0.0
    max_cl: int = 0
    profit_contribution_pct: float = 0.0
    bootstrap_pp_gt1: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n": self.n,
            "yes_n": self.yes_n,
            "no_n": self.no_n,
            "pf": self.pf,
            "yes_pf": self.yes_pf,
            "no_pf": self.no_pf,
            "wr": self.wr,
            "avg_pnl": self.avg_pnl,
            "max_dd": self.max_dd,
            "max_cl": self.max_cl,
            "profit_contribution_pct": self.profit_contribution_pct,
            "bootstrap_pp_gt1": self.bootstrap_pp_gt1,
        }
