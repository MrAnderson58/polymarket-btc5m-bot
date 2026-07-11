"""Phase F.1 — persist realized paper outcomes vs signal report."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.signal_report_f1 import load_signal_report_f1


def _ai_agreed(conn: Any, event_id: int, pnl_pct: float) -> bool | None:
    row = conn.execute(
        """
        SELECT bias, reversal_probability FROM market_event_ai_analyses_f0
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None
    bias = str(row["bias"] or "").upper()
    if bias in ("FADE", "WAIT"):
        return pnl_pct > 0
    if bias == "CONTINUATION":
        return pnl_pct < 0
    return None


def record_outcome_f1(
    conn: Any,
    *,
    event_id: int,
    paper_run_id: int | None,
    pnl_pct: float,
    holding_seconds: int,
) -> None:
    report = load_signal_report_f1(conn, event_id)
    if not report:
        return

    expected = report.expected_target_pct
    hist_matched = None
    if expected is not None:
        hist_matched = pnl_pct >= expected * 0.5

    ai_agreed = _ai_agreed(conn, event_id, pnl_pct)
    outcome = {
        "pnl_pct": pnl_pct,
        "holding_seconds": holding_seconds,
        "expected_target_pct": expected,
        "ai_agreed": ai_agreed,
        "historical_matched": hist_matched,
    }

    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_signal_outcomes_f1 (
          event_id, paper_run_id, confidence_score, entry_recommendation,
          expected_target_pct, realized_pnl_pct, holding_seconds,
          ai_agreed, historical_matched, outcome_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            paper_run_id,
            report.confidence_score,
            report.entry_recommendation,
            report.expected_target_pct,
            pnl_pct,
            holding_seconds,
            1 if ai_agreed else 0 if ai_agreed is False else None,
            1 if hist_matched else 0 if hist_matched is False else None,
            json.dumps(outcome, ensure_ascii=False),
            int(time.time()),
        ),
    )
