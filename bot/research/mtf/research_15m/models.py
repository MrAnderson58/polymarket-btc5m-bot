"""15m research data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Obs15m:
    timestamp: int
    market_slug: str
    window_start_ts: int
    entry_second: int
    seconds_left: int | None
    btc_price: float | None
    strike: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    spread: float | None
    btc_move_30s: float = 0.0
    btc_move_1m: float = 0.0
    btc_move_3m: float = 0.0
    btc_move_5m: float = 0.0
    btc_move_10m: float = 0.0
    dist_strike_usd: float = 0.0
    dist_strike_pct: float = 0.0
    realized_vol: float = 0.0
    acceleration: float = 0.0
    momentum_consistency: float = 0.0
    btc_trend_1h: float | None = None
    btc_trend_daily: float | None = None
    pm_prob_1h: float | None = None
    pm_prob_daily: float | None = None
    yes_mid: float | None = None


@dataclass
class SimTrade:
    family: str
    side: str
    market_slug: str
    entry_ts: int
    entry_price: float
    entry_second: int
    exit_ts: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_pct: float | None = None
    mfe_pct: float = 0.0
    mae_pct: float = 0.0


@dataclass
class FamilyResult:
    family: str
    side: str
    exit_mode: str
    n: int = 0
    pf: float = 0.0
    wr: float = 0.0
    avg_pnl: float = 0.0
    max_dd: float = 0.0
    max_cl: int = 0
    stress_pp_01: dict[str, float] = field(default_factory=dict)
    stress_pp_02: dict[str, float] = field(default_factory=dict)
    bootstrap_p_pf_gt1: float | None = None
    oos_pf: float | None = None
    temporal_stable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "side": self.side,
            "exit_mode": self.exit_mode,
            "n": self.n,
            "pf": self.pf,
            "wr": self.wr,
            "avg_pnl": self.avg_pnl,
            "max_dd": self.max_dd,
            "max_cl": self.max_cl,
            "stress_pp_01": self.stress_pp_01,
            "stress_pp_02": self.stress_pp_02,
            "bootstrap_p_pf_gt1": self.bootstrap_p_pf_gt1,
            "oos_pf": self.oos_pf,
            "temporal_stable": self.temporal_stable,
        }
