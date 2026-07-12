"""Phase F.4.1 — Telegram message stage dedupe (one SHOCK/ENTRY/EXIT per event)."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.db import execute_with_retry

MSG_SHOCK = "SHOCK"
MSG_ENTRY = "ENTRY"
MSG_EXIT = "EXIT"

STAGE_NONE = "NONE"
STAGE_FINAL_SENT = "FINAL_SENT"
STAGE_ENTRY_SENT = "ENTRY_SENT"
STAGE_EXIT_SENT = "EXIT_SENT"

STAGE_FOR_MESSAGE_TYPE = {
    MSG_SHOCK: STAGE_FINAL_SENT,
    MSG_ENTRY: STAGE_ENTRY_SENT,
    MSG_EXIT: STAGE_EXIT_SENT,
}

SENT_STAGES = frozenset({STAGE_FINAL_SENT, STAGE_ENTRY_SENT, STAGE_EXIT_SENT})


def message_type_for_paper_update(update_type: str) -> str | None:
    if update_type == "PAPER_ENTRY":
        return MSG_ENTRY
    if update_type in ("TP", "STOP", "BE_STOP", "CLOSED"):
        return MSG_EXIT
    return None


def stage_already_sent(conn: Any, event_id: int, message_type: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM market_event_alert_log
        WHERE event_id = ? AND message_type = ?
          AND sent = 1 AND duplicate_prevented = 0
          AND telegram_message_stage IN (?, ?, ?)
        LIMIT 1
        """,
        (event_id, message_type, STAGE_FINAL_SENT, STAGE_ENTRY_SENT, STAGE_EXIT_SENT),
    ).fetchone()
    return row is not None


def record_duplicate_prevented(
    conn: Any,
    *,
    event_id: int,
    alert_type: str,
    message_type: str,
    dedupe_key: str,
) -> None:
    prevented_key = f"{dedupe_key}:prevented:{int(time.time() * 1000)}"
    execute_with_retry(
        conn,
        """
        INSERT INTO market_event_alert_log (
          event_id, alert_type, dedupe_key, message_text, sent, latency_ms,
          error, created_at, message_type, telegram_message_stage, duplicate_prevented
        ) VALUES (?, ?, ?, ?, 0, 0, 'duplicate_prevented', ?, ?, ?, 1)
        """,
        (
            event_id, alert_type, prevented_key, "",
            int(time.time()), message_type, STAGE_NONE,
        ),
    )


def duplicate_prevented_count(conn: Any, *, since_ts: int | None = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM market_event_alert_log WHERE duplicate_prevented = 1"
    params: tuple[Any, ...] = ()
    if since_ts is not None:
        sql += " AND created_at >= ?"
        params = (since_ts,)
    row = conn.execute(sql, params).fetchone()
    return int(row["n"] if row else 0)


def fetch_ai_summary_for_merge(conn: Any, event_id: int) -> str | None:
    """Latest AI commentary to embed in final shock alert (never standalone)."""
    row = conn.execute(
        """
        SELECT structured_output_json FROM market_event_ai_analyses
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if row:
        import json
        try:
            data = json.loads(row["structured_output_json"] or "{}")
            note = (data.get("short_commentary") or data.get("movement_interpretation") or "").strip()
            if note:
                return note.split("\n")[0][:200]
        except Exception:
            pass

    f0 = conn.execute(
        """
        SELECT summary_ru, summary_en FROM market_event_ai_analyses_f0
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if f0:
        text = f0["summary_ru"] or f0["summary_en"]
        if text:
            return str(text).split("\n")[0][:200]

    f2 = conn.execute(
        "SELECT ai_summary_v2_ru FROM market_events_signal_reports_f2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if f2 and f2["ai_summary_v2_ru"]:
        return str(f2["ai_summary_v2_ru"]).split("\n")[0][:200]
    return None


def merge_ai_into_shock_message(conn: Any, event_id: int, message: str) -> str:
    ai = fetch_ai_summary_for_merge(conn, event_id)
    if not ai:
        return message
    if ai in message:
        return message
    return f"{message}\n\nИИ (research)\n{ai}"
