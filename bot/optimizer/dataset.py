"""Build and persist trade_features dataset (Block 1)."""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from bot.config import ER_V2_STOP_LOSS_PCT, TRAILING_ACTIVATION_PROFIT, TRAILING_OFFSET
from bot.er_btc_direction_stats import _exit_ts
from bot.no_c_filter_shadow import _btc_price_at
from bot.optimizer.constants import SETTLEMENT_BID, SOURCE_TABLE
from bot.report.analytics import _bid_column, fetch_bid_series, trade_pnl


def _btc_move_at(conn: sqlite3.Connection, *, entry_ts: int, lookback: int) -> float | None:
    current = _btc_price_at(conn, entry_ts)
    if current is None:
        return None
    past = _btc_price_at(conn, entry_ts - lookback)
    if past is None:
        return None
    return current - past


def _btc_volatility(conn: sqlite3.Connection, *, entry_ts: int, window: int) -> float | None:
    rows = conn.execute(
        """
        SELECT btc_price FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
        ORDER BY checked_at ASC
        """,
        (entry_ts - window, entry_ts),
    ).fetchall()
    prices = [float(r["btc_price"]) for r in rows if r["btc_price"] is not None]
    if len(prices) < 2:
        return None
    return statistics.pstdev(prices)


def _quote_at_entry(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    side: str,
    entry_ts: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    prefix = "yes" if side == "YES" else "no"
    row = conn.execute(
        f"""
        SELECT {prefix}_bid AS bid, {prefix}_ask AS ask,
               strike_price, btc_price, seconds_remaining
        FROM market_checks
        WHERE market_slug = ?
        ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
        LIMIT 1
        """,
        (market_slug, entry_ts),
    ).fetchone()
    if row is None:
        return None, None, None, None
    bid = float(row["bid"]) if row["bid"] is not None else None
    ask = float(row["ask"]) if row["ask"] is not None else None
    strike = float(row["strike_price"]) if row["strike_price"] is not None else None
    btc = float(row["btc_price"]) if row["btc_price"] is not None else None
    dist = abs(btc - strike) if btc is not None and strike is not None else None
    return bid, ask, dist, float(row["seconds_remaining"]) if row["seconds_remaining"] else None


def _mfe_mae(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
) -> tuple[float | None, float | None]:
    entry = float(trade["entry_price"])
    series = fetch_bid_series(
        conn,
        market_slug=trade["market_slug"],
        side=trade["side"],
        start_ts=int(trade["entry_ts"]),
        end_ts=_exit_ts(trade),
    )
    if not series:
        peak = trade["max_price_seen"]
        if peak is None:
            return None, None
        mfe = (float(peak) - entry) / entry * 100
        return mfe, trade_pnl(trade)
    bids = [b for _, b in series]
    return (max(bids) - entry) / entry * 100, (min(bids) - entry) / entry * 100


def build_feature_row(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    cache: Any | None = None,
) -> dict[str, Any]:
    if cache is not None:
        from bot.perf.feature_store import build_feature_row_cached

        return build_feature_row_cached(trade, cache)
    from bot.perf.market_cache import MarketDataCache
    from bot.perf.feature_store import build_feature_row_cached

    built = MarketDataCache.build(conn, [trade])
    return build_feature_row_cached(trade, built)


def rebuild_trade_features(conn: sqlite3.Connection) -> int:
    from bot.perf.feature_store import build_feature_row_cached, _upsert_feature_row
    from bot.perf.market_cache import MarketDataCache

    conn.execute("DELETE FROM trade_features WHERE source_table = ?", (SOURCE_TABLE,))
    trades = conn.execute(
        f"""
        SELECT * FROM {SOURCE_TABLE}
        WHERE status = 'closed'
        ORDER BY entry_ts ASC
        """
    ).fetchall()
    if not trades:
        return 0
    cache = MarketDataCache.build(conn, trades)
    count = 0
    for trade in trades:
        _upsert_feature_row(conn, build_feature_row_cached(trade, cache))
        count += 1
    return count


def load_trade_features(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM trade_features
        WHERE source_table = ?
        ORDER BY entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()


def build_replay_contexts(
    conn: sqlite3.Connection,
    trades: list[sqlite3.Row] | None = None,
) -> list:
    from bot.optimizer.replay import TradeReplay

    if trades is None:
        trades = conn.execute(
            f"""
            SELECT * FROM {SOURCE_TABLE}
            WHERE status = 'closed'
            ORDER BY entry_ts ASC
            """
        ).fetchall()
    contexts: list[TradeReplay] = []
    for trade in trades:
        entry_ts = int(trade["entry_ts"])
        exit_ts = _exit_ts(trade)
        series = fetch_bid_series(
            conn,
            market_slug=trade["market_slug"],
            side=trade["side"],
            start_ts=entry_ts,
            end_ts=exit_ts,
        )
        bids = [(ts - entry_ts, bid) for ts, bid in series if bid < SETTLEMENT_BID]
        move = _btc_move_at(conn, entry_ts=entry_ts, lookback=30)
        contexts.append(
            TradeReplay(
                trade_id=int(trade["id"]),
                entry_price=float(trade["entry_price"]),
                entry_ts=entry_ts,
                actual_pnl=trade_pnl(trade),
                btc_move_30s=move,
                bids=bids,
            )
        )
    return contexts
