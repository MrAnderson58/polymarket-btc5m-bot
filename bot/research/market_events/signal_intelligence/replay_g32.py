"""Phase G.3.2 — candidate outcome replay at 15m/30m/1h/2h/4h/24h horizons."""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.config import (
    G32_DEFAULT_STOP_PCT,
    G32_ENABLED,
    G32_REPLAY_INTERVAL_SEC,
)

logger = logging.getLogger(__name__)

_TABLE = "market_candidate_outcomes_g32"
STATUS_OPEN = "OPEN"
STATUS_COMPLETE = "COMPLETE"

HORIZONS: tuple[tuple[str, int], ...] = (
    ("price_15m", 900),
    ("price_30m", 1800),
    ("price_1h", 3600),
    ("price_2h", 7200),
    ("price_4h", 14400),
    ("price_24h", 86400),
)


def _resolve_price(conn: Any, *, symbol: str, ts: int | None = None) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=300)
    if not bars:
        return None
    if ts is None:
        return float(bars[-1].close)
    best = None
    for b in bars:
        if b.open_ts <= ts:
            best = b
        else:
            break
    if best:
        return float(best.close)
    return float(bars[0].close) if bars else None


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _tp_sl_pct(rr: float | None) -> tuple[float, float]:
    stop = G32_DEFAULT_STOP_PCT
    rr_val = rr if rr and rr > 0 else 2.5
    tp = min(5.0, stop * rr_val * 0.45)
    return tp, stop


def seed_outcome_g32(
    conn: Any,
    *,
    candidate_id: int,
    symbol: str,
    direction: str | None,
    created_at: int,
    rr: float | None = None,
) -> int | None:
    if not G32_ENABLED:
        return None
    existing = conn.execute(
        f"SELECT id FROM {_TABLE} WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing:
        return int(existing["id"])

    entry = _resolve_price(conn, symbol=symbol, ts=created_at)
    now = int(time.time())
    return insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          candidate_id, symbol, direction, created_at, price_entry,
          max_profit_pct, max_drawdown_pct, would_hit_tp, would_hit_sl,
          best_rr, replay_status, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?)
        """,
        (candidate_id, symbol, direction, created_at, entry, rr, STATUS_OPEN, now),
    )


def _update_outcome_row(conn: Any, row: Any, *, now: int) -> None:
    entry = float(row["price_entry"] or 0)
    if entry <= 0:
        entry = _resolve_price(conn, symbol=row["symbol"], ts=int(row["created_at"])) or 0.0
        if entry > 0:
            conn.execute(
                f"UPDATE {_TABLE} SET price_entry = ? WHERE id = ?",
                (entry, row["id"]),
            )

    if entry <= 0:
        return

    direction = str(row["direction"] or "LONG").upper()
    is_long = direction == "LONG"
    age = now - int(row["created_at"])
    current = _resolve_price(conn, symbol=row["symbol"])
    if not current:
        return

    updates: dict[str, float] = {}
    max_profit = float(row["max_profit_pct"] or 0)
    max_dd = float(row["max_drawdown_pct"] or 0)

    for col, sec in HORIZONS:
        if age >= sec and row[col] is None:
            price_at = _resolve_price(conn, symbol=row["symbol"])
            if price_at:
                updates[col] = price_at
                pnl = _pnl_pct(entry, price_at, is_long=is_long)
                max_profit = max(max_profit, pnl)
                max_dd = min(max_dd, pnl)

    pnl_now = _pnl_pct(entry, current, is_long=is_long)
    max_profit = max(max_profit, pnl_now)
    max_dd = min(max_dd, pnl_now)

    cand = conn.execute(
        "SELECT rr FROM market_candidate_g31 WHERE id = ?",
        (row["candidate_id"],),
    ).fetchone()
    rr = float(cand["rr"]) if cand and cand["rr"] is not None else float(row["best_rr"] or 2.5)
    tp_pct, stop_pct = _tp_sl_pct(rr)
    would_tp = 1 if max_profit >= tp_pct else 0
    would_sl = 1 if max_dd <= -stop_pct else 0
    best_rr = round(max_profit / stop_pct, 2) if stop_pct > 0 and max_profit > 0 else 0.0

    status = STATUS_COMPLETE if row["price_24h"] is not None or updates.get("price_24h") else STATUS_OPEN
    if age >= 86400:
        status = STATUS_COMPLETE

    set_parts = [
        "max_profit_pct = ?", "max_drawdown_pct = ?", "would_hit_tp = ?",
        "would_hit_sl = ?", "best_rr = ?", "replay_status = ?", "updated_at = ?",
    ]
    params: list[Any] = [max_profit, max_dd, would_tp, would_sl, best_rr, status, now]
    for col, val in updates.items():
        set_parts.insert(0, f"{col} = ?")
        params.insert(0, val)
    params.append(row["id"])
    conn.execute(
        f"UPDATE {_TABLE} SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params),
    )


def update_open_outcomes_g32(conn: Any, *, limit: int = 500) -> int:
    """Refresh open candidate outcomes — run every 5 minutes."""
    if not G32_ENABLED:
        return 0

    now = int(time.time())
    rows = conn.execute(
        f"""
        SELECT * FROM {_TABLE}
        WHERE replay_status = ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (STATUS_OPEN, limit),
    ).fetchall()

    n = 0
    for row in rows:
        try:
            _update_outcome_row(conn, row, now=now)
            n += 1
        except Exception as exc:
            logger.debug("g32 replay update skipped id=%s: %s", row["id"], exc)
    return n


def maybe_run_replay_g32(conn: Any) -> int:
    """Run replay updater if interval elapsed."""
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state

    now = int(time.time())
    last_raw = get_g3_ops_state(conn, "last_g32_replay_ts")
    last = int(last_raw) if last_raw else 0
    if last and (now - last) < G32_REPLAY_INTERVAL_SEC:
        return 0
    n = update_open_outcomes_g32(conn)
    set_g3_ops_state(conn, "last_g32_replay_ts", str(now))
    return n


def fetch_top_replay_g32(conn: Any, *, limit: int = 20, hours: int = 168) -> list[Any]:
    since = int(time.time()) - hours * 3600
    return conn.execute(
        f"""
        SELECT o.*, c.confidence, c.market_score, c.candidate_state, c.rejection_reason,
               c.rr AS candidate_rr, c.liquidity_score
        FROM {_TABLE} o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        WHERE o.created_at >= ? AND o.max_profit_pct IS NOT NULL
        ORDER BY o.max_profit_pct DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()


def format_candidate_replay_report(conn: Any, *, limit: int = 20) -> str:
    rows = fetch_top_replay_g32(conn, limit=limit)
    if not rows:
        return "No replay outcomes yet — candidates need 15m+ to accumulate."

    lines = ["G3.2 Candidate Replay — TOP profitable (incl. non-Telegram)", ""]
    for r in rows:
        sent_note = "Telegram sent" if r["candidate_state"] == "accepted" else "Not sent"
        lines.extend([
            str(r["symbol"]),
            "",
            f"+{float(r['max_profit_pct']):.1f}%",
            "",
            "Confidence",
            f"{float(r['confidence'] or 0):.1f}",
            "",
            "Outcome",
            f"TP={'yes' if r['would_hit_tp'] else 'no'} SL={'yes' if r['would_hit_sl'] else 'no'}",
            "",
            "Rejection",
            str(r["rejection_reason"] or "—"),
            "",
            sent_note,
            "",
            "------------",
            "",
        ])
    return "\n".join(lines).rstrip()


def replay_dashboard_rows(conn: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT o.candidate_id, o.symbol, o.max_profit_pct, o.max_drawdown_pct,
               o.would_hit_tp, o.would_hit_sl, o.best_rr, o.replay_status,
               c.confidence, c.market_score, c.candidate_state, c.rejection_reason,
               c.direction
        FROM {_TABLE} o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        ORDER BY o.updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        worth = float(r["max_profit_pct"] or 0) >= 2.0 and r["candidate_state"] != "accepted"
        out.append({
            "candidate": {
                "id": r["candidate_id"],
                "symbol": r["symbol"],
                "confidence": r["confidence"],
                "market_score": r["market_score"],
                "direction": r["direction"],
                "state": r["candidate_state"],
            },
            "outcome": {
                "max_profit_pct": r["max_profit_pct"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "would_hit_tp": bool(r["would_hit_tp"]),
                "would_hit_sl": bool(r["would_hit_sl"]),
                "best_rr": r["best_rr"],
                "status": r["replay_status"],
            },
            "blocker": r["rejection_reason"],
            "worth_it": worth,
        })
    return out
