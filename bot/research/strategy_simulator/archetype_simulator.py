"""Archetype strategy forward replay simulator."""

from __future__ import annotations

from bot.research.strategy_simulator.archetype_context import ArchetypeMarketContext
from bot.research.strategy_simulator.archetypes import ArchetypeStrategy, matches_archetype
from bot.research.strategy_simulator.causal_features import CausalSnapshotFeatures
from bot.research.strategy_simulator.simulator import VirtualTrade


def _entry_ask(feat: CausalSnapshotFeatures, direction: str) -> float | None:
    return feat.yes_ask if direction == "YES" else feat.no_ask


def simulate_archetype_on_context(
    ctx: ArchetypeMarketContext,
    strategy: ArchetypeStrategy,
    *,
    one_trade_per_market: bool = False,
) -> list[VirtualTrade]:
    features = ctx.features_yes if strategy.direction == "YES" else ctx.features_no
    exits = ctx.exit_outcomes_yes if strategy.direction == "YES" else ctx.exit_outcomes_no
    exit_key = strategy.exit_spec.label()
    trades: list[VirtualTrade] = []

    for idx, feat in enumerate(features):
        if feat is None or not matches_archetype(feat, strategy):
            continue
        entry = _entry_ask(feat, strategy.direction)
        if entry is None:
            continue
        exit_price, exit_ts, won = exits[idx].get(
            exit_key,
            (entry, feat.timestamp, False),
        )
        trades.append(VirtualTrade(
            market_slug=ctx.market_slug,
            strategy_fp=strategy.fingerprint(),
            direction=strategy.direction,
            entry_ts=feat.timestamp,
            exit_ts=exit_ts,
            entry_price=entry,
            exit_price=exit_price,
            pnl=exit_price - entry,
            won=won,
            holding_seconds=max(0, exit_ts - feat.timestamp),
            btc_delta_at_entry=feat.btc_delta,
            seconds_left_at_entry=feat.seconds_left,
            spread_at_entry=feat.spread_now,
        ))
        if one_trade_per_market:
            break
    return trades
