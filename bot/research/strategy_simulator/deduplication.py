"""Deduplicate strategies with identical trade sets."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.statistics import SimulationStats
from bot.research.strategy_simulator.strategies import Strategy


def trade_set_identity(trades: list[VirtualTrade]) -> frozenset[tuple[str, int]]:
    """Sorted market_slug + entry_ts identity set."""
    return frozenset((t.market_slug, t.entry_ts) for t in trades)


def _strictness_key(strategy: Strategy) -> tuple:
    """Lower is stricter / preferred representative."""
    return (
        strategy.max_spread,
        strategy.max_entry,
        -strategy.min_seconds_left,
        -(strategy.min_delta or -1e9),
        strategy.max_delta if strategy.max_delta is not None else 1e9,
        strategy.tp,
        strategy.fingerprint(),
    )


@dataclass
class StrategyFamily:
    representative: SimulationStats
    members: list[SimulationStats] = field(default_factory=list)
    trade_identity: frozenset[tuple[str, int]] = field(default_factory=frozenset)

    @property
    def family_size(self) -> int:
        return len(self.members)

    @property
    def spread_range(self) -> str:
        spreads = sorted({m.strategy.max_spread for m in self.members})
        if len(spreads) == 1:
            return f"{spreads[0] * 100:.0f}c"
        return f"{spreads[0] * 100:.0f}c–{spreads[-1] * 100:.0f}c"


def deduplicate_by_trade_set(
    ranked: list[SimulationStats],
    trades_by_fp: dict[str, list[VirtualTrade]],
) -> list[StrategyFamily]:
    """Group strategies with identical trade sets; keep strictest representative."""
    buckets: dict[frozenset[tuple[str, int]], list[SimulationStats]] = {}
    for stats in ranked:
        identity = trade_set_identity(trades_by_fp.get(stats.fingerprint, []))
        buckets.setdefault(identity, []).append(stats)

    families: list[StrategyFamily] = []
    for identity, members in buckets.items():
        members_sorted = sorted(members, key=lambda s: _strictness_key(s.strategy))
        rep = members_sorted[0]
        families.append(StrategyFamily(
            representative=rep,
            members=members_sorted,
            trade_identity=identity,
        ))

    families.sort(
        key=lambda f: (
            f.representative.expected_value,
            f.representative.profit_factor,
            f.representative.win_rate,
            f.representative.trades,
        ),
        reverse=True,
    )
    return families


def deduplicate_stats_list(
    ranked: list[SimulationStats],
    trades_by_fp: dict[str, list[VirtualTrade]],
    *,
    top_n: int,
) -> tuple[list[SimulationStats], list[StrategyFamily]]:
    families = deduplicate_by_trade_set(ranked, trades_by_fp)
    reps = [f.representative for f in families[:top_n]]
    return reps, families[:top_n]
