"""Quote path reconstruction around candidate entry signals — no look-ahead."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from bot.research.bidirectional_live_audit import LiveTrade
from bot.research.bidirectional_v13_execution.config import (
    QUOTE_PATH_POST_SEC,
    QUOTE_PATH_PRE_SEC,
)
from bot.research.features import compute_btc_moves


@dataclass
class QuoteObservation:
    timestamp: int
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    btc_price: float | None = None
    seconds_left: int | None = None


@dataclass
class QuotePathContext:
    trade_id: int
    market_slug: str
    side: str
    signal_ts: int
    signal_ask: float | None
    signal_bid: float | None
    spread: float | None
    quote_age_sec: int | None
    seconds_left: int | None
    regime: str | None
    entry_price_bucket: str
    btc_moves: dict[str, float] = field(default_factory=dict)
    next_quote_timestamps: list[int] = field(default_factory=list)
    ask_at_offset: dict[int, float | None] = field(default_factory=dict)
    min_ask_window: dict[int, float | None] = field(default_factory=dict)
    max_ask_window: dict[int, float | None] = field(default_factory=dict)
    signal_ask_reavailable: bool = False
    observations: list[QuoteObservation] = field(default_factory=list)
    coverage_ok: bool = False


def _side_ask(obs: QuoteObservation, side: str) -> float | None:
    return obs.yes_ask if side == "YES" else obs.no_ask


def _side_bid(obs: QuoteObservation, side: str) -> float | None:
    return obs.yes_bid if side == "YES" else obs.no_bid


def _price_bucket(price: float) -> str:
    lo = int(price * 20) * 5
    hi = lo + 5
    return f"{lo:02d}-{hi:02d}c"


def _load_v4_window(
    conn: sqlite3.Connection,
    market_slug: str,
    start_ts: int,
    end_ts: int,
) -> list[QuoteObservation]:
    rows = conn.execute(
        """
        SELECT timestamp, yes_bid, yes_ask, no_bid, no_ask, btc_price, seconds_left
        FROM v4_shadow_observations
        WHERE market_slug = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (market_slug, start_ts, end_ts),
    ).fetchall()
    return [
        QuoteObservation(
            timestamp=int(r["timestamp"]),
            yes_bid=r["yes_bid"],
            yes_ask=r["yes_ask"],
            no_bid=r["no_bid"],
            no_ask=r["no_ask"],
            btc_price=r["btc_price"],
            seconds_left=int(r["seconds_left"]) if r["seconds_left"] is not None else None,
        )
        for r in rows
    ]


def _signal_quote_at_or_before(
    observations: list[QuoteObservation],
    side: str,
    signal_ts: int,
) -> QuoteObservation | None:
    best: QuoteObservation | None = None
    for obs in observations:
        if obs.timestamp > signal_ts:
            break
        if _side_ask(obs, side) is not None:
            best = obs
    return best


def _ask_at_or_after(
    observations: list[QuoteObservation],
    side: str,
    target_ts: int,
    max_lag: int = 5,
) -> tuple[float | None, int | None]:
    for obs in observations:
        if obs.timestamp < target_ts:
            continue
        if obs.timestamp > target_ts + max_lag:
            break
        ask = _side_ask(obs, side)
        if ask is not None and ask > 0:
            return ask, obs.timestamp
    return None, None


def _min_max_ask_in_window(
    observations: list[QuoteObservation],
    side: str,
    start_ts: int,
    window_sec: int,
) -> tuple[float | None, float | None]:
    end_ts = start_ts + window_sec
    asks: list[float] = []
    for obs in observations:
        if obs.timestamp < start_ts:
            continue
        if obs.timestamp > end_ts:
            break
        ask = _side_ask(obs, side)
        if ask is not None and ask > 0:
            asks.append(ask)
    if not asks:
        return None, None
    return min(asks), max(asks)


def _btc_moves_at_signal(
    observations: list[QuoteObservation],
    signal_ts: int,
) -> dict[str, float]:
    dict_obs = [
        {"timestamp": o.timestamp, "btc_price": o.btc_price or 0.0}
        for o in observations
        if o.timestamp <= signal_ts and o.btc_price
    ]
    if not dict_obs:
        return {}
    idx = len(dict_obs) - 1
    moves = compute_btc_moves(dict_obs, idx)
    return {k: round(v, 2) for k, v in moves.items() if k.startswith("btc_move_")}


def reconstruct_quote_path(
    conn: sqlite3.Connection,
    trade: LiveTrade,
) -> QuotePathContext:
    signal_ts = trade.entry_ts
    start = signal_ts - QUOTE_PATH_PRE_SEC
    end = signal_ts + QUOTE_PATH_POST_SEC
    observations = _load_v4_window(conn, trade.market_slug, start, end)

    ctx = QuotePathContext(
        trade_id=trade.id,
        market_slug=trade.market_slug,
        side=trade.side,
        signal_ts=signal_ts,
        signal_ask=None,
        signal_bid=None,
        spread=None,
        quote_age_sec=None,
        seconds_left=trade.seconds_left,
        regime=trade.entry_regime,
        entry_price_bucket=_price_bucket(trade.entry_price),
        observations=observations,
    )

    if not observations:
        return ctx

    sig_obs = _signal_quote_at_or_before(observations, trade.side, signal_ts)
    if sig_obs:
        ctx.signal_ask = _side_ask(sig_obs, trade.side)
        ctx.signal_bid = _side_bid(sig_obs, trade.side)
        ctx.quote_age_sec = signal_ts - sig_obs.timestamp
        if ctx.seconds_left is None and sig_obs.seconds_left is not None:
            ctx.seconds_left = sig_obs.seconds_left
        if ctx.signal_ask is not None and ctx.signal_bid is not None:
            ctx.spread = round(ctx.signal_ask - ctx.signal_bid, 4)

    future_obs = [o for o in observations if o.timestamp > signal_ts]
    ctx.next_quote_timestamps = [o.timestamp for o in future_obs]

    for offset in (1, 2, 3, 5):
        ask, _ = _ask_at_or_after(observations, trade.side, signal_ts + offset)
        ctx.ask_at_offset[offset] = ask

    for window in (1, 3, 5):
        mn, mx = _min_max_ask_in_window(observations, trade.side, signal_ts, window)
        ctx.min_ask_window[window] = mn
        ctx.max_ask_window[window] = mx

    if ctx.signal_ask is not None:
        for obs in future_obs:
            ask = _side_ask(obs, trade.side)
            if ask is not None and abs(ask - ctx.signal_ask) <= 0.005:
                ctx.signal_ask_reavailable = True
                break

    ctx.btc_moves = _btc_moves_at_signal(observations, signal_ts)
    ctx.coverage_ok = sig_obs is not None and ctx.signal_ask is not None
    return ctx


def load_quote_paths(
    conn: sqlite3.Connection,
    trades: list[LiveTrade],
) -> dict[int, QuotePathContext]:
    return {t.id: reconstruct_quote_path(conn, t) for t in trades}
