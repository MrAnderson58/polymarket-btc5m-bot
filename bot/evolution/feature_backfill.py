"""Lightweight trade_features backfill for regime shadow (no execution imports)."""

from __future__ import annotations

import sqlite3

SOURCE_TABLE = "early_reversion_v2_trades"


def regime_label_from_features(
    move_30: float | None,
    spread: float | None,
) -> str:
    """Mirror of bot.ai_agent.features.regime_label_from_features (no heavy imports)."""
    spread = float(spread or 0)
    if spread > 0.025:
        return "Low Liquidity"
    if move_30 is not None and abs(move_30) > 30:
        return "News Spike"
    if move_30 is not None and move_30 > 15:
        return "Strong Uptrend"
    if move_30 is not None and move_30 < -15:
        return "Panic"
    if move_30 is not None and abs(move_30) <= 5:
        return "Mean Reversion"
    return "Range"


def _btc_move_30s(conn: sqlite3.Connection, entry_ts: int, btc_now: float | None) -> float | None:
    if btc_now is None:
        return None
    row = conn.execute(
        """
        SELECT btc_price FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY checked_at DESC LIMIT 1
        """,
        (entry_ts - 30,),
    ).fetchone()
    if not row or row["btc_price"] is None:
        return None
    return float(btc_now) - float(row["btc_price"])


def _btc_at_entry(conn: sqlite3.Connection, entry_ts: int) -> float | None:
    row = conn.execute(
        """
        SELECT btc_price FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY checked_at DESC LIMIT 1
        """,
        (entry_ts,),
    ).fetchone()
    if row and row["btc_price"] is not None:
        return float(row["btc_price"])
    return None


def _spread_at_entry(
    conn: sqlite3.Connection,
    market_slug: str,
    entry_ts: int,
    side: str,
) -> float | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask FROM market_checks
        WHERE market_slug = ?
          AND cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY checked_at DESC LIMIT 1
        """,
        (market_slug, entry_ts),
    ).fetchone()
    if not row:
        return None
    if side == "YES":
        bid, ask = row["yes_bid"], row["yes_ask"]
    else:
        bid, ask = row["no_bid"], row["no_ask"]
    if bid is None or ask is None:
        return None
    return float(ask) - float(bid)


def backfill_missing_regime_features(conn: sqlite3.Connection) -> int:
    """Insert minimal trade_features rows so regime shadow can evaluate."""
    missing = conn.execute(
        f"""
        SELECT t.*
        FROM {SOURCE_TABLE} t
        LEFT JOIN trade_features f
          ON f.trade_id = t.id AND f.source_table = ?
        WHERE t.status = 'closed' AND f.trade_id IS NULL
        ORDER BY t.entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()
    if not missing:
        return 0

    count = 0
    for trade in missing:
        entry_ts = int(trade["entry_ts"])
        side = str(trade["side"])
        btc_now = _btc_at_entry(conn, entry_ts)
        move_30 = _btc_move_30s(conn, entry_ts, btc_now)
        spread = _spread_at_entry(conn, str(trade["market_slug"]), entry_ts, side)
        regime = regime_label_from_features(move_30, spread)
        pnl = float(trade["pnl_percent"] or 0)

        conn.execute(
            """
            INSERT OR REPLACE INTO trade_features (
                trade_id, source_table, market_slug, strategy_name, side,
                entry_ts, entry_price, exit_price, pnl, pnl_usdc,
                is_win, is_loss, is_stop, is_time_stop, is_trailing,
                exit_reason, regime_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(trade["id"]),
                SOURCE_TABLE,
                trade["market_slug"],
                trade["strategy_name"],
                side,
                entry_ts,
                float(trade["entry_price"]),
                float(trade["exit_price"]) if trade["exit_price"] is not None else None,
                pnl,
                float(trade["pnl_usdc"]) if trade["pnl_usdc"] is not None else None,
                int(pnl > 0),
                int(pnl <= 0),
                int((trade["exit_reason"] or "") == "STOP_LOSS"),
                0,
                0,
                trade["exit_reason"] or "",
                regime,
            ),
        )
        count += 1
    return count
