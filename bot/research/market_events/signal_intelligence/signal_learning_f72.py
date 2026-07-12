"""Phase F.7.2 — self-learning from closed signal outcomes."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import F6_ENABLED, F72_ENABLED


def _pattern_key(pattern: dict[str, Any]) -> str:
    parts = [
        pattern.get("trend_stage") or "unknown_trend",
        pattern.get("funding_regime") or "unknown_funding",
        pattern.get("oi_regime") or "unknown_oi",
        pattern.get("long_trend_stage") or "",
    ]
    return "|".join(str(p) for p in parts if p)


def _update_pattern_stats(conn: Any, *, pattern_key: str, pnl: float, rr: float, win: bool) -> None:
    row = conn.execute(
        "SELECT * FROM market_events_pattern_stats_f72 WHERE pattern_key = ?",
        (pattern_key,),
    ).fetchone()
    now = int(time.time())
    if row:
        n = int(row["signals_count"]) + 1
        wins = int(row["wins"]) + (1 if win else 0)
        avg_pnl = (float(row["avg_pnl"]) * int(row["signals_count"]) + pnl) / n
        avg_rr = (float(row["avg_rr"]) * int(row["signals_count"]) + rr) / n
        conn.execute(
            """
            UPDATE market_events_pattern_stats_f72
            SET signals_count = ?, wins = ?, avg_pnl = ?, avg_rr = ?, updated_at = ?
            WHERE pattern_key = ?
            """,
            (n, wins, round(avg_pnl, 3), round(avg_rr, 2), now, pattern_key),
        )
    else:
        insert_returning_id(
            conn,
            """
            INSERT INTO market_events_pattern_stats_f72 (
              pattern_key, signals_count, wins, avg_pnl, avg_rr, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?)
            """,
            (pattern_key, 1 if win else 0, round(pnl, 3), round(rr, 2), now),
        )


def apply_signal_learning_f72(conn: Any, *, event_id: int) -> None:
    if not F72_ENABLED:
        return

    row = conn.execute(
        "SELECT * FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row or row["status"] != "CLOSED":
        return

    pnl = float(row["pnl_pct"] or 0)
    rr = float(row["risk_reward"] or 0)
    win = pnl > 0
    pattern = json.loads(row["pattern_json"] or "{}")
    _update_pattern_stats(conn, pattern_key=_pattern_key(pattern), pnl=pnl, rr=rr, win=win)

    if not F6_ENABLED:
        return

    channel = row["author_channel"]
    if not channel:
        return

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
        record_paper_close_f6,
    )

    duration = int(row["holding_seconds"] or 0)
    exit_reason = str(row["exit_reason"] or "CLOSED")
    record_paper_close_f6(
        conn,
        event_id=event_id,
        net_return=pnl,
        exit_reason=exit_reason,
        duration_seconds=duration,
        mfe=float(row["max_profit_pct"]),
        mae=-float(row["max_drawdown_pct"]),
        gross_return=pnl,
        reversal_variant=f"f72_{event_id}",
        exit_variant="signal_outcome",
    )
