"""Per-condition opportunity funnel diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.strategy_simulator.features import SnapshotFeatures
from bot.research.strategy_simulator.market_context import MarketPathContext
from bot.research.strategy_simulator.simulator import (
    VirtualTrade,
    _entry_ask,
    _matches_strategy,
    simulate_strategy_on_context,
)
from bot.research.strategy_simulator.strategies import Strategy


def entry_condition(feat: SnapshotFeatures, strategy: Strategy) -> bool:
    ask = _entry_ask(feat, strategy.direction)
    return (
        ask is not None
        and ask > 0.05
        and ask <= strategy.max_entry
        and ask < strategy.tp
    )


def delta_condition(feat: SnapshotFeatures, strategy: Strategy) -> bool:
    if feat.btc_delta is None:
        return False
    if strategy.min_delta is not None and feat.btc_delta < strategy.min_delta:
        return False
    if strategy.max_delta is not None and feat.btc_delta > strategy.max_delta:
        return False
    return True


def time_condition(feat: SnapshotFeatures, strategy: Strategy) -> bool:
    return feat.seconds_left >= strategy.min_seconds_left


def spread_condition(feat: SnapshotFeatures, strategy: Strategy) -> bool:
    return feat.spread_now is not None and feat.spread_now <= strategy.max_spread


@dataclass
class OpportunityFunnel:
    markets_scanned: int = 0
    entry_opportunity: int = 0
    delta_opportunity: int = 0
    time_observable: int = 0
    spread_opportunity: int = 0
    all_conditions: int = 0
    trades: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "markets_scanned": self.markets_scanned,
            "entry_opportunity": self.entry_opportunity,
            "delta_opportunity": self.delta_opportunity,
            "time_observable": self.time_observable,
            "spread_opportunity": self.spread_opportunity,
            "all_conditions": self.all_conditions,
            "trades": self.trades,
        }


def analyze_market_opportunity(ctx: MarketPathContext, strategy: Strategy) -> OpportunityFunnel:
    """Per-market funnel counts (one market)."""
    features = ctx.features_yes if strategy.direction == "YES" else ctx.features_no
    funnel = OpportunityFunnel(markets_scanned=1)

    has_entry = has_delta = has_time = has_spread = has_all = False
    for feat in features:
        if feat is None:
            continue
        if entry_condition(feat, strategy):
            has_entry = True
        if delta_condition(feat, strategy):
            has_delta = True
        if time_condition(feat, strategy):
            has_time = True
        if spread_condition(feat, strategy):
            has_spread = True
        if _matches_strategy(feat, strategy):
            has_all = True

    funnel.entry_opportunity = 1 if has_entry else 0
    funnel.delta_opportunity = 1 if has_delta else 0
    funnel.time_observable = 1 if has_time else 0
    funnel.spread_opportunity = 1 if has_spread else 0
    funnel.all_conditions = 1 if has_all else 0
    return funnel


def analyze_split_opportunity(
    contexts: dict[str, MarketPathContext],
    strategy: Strategy,
    *,
    trades: list[VirtualTrade] | None = None,
) -> OpportunityFunnel:
    total = OpportunityFunnel()
    for ctx in contexts.values():
        m = analyze_market_opportunity(ctx, strategy)
        total.markets_scanned += m.markets_scanned
        total.entry_opportunity += m.entry_opportunity
        total.delta_opportunity += m.delta_opportunity
        total.time_observable += m.time_observable
        total.spread_opportunity += m.spread_opportunity
        total.all_conditions += m.all_conditions

    if trades is not None:
        total.trades = len(trades)
    else:
        for slug, ctx in contexts.items():
            total.trades += len(
                simulate_strategy_on_context(ctx, strategy, one_trade_per_market=True),
            )
    return total
