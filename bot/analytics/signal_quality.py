"""Section 36 — signal quality score vs realized PnL."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.no_c_btc_filter import compute_btc_move_30s
from bot.no_c_filter_shadow import _btc_price_at
from bot.report.analytics import ENTRY_PRICES, trade_pnl


def _score_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    entry_pf_map: dict[float, float],
) -> int:
    score = 50.0
    entry = round(float(trade["entry_price"]), 2)
    nearest = min(ENTRY_PRICES, key=lambda p: abs(p - entry))
    pf = entry_pf_map.get(nearest, 1.0)
    if pf >= 2.5:
        score += 25
    elif pf >= 1.5:
        score += 15
    elif pf < 1.0:
        score -= 20

    entry_ts = int(trade["entry_ts"])
    current = _btc_price_at(conn, entry_ts)
    if current is not None:
        move = compute_btc_move_30s(conn, current_btc=current, now_ts=entry_ts)
        if move is not None:
            if trade["side"] == "NO" and move < 0:
                score += 15
            elif trade["side"] == "YES" and move > 0:
                score += 15
            elif abs(move) > 20:
                score -= 10

    prefix = "yes" if trade["side"] == "YES" else "no"
    row = conn.execute(
        f"""
        SELECT {prefix}_bid AS bid, {prefix}_ask AS ask
        FROM market_checks
        WHERE market_slug = ?
        ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
        LIMIT 1
        """,
        (trade["market_slug"], entry_ts),
    ).fetchone()
    if row and row["bid"] is not None and row["ask"] is not None:
        spread = float(row["ask"]) - float(row["bid"])
        if spread <= 0.015:
            score += 10
        elif spread > 0.03:
            score -= 15

    return max(0, min(100, int(round(score))))


def build_signal_quality(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
    report: dict[str, Any],
) -> dict[str, Any]:
    entry_rows = report.get("entry_price_analysis", {}).get("rows", [])
    entry_pf_map = {r["entry_price"]: r["profit_factor"] for r in entry_rows if r["trades"] >= 3}

    scored: list[dict[str, Any]] = []
    for trade in closed[-80:]:
        pnl = trade_pnl(trade)
        score = _score_trade(conn, trade, entry_pf_map)
        scored.append(
            {
                "trade_id": int(trade["id"]),
                "score": score,
                "pnl_pct": round(pnl, 2),
                "entry": float(trade["entry_price"]),
                "label": "Strong" if score >= 75 else "Weak" if score < 40 else "Average",
            }
        )

    high = [s for s in scored if s["score"] >= 75]
    low = [s for s in scored if s["score"] < 40]
    high_wr = sum(1 for s in high if s["pnl_pct"] > 0) / len(high) if high else 0.0
    low_wr = sum(1 for s in low if s["pnl_pct"] > 0) / len(low) if low else 0.0

    return {
        "trades_scored": len(scored),
        "high_score_win_rate": high_wr,
        "low_score_win_rate": low_wr,
        "score_predicts_pnl": high_wr > low_wr + 0.1 if high and low else None,
        "samples": scored[-20:],
    }
