"""Section 35 — extended market regime engine with per-regime PF."""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from bot.er_btc_direction_stats import BTC_FLAT_THRESHOLD_USD, _exit_ts, _nearest_btc_price
from bot.no_c_filter_shadow import _btc_price_at
from bot.report.analytics import _metrics, trade_pnl


def _classify_regime(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
) -> str:
    entry_ts = int(trade["entry_ts"])
    slug = trade["market_slug"]
    prefix = "yes" if trade["side"] == "YES" else "no"
    row = conn.execute(
        f"""
        SELECT {prefix}_bid AS bid, {prefix}_ask AS ask, btc_price
        FROM market_checks
        WHERE market_slug = ?
        ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
        LIMIT 1
        """,
        (slug, entry_ts),
    ).fetchone()

    btc_move_30 = None
    current = _btc_price_at(conn, entry_ts)
    if current is not None:
        past = _btc_price_at(conn, entry_ts - 30)
        if past is not None:
            btc_move_30 = current - past

    btc_entry = _nearest_btc_price(conn, market_slug=slug, target_ts=entry_ts)
    btc_exit = _nearest_btc_price(conn, market_slug=slug, target_ts=_exit_ts(trade))
    trade_move = (btc_exit - btc_entry) if btc_entry and btc_exit else 0.0

    spread = 0.0
    if row and row["bid"] is not None and row["ask"] is not None:
        spread = float(row["ask"]) - float(row["bid"])

    if spread > 0.025:
        return "Low Liquidity"

    if btc_move_30 is not None and abs(btc_move_30) > 30:
        return "News Spike"

    if abs(trade_move) >= BTC_FLAT_THRESHOLD_USD * 3:
        if trade_move > 0:
            return "Strong Uptrend"
        return "Panic"

    if abs(trade_move) >= BTC_FLAT_THRESHOLD_USD:
        return "Weak Uptrend" if trade_move > 0 else "Expansion"

    vol_rows = conn.execute(
        """
        SELECT btc_price FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
        """,
        (entry_ts - 60, entry_ts),
    ).fetchall()
    prices = [float(r["btc_price"]) for r in vol_rows if r["btc_price"] is not None]
    vol = statistics.pstdev(prices) if len(prices) >= 3 else 0.0

    if vol < 5:
        return "Compression"
    if vol > 20:
        return "Expansion"
    if abs(trade_move) < BTC_FLAT_THRESHOLD_USD / 2:
        return "Mean Reversion"
    return "Range"


def build_regime_engine(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for trade in closed:
        regime = _classify_regime(conn, trade)
        buckets.setdefault(regime, []).append(trade_pnl(trade))

    regimes = {
        name: _metrics(pnls)
        for name, pnls in sorted(buckets.items(), key=lambda x: -len(x[1]))
    }
    best = max(regimes.items(), key=lambda x: x[1]["profit_factor"] if x[1]["trades"] >= 5 else -1)[0] if regimes else None
    worst = min(
        (r for r in regimes.items() if r[1]["trades"] >= 5),
        key=lambda x: x[1]["profit_factor"],
        default=(None, {}),
    )[0]

    return {
        "regimes": regimes,
        "best_regime": best,
        "worst_regime": worst,
    }
