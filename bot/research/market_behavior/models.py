"""Data models for market behavior research."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MarketSummary:
    market_slug: str
    window_start_ts: int
    strike: float | None
    final_btc: float
    btc_delta_final: float | None
    winning_side: str
    yes_bid_min: float | None
    yes_bid_max: float | None
    yes_ask_min: float | None
    yes_ask_max: float | None
    no_bid_min: float | None
    no_bid_max: float | None
    no_ask_min: float | None
    no_ask_max: float | None
    observation_count: int


@dataclass
class LateWindowSnapshot:
    market_slug: str
    seconds_bucket: int
    obs_timestamp: int | None
    seconds_left_actual: int | None
    btc_price: float | None
    btc_delta_vs_strike: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    btc_move_to_close: float | None
    yes_ask_move_to_close: float | None
    no_ask_move_to_close: float | None


@dataclass
class TpSample:
    side: str
    entry_bucket: str
    entry_price_mid: float
    tp_level: float
    reached: bool


@dataclass
class TpAggregate:
    side: str
    entry_bucket: str
    entry_price_mid: float
    tp_level: float
    sample_count: int
    reach_count: int

    @property
    def reach_probability(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.reach_count / self.sample_count


@dataclass
class AnalysisReport:
    markets_analyzed: int
    markets_skipped: int
    summaries: list[MarketSummary] = field(default_factory=list)
    late_windows: list[LateWindowSnapshot] = field(default_factory=list)
    tp_aggregates: list[TpAggregate] = field(default_factory=list)
    yes_wins: int = 0
    no_wins: int = 0
    ties: int = 0
