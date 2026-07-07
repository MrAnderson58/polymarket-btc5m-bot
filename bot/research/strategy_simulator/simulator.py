"""Forward replay simulator — no look-ahead."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.strategy_simulator.config import DISCOVER_TPS
from bot.research.strategy_simulator.features import SnapshotFeatures, build_snapshot_features
from bot.research.strategy_simulator.market_context import MarketPathContext
from bot.research.strategy_simulator.strategies import Strategy


@dataclass
class VirtualTrade:
    market_slug: str
    strategy_fp: str
    direction: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    pnl: float
    won: bool
    holding_seconds: int
    btc_delta_at_entry: float | None
    seconds_left_at_entry: int
    spread_at_entry: float | None


def _entry_ask(feat: SnapshotFeatures, direction: str) -> float | None:
    return feat.yes_ask if direction == "YES" else feat.no_ask


def _matches_strategy(feat: SnapshotFeatures, strategy: Strategy) -> bool:
    if feat.seconds_left < strategy.min_seconds_left:
        return False
    ask = _entry_ask(feat, strategy.direction)
    if ask is None or ask > strategy.max_entry or ask <= 0.05:
        return False
    if feat.spread_now is None or feat.spread_now > strategy.max_spread:
        return False
    if feat.btc_delta is None:
        return False
    if strategy.min_delta is not None and feat.btc_delta < strategy.min_delta:
        return False
    if strategy.max_delta is not None and feat.btc_delta > strategy.max_delta:
        return False
    if ask >= strategy.tp:
        return False
    return True


def _forward_exit(
    observations: list[dict],
    start_idx: int,
    *,
    direction: str,
    entry_price: float,
    tp: float,
) -> tuple[float, int, bool]:
    bid_key = "yes_bid" if direction == "YES" else "no_bid"
    exit_price = float(observations[-1].get(bid_key) or entry_price)
    exit_ts = int(observations[-1]["timestamp"])
    won = False

    for obs in observations[start_idx + 1:]:
        bid = obs.get(bid_key)
        if bid is None:
            continue
        bid_f = float(bid)
        if bid_f >= tp:
            return tp, int(obs["timestamp"]), True
        exit_price = bid_f
        exit_ts = int(obs["timestamp"])

    return exit_price, exit_ts, won


def simulate_strategy_on_context(
    ctx: MarketPathContext,
    strategy: Strategy,
    *,
    one_trade_per_market: bool = False,
) -> list[VirtualTrade]:
    """Replay strategy using precomputed features and forward exits."""
    if len(ctx.observations) < 2:
        return []

    features = ctx.features_yes if strategy.direction == "YES" else ctx.features_no
    forwards = ctx.forward_yes if strategy.direction == "YES" else ctx.forward_no

    trades: list[VirtualTrade] = []
    for idx, feat in enumerate(features):
        if feat is None or not _matches_strategy(feat, strategy):
            continue

        entry = _entry_ask(feat, strategy.direction)
        assert entry is not None
        exit_price, exit_ts, won = forwards[idx].exit_for(strategy.tp)
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


def simulate_strategy_on_market(
    market_slug: str,
    observations: list[dict],
    strategy: Strategy,
    *,
    one_trade_per_market: bool = False,
    ctx: MarketPathContext | None = None,
) -> list[VirtualTrade]:
    """Replay strategy on one market path."""
    if len(observations) < 2:
        return []

    if ctx is not None:
        return simulate_strategy_on_context(ctx, strategy, one_trade_per_market=one_trade_per_market)

    trades: list[VirtualTrade] = []
    for idx in range(len(observations)):
        feat = build_snapshot_features(observations, idx, side=strategy.direction)
        if feat is None or not _matches_strategy(feat, strategy):
            continue

        entry = _entry_ask(feat, strategy.direction)
        assert entry is not None
        exit_price, exit_ts, won = _forward_exit(
            observations, idx,
            direction=strategy.direction,
            entry_price=entry,
            tp=strategy.tp,
        )
        pnl = exit_price - entry
        trades.append(VirtualTrade(
            market_slug=market_slug,
            strategy_fp=strategy.fingerprint(),
            direction=strategy.direction,
            entry_ts=feat.timestamp,
            exit_ts=exit_ts,
            entry_price=entry,
            exit_price=exit_price,
            pnl=pnl,
            won=won,
            holding_seconds=max(0, exit_ts - feat.timestamp),
            btc_delta_at_entry=feat.btc_delta,
            seconds_left_at_entry=feat.seconds_left,
            spread_at_entry=feat.spread_now,
        ))
        if one_trade_per_market:
            break

    return trades


def build_market_context(
    market_slug: str,
    observations: list[dict],
    *,
    tp_levels: tuple[float, ...] | None = None,
) -> MarketPathContext:
    return MarketPathContext.build(
        market_slug,
        observations,
        tp_levels=tp_levels or DISCOVER_TPS,
    )
