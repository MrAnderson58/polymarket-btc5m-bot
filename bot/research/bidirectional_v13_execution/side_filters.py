"""Side-specific candidate filters — tested without joint optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bot.research.bidirectional_live_audit import LiveTrade


@dataclass(frozen=True)
class SideFilter:
    name: str
    side: str
    fn: Callable[[LiveTrade], bool]


def _move_abs(trade: LiveTrade) -> float:
    return abs(trade.btc_move_30s or 0.0)


def _move_signed(trade: LiveTrade) -> float:
    return trade.btc_move_30s or 0.0


YES_FILTERS: list[SideFilter] = [
    SideFilter("yes_baseline_move5", "YES", lambda t: t.side == "YES" and _move_signed(t) >= 5),
    SideFilter("yes_move10", "YES", lambda t: t.side == "YES" and _move_signed(t) >= 10),
    SideFilter("yes_move15", "YES", lambda t: t.side == "YES" and _move_signed(t) >= 15),
    SideFilter("yes_move15_30", "YES", lambda t: t.side == "YES" and 15 <= _move_signed(t) < 30),
    SideFilter("yes_excl_035_040", "YES", lambda t: t.side == "YES" and not (0.35 <= t.entry_price < 0.40)),
    SideFilter("yes_bucket_025_030", "YES", lambda t: t.side == "YES" and 0.25 <= t.entry_price < 0.30),
    SideFilter("yes_bucket_030_035", "YES", lambda t: t.side == "YES" and 0.30 <= t.entry_price < 0.35),
    SideFilter("yes_bucket_040_045", "YES", lambda t: t.side == "YES" and 0.40 <= t.entry_price <= 0.45),
]

NO_FILTERS: list[SideFilter] = [
    SideFilter("no_baseline_move5", "NO", lambda t: t.side == "NO" and _move_abs(t) >= 5),
    SideFilter("no_move10", "NO", lambda t: t.side == "NO" and _move_abs(t) >= 10),
    SideFilter("no_move15", "NO", lambda t: t.side == "NO" and _move_abs(t) >= 15),
    SideFilter("no_move15_30", "NO", lambda t: t.side == "NO" and 15 <= _move_abs(t) < 30),
    SideFilter("no_entry_lt_025", "NO", lambda t: t.side == "NO" and t.entry_price < 0.25),
    SideFilter("no_entry_025_030", "NO", lambda t: t.side == "NO" and 0.25 <= t.entry_price < 0.30),
    SideFilter("no_entry_035_040", "NO", lambda t: t.side == "NO" and 0.35 <= t.entry_price < 0.40),
    SideFilter("no_excl_040_045", "NO", lambda t: t.side == "NO" and not (0.40 <= t.entry_price < 0.45)),
]

ALL_SIDE_FILTERS = YES_FILTERS + NO_FILTERS


def apply_side_filter(trades: list[LiveTrade], filt: SideFilter) -> list[LiveTrade]:
    return [t for t in trades if filt.fn(t)]
