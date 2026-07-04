"""Bidirectional Momentum Shadow Strategy — observe-only virtual trading.

Watches every eligible market, independently chooses YES/NO/SKIP,
creates virtual trades only. Never calls execution or places orders.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from bot.strategy.bidirectional_momentum import (
    DirectionDecision,
    EntryConfig,
    ExitConfig,
    evaluate_direction,
    get_exit_config,
)
from bot.research.features import MovementFeatures

logger = logging.getLogger(__name__)

SHADOW_ENTRY_CONFIG = EntryConfig(
    min_confidence=0.55,
    min_move_30s=5.0,
    yes_max_ask=0.45,
    no_max_ask=0.45,
    no_avoid_zone_lo=0.28,
    no_avoid_zone_hi=0.35,
    max_spread=0.06,
    min_consistency=0.5,
    min_seconds_from_start=15,
    max_seconds_from_start=250,
    skip_regimes=("CHOP", "REVERSAL"),
)


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create shadow tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bidirectional_shadow_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            decision TEXT NOT NULL,
            confidence REAL,
            probability_yes REAL,
            probability_no REAL,
            regime TEXT,
            reason TEXT,
            btc_move_30s REAL,
            entry_price REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bidirectional_shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_regime TEXT,
            entry_confidence REAL,
            entry_reason TEXT,
            max_price_seen REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            holding_time_seconds REAL,
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_obs_market
            ON bidirectional_shadow_observations(market_slug, timestamp);
        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_trades_status
            ON bidirectional_shadow_trades(status);
    """)


def record_observation(
    conn: sqlite3.Connection,
    decision: DirectionDecision,
    market_slug: str,
    timestamp: int,
    entry_price: float | None = None,
) -> None:
    """Record a shadow observation (decision point)."""
    conn.execute(
        """INSERT INTO bidirectional_shadow_observations
           (market_slug, timestamp, decision, confidence, probability_yes,
            probability_no, regime, reason, btc_move_30s, entry_price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            market_slug,
            timestamp,
            decision.decision,
            decision.confidence,
            decision.probability_yes,
            decision.probability_no,
            decision.regime,
            decision.reason,
            decision.features.get("btc_move_30s"),
            entry_price,
        ),
    )


def open_shadow_trade(
    conn: sqlite3.Connection,
    decision: DirectionDecision,
    market_slug: str,
    window_start_ts: int,
    entry_price: float,
    entry_ts: int,
) -> int:
    """Open a virtual shadow trade. Returns trade id."""
    cur = conn.execute(
        """INSERT INTO bidirectional_shadow_trades
           (market_slug, window_start_ts, side, status, entry_price, entry_ts,
            entry_regime, entry_confidence, entry_reason, max_price_seen)
           VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)""",
        (
            market_slug,
            window_start_ts,
            decision.decision,
            entry_price,
            entry_ts,
            decision.regime,
            decision.confidence,
            decision.reason,
            entry_price,
        ),
    )
    return cur.lastrowid


def close_shadow_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    pnl_pct: float,
    holding_time: float,
) -> None:
    """Close a virtual shadow trade."""
    conn.execute(
        """UPDATE bidirectional_shadow_trades
           SET status='closed', exit_price=?, exit_reason=?, pnl_pct=?,
               holding_time_seconds=?, closed_at=datetime('now')
           WHERE id=?""",
        (exit_price, exit_reason, pnl_pct, holding_time, trade_id),
    )


def get_open_shadow_trade(conn: sqlite3.Connection, market_slug: str) -> dict | None:
    """Get open shadow trade for a market."""
    row = conn.execute(
        "SELECT * FROM bidirectional_shadow_trades WHERE market_slug=? AND status='open' LIMIT 1",
        (market_slug,),
    ).fetchone()
    return dict(row) if row else None


def shadow_state(conn: sqlite3.Connection) -> dict:
    """Get current shadow strategy state for reporting."""
    ensure_tables(conn)

    total_obs = conn.execute(
        "SELECT COUNT(*) FROM bidirectional_shadow_observations"
    ).fetchone()[0]

    decisions = conn.execute("""
        SELECT decision, COUNT(*) as n
        FROM bidirectional_shadow_observations
        GROUP BY decision
    """).fetchall()
    decision_counts = {r["decision"]: r["n"] for r in decisions}

    trades = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_n,
               SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
               SUM(CASE WHEN side='YES' AND status='closed' THEN 1 ELSE 0 END) as yes_closed,
               SUM(CASE WHEN side='NO' AND status='closed' THEN 1 ELSE 0 END) as no_closed,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) as gross_profit,
               SUM(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) ELSE 0 END) as gross_loss
        FROM bidirectional_shadow_trades
    """).fetchone()

    closed_n = trades["closed"] or 0
    open_n = trades["open_n"] or 0
    gp = trades["gross_profit"] or 0
    gl = trades["gross_loss"] or 0
    pf = gp / gl if gl else 0.0
    wr = (trades["wins"] or 0) / closed_n * 100 if closed_n else 0.0

    # Side-specific PF
    yes_pf = _side_pf(conn, "YES")
    no_pf = _side_pf(conn, "NO")

    # Max drawdown and consecutive losses
    pnls = conn.execute("""
        SELECT pnl_pct FROM bidirectional_shadow_trades
        WHERE status='closed' ORDER BY entry_ts
    """).fetchall()
    max_dd = 0.0
    max_cl = 0
    if pnls:
        equity = 0.0
        peak = 0.0
        streak = 0
        for row in pnls:
            p = row["pnl_pct"] or 0
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
            if p <= 0:
                streak += 1
                if streak > max_cl:
                    max_cl = streak
            else:
                streak = 0

    # Regime breakdown
    regimes = conn.execute("""
        SELECT entry_regime, COUNT(*) as n,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               SUM(pnl_pct) as total_pnl
        FROM bidirectional_shadow_trades WHERE status='closed'
        GROUP BY entry_regime
    """).fetchall()
    regime_stats = {}
    for r in regimes:
        regime_stats[r["entry_regime"] or "UNKNOWN"] = {
            "n": r["n"], "wins": r["wins"] or 0, "pnl": round(r["total_pnl"] or 0, 1)
        }

    # Status determination
    status = "COLLECTING"
    if closed_n >= 100:
        if pf >= 1.25 and yes_pf > 1 and no_pf > 1:
            status = "READY_FOR_PAPER_REVIEW"
        elif pf < 0.9:
            status = "REJECTED"
        else:
            status = "VALIDATING"

    return {
        "status": status,
        "observations": total_obs,
        "decisions": decision_counts,
        "trades_total": trades["total"] or 0,
        "trades_open": open_n,
        "trades_closed": closed_n,
        "yes_trades": trades["yes_closed"] or 0,
        "no_trades": trades["no_closed"] or 0,
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "yes_pf": round(yes_pf, 3),
        "no_pf": round(no_pf, 3),
        "gross_profit": round(gp, 1),
        "gross_loss": round(gl, 1),
        "max_dd": round(max_dd, 1),
        "max_consecutive_losses": max_cl,
        "regime_stats": regime_stats,
        "progress": f"{closed_n} / 100",
    }


def _side_pf(conn: sqlite3.Connection, side: str) -> float:
    row = conn.execute("""
        SELECT SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) as gp,
               SUM(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) ELSE 0 END) as gl
        FROM bidirectional_shadow_trades
        WHERE side = ? AND status = 'closed'
    """, (side,)).fetchone()
    gp = row["gp"] or 0
    gl = row["gl"] or 0
    return gp / gl if gl else 0.0
