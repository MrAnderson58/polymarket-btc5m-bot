"""Exit execution models — apply exit cost without double-counting entry."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bot.research.bidirectional_live_audit import LiveTrade, _pnl_from_prices
from bot.research.bidirectional_v13_execution.quote_path import QuoteObservation


@dataclass(frozen=True)
class ExitResult:
    exit_price: float | None
    exit_ts: int | None
    pnl_pct: float | None
    reason: str


def _side_bid(obs: QuoteObservation, side: str) -> float | None:
    return obs.yes_bid if side == "YES" else obs.no_bid


def _load_exit_window(
    conn: sqlite3.Connection,
    market_slug: str,
    exit_ts: int,
) -> list[QuoteObservation]:
    rows = conn.execute(
        """
        SELECT timestamp, yes_bid, yes_ask, no_bid, no_ask
        FROM v4_shadow_observations
        WHERE market_slug = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (market_slug, exit_ts - 2, exit_ts + 5),
    ).fetchall()
    return [
        QuoteObservation(
            timestamp=int(r["timestamp"]),
            yes_bid=r["yes_bid"],
            yes_ask=r["yes_ask"],
            no_bid=r["no_bid"],
            no_ask=r["no_ask"],
        )
        for r in rows
    ]


def _bid_at_or_before(observations: list[QuoteObservation], side: str, ts: int) -> float | None:
    best: float | None = None
    for obs in observations:
        if obs.timestamp > ts:
            break
        bid = _side_bid(obs, side)
        if bid is not None and bid > 0:
            best = bid
    return best


def _bid_at_or_after(observations: list[QuoteObservation], side: str, ts: int, max_lag: int = 5) -> float | None:
    for obs in observations:
        if obs.timestamp < ts:
            continue
        if obs.timestamp > ts + max_lag:
            break
        bid = _side_bid(obs, side)
        if bid is not None and bid > 0:
            return bid
    return None


def apply_exit_model(
    conn: sqlite3.Connection,
    trade: LiveTrade,
    entry_price: float,
    model: str,
) -> ExitResult:
    if trade.exit_ts is None or trade.exit_price is None:
        return ExitResult(None, None, None, "no_shadow_exit")

    exit_ts = trade.exit_ts
    observations = _load_exit_window(conn, trade.market_slug, exit_ts)
    side = trade.side

    if model == "immediate_bid":
        bid = _bid_at_or_before(observations, side, exit_ts)
        if bid is None:
            bid = trade.exit_price
        return ExitResult(bid, exit_ts, _pnl_from_prices(entry_price, bid), "immediate_bid")

    if model == "next_observation_bid":
        bid = _bid_at_or_after(observations, side, exit_ts)
        if bid is None:
            return ExitResult(None, None, None, "no_next_bid")
        ts = next((o.timestamp for o in observations if _side_bid(o, side) == bid), exit_ts)
        return ExitResult(bid, ts, _pnl_from_prices(entry_price, bid), "next_obs_bid")

    if model == "adverse_delay_1s":
        bid = _bid_at_or_after(observations, side, exit_ts + 1)
        if bid is None:
            return ExitResult(None, None, None, "no_bid_1s_delay")
        return ExitResult(bid, exit_ts + 1, _pnl_from_prices(entry_price, bid), "adverse_1s")

    if model == "adverse_delay_2s":
        bid = _bid_at_or_after(observations, side, exit_ts + 2)
        if bid is None:
            return ExitResult(None, None, None, "no_bid_2s_delay")
        return ExitResult(bid, exit_ts + 2, _pnl_from_prices(entry_price, bid), "adverse_2s")

    if model == "fixed_minus_005":
        ep = max(trade.exit_price - 0.005, 0.01)
        return ExitResult(ep, exit_ts, _pnl_from_prices(entry_price, ep), "fixed_-0.005")

    if model == "fixed_minus_010":
        ep = max(trade.exit_price - 0.01, 0.01)
        return ExitResult(ep, exit_ts, _pnl_from_prices(entry_price, ep), "fixed_-0.01")

    # Shadow ideal (reference only — not for promotion)
    return ExitResult(trade.exit_price, exit_ts, _pnl_from_prices(entry_price, trade.exit_price), "shadow_ideal")


EXIT_MODELS = (
    "immediate_bid",
    "next_observation_bid",
    "adverse_delay_1s",
    "adverse_delay_2s",
    "fixed_minus_005",
    "fixed_minus_010",
)
