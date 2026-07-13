"""Phase F.7.3 — near miss, detector stats, threshold optimizer, dashboard."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.event_types import DETECTOR_IDS
from bot.research.market_events.signal_intelligence.config import F7_MIN_FINAL_CONFIDENCE
from bot.research.market_events.signal_intelligence.near_miss_f73 import (
    CAT_BTC,
    CAT_CONFIDENCE,
    CAT_CONFIRMATION,
    CAT_FUNDING,
    CAT_MARKET_SCORE,
    CAT_PRIORITY,
    CAT_TREND,
    CAT_VOLUME,
)


def _since_ts(days: int) -> int:
    return int(time.time()) - days * 86400


def _day_start_ts(day_offset: int = 0) -> int:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_offset:
        return int(today.timestamp()) - day_offset * 86400
    return int(today.timestamp())


def near_miss_report(conn: Any, *, days: int = 1) -> str:
    since = _since_ts(days)
    label = "Yesterday" if days == 1 else f"Last {days} days"

    candidates = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?",
        (since,),
    ).fetchone()["n"]

    sent = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72
        WHERE entry_time >= ?
        """,
        (since,),
    ).fetchone()["n"]

    rejected = conn.execute(
        f"SELECT COUNT(*) AS n FROM market_events_near_miss_f73 WHERE event_ts >= ?",
        (since,),
    ).fetchone()["n"]

    rows = conn.execute(
        """
        SELECT rejection_category, COUNT(*) AS n
        FROM market_events_near_miss_f73
        WHERE event_ts >= ?
        GROUP BY rejection_category
        ORDER BY n DESC
        """,
        (since,),
    ).fetchall()

    lines = [
        label,
        "",
        "Candidates",
        str(int(candidates or 0)),
        "",
        "Sent",
        str(int(sent or 0)),
        "",
        "Rejected",
        str(int(rejected or 0)),
        "",
        "Reasons",
        "",
    ]
    for r in rows:
        lines.append(str(r["rejection_category"]))
        lines.append(str(int(r["n"])))
        lines.append("")
    if not rows:
        lines.append("—")
    return "\n".join(lines).rstrip()


def _detector_events(conn: Any, detector_id: str, since: int | None) -> list[Any]:
    sql = "SELECT id, detector_triggers_json FROM market_events WHERE 1=1"
    params: list[Any] = []
    if since is not None:
        sql += " AND event_ts >= ?"
        params.append(since)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        try:
            triggers = json.loads(r["detector_triggers_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            triggers = []
        if detector_id in triggers:
            out.append(r)
    return out


def _accepted_event_ids(conn: Any, since: int | None) -> set[int]:
    sql = "SELECT event_id FROM market_events_signal_outcomes_f72"
    params: list[Any] = []
    if since is not None:
        sql += " WHERE entry_time >= ?"
        params.append(since)
    rows = conn.execute(sql, params).fetchall()
    return {int(r["event_id"]) for r in rows}


def detector_stats_report(conn: Any, *, days: int | None = None) -> str:
    since = _since_ts(days) if days else None
    accepted_ids = _accepted_event_ids(conn, since)
    lines: list[str] = []

    for det_id in DETECTOR_IDS:
        events = _detector_events(conn, det_id, since)
        detected = len(events)
        accepted = sum(1 for e in events if int(e["id"]) in accepted_ids)
        filtered = detected - accepted

        confs: list[float] = []
        scores: list[float] = []
        for e in events:
            eid = int(e["id"])
            f5 = conn.execute(
                "SELECT dynamic_confidence FROM market_events_signal_reports_f5 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            f7 = conn.execute(
                "SELECT final_confidence, market_score FROM market_events_market_intelligence_f7 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            if f7:
                confs.append(float(f7["final_confidence"]))
                scores.append(float(f7["market_score"]))
            elif f5:
                confs.append(float(f5["dynamic_confidence"]))

        avg_conf = sum(confs) / len(confs) if confs else 0.0
        avg_mscore = sum(scores) / len(scores) if scores else 0.0

        lines.extend([
            det_id,
            "",
            "detected",
            str(detected),
            "",
            "accepted",
            str(accepted),
            "",
            "filtered",
            str(filtered),
            "",
            "avg confidence",
            f"{avg_conf:.1f}",
            "",
            "avg market score",
            f"{avg_mscore:.0f}",
            "",
        ])

    # SHOCK_F shadow
    sql_f = "SELECT COUNT(*) AS n FROM market_events_shadow_candidates WHERE detector_id = 'SHOCK_F'"
    params_f: list[Any] = []
    if since is not None:
        sql_f += " AND created_at >= ?"
        params_f.append(since)
    shadow_n = conn.execute(sql_f, params_f).fetchone()["n"]
    lines.extend([
        "SHOCK_F",
        "",
        "detected",
        str(int(shadow_n or 0)),
        "",
        "accepted",
        "0",
        "",
        "filtered",
        str(int(shadow_n or 0)),
        "",
        "avg confidence",
        "—",
        "",
        "avg market score",
        "—",
        "",
    ])
    return "\n".join(lines).rstrip()


def threshold_report(conn: Any, *, days: int = 30) -> str:
    since = _since_ts(days)
    current = F7_MIN_FINAL_CONFIDENCE

    rows = conn.execute(
        """
        SELECT f7.final_confidence, o.pnl_pct, o.status
        FROM market_events_market_intelligence_f7 f7
        LEFT JOIN market_events_signal_outcomes_f72 o ON o.event_id = f7.event_id
        JOIN market_events e ON e.id = f7.event_id
        WHERE e.event_ts >= ?
        """,
        (since,),
    ).fetchall()

    sent_at_current = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72 o
        JOIN market_events e ON e.id = o.event_id
        WHERE e.event_ts >= ?
        """,
        (since,),
    ).fetchone()["n"]

    lines = [
        "Current Telegram threshold",
        f"{current:.1f}",
        "",
    ]

    for thr in (7.0, 6.5):
        eligible = sum(1 for r in rows if float(r["final_confidence"]) >= thr)
        if sent_at_current > 0:
            pct_more = (eligible - int(sent_at_current)) / int(sent_at_current) * 100
        else:
            pct_more = float(eligible) * 100 if eligible else 0.0
        lines.extend([
            f"If threshold={thr:.1f}",
            "",
            f"signals +{max(0, pct_more):.0f}%",
            "",
        ])

    closed = [r for r in rows if r["status"] == "CLOSED" and r["pnl_pct"] is not None]
    wins = sum(1 for r in closed if float(r["pnl_pct"]) > 0)
    wr = wins / len(closed) * 100 if closed else 0.0
    lines.extend([
        "Expected WR",
        f"{wr:.0f}%",
    ])
    return "\n".join(lines)


def diagnostics_dashboard_stats(conn: Any) -> dict[str, Any]:
    start = _day_start_ts(0)
    rejected_today = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_near_miss_f73 WHERE event_ts >= ?",
        (start,),
    ).fetchone()["n"]

    recent = conn.execute(
        """
        SELECT rejection_category, COUNT(*) AS n
        FROM market_events_near_miss_f73
        WHERE event_ts >= ?
        GROUP BY rejection_category
        ORDER BY n DESC LIMIT 5
        """,
        (start,),
    ).fetchall()

    det_lines = []
    for det_id in DETECTOR_IDS:
        events = _detector_events(conn, det_id, start)
        if not events:
            continue
        accepted_ids = _accepted_event_ids(conn, start)
        det_lines.append({
            "detector": det_id,
            "detected": len(events),
            "accepted": sum(1 for e in events if int(e["id"]) in accepted_ids),
        })

    near_miss_rows = conn.execute(
        """
        SELECT * FROM market_events_near_miss_f73
        WHERE event_ts >= ?
        ORDER BY event_ts DESC LIMIT 20
        """,
        (start,),
    ).fetchall()

    return {
        "rejected_today": int(rejected_today or 0),
        "top_rejection_reasons": [
            {"category": r["rejection_category"], "count": int(r["n"])} for r in recent
        ],
        "detector_stats": det_lines,
        "near_miss": [dict(r) for r in near_miss_rows],
    }


def build_quiet_market_telegram_f73(conn: Any) -> str | None:
    """Message when no signals sent in last N hours but candidates exist."""
    from bot.research.market_events.signal_intelligence.config import F73_QUIET_MARKET_HOURS

    since = int(time.time()) - F73_QUIET_MARKET_HOURS * 3600

    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72 WHERE entry_time >= ?",
        (since,),
    ).fetchone()["n"]
    if int(sent or 0) > 0:
        return None

    candidates = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?",
        (since,),
    ).fetchone()["n"]
    rejected = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_near_miss_f73 WHERE event_ts >= ?",
        (since,),
    ).fetchone()["n"]

    if int(candidates or 0) == 0:
        return None

    top = conn.execute(
        """
        SELECT rejection_category, COUNT(*) AS n
        FROM market_events_near_miss_f73
        WHERE event_ts >= ?
        GROUP BY rejection_category
        ORDER BY n DESC LIMIT 1
        """,
        (since,),
    ).fetchone()

    reason_map = {
        CAT_CONFIDENCE: "низкая уверенность",
        CAT_MARKET_SCORE: "низкий market score",
        CAT_FUNDING: "нет данных по funding",
        CAT_VOLUME: "низкий объём",
        CAT_TREND: "слабый тренд",
        CAT_BTC: "BTC против сделки",
        CAT_PRIORITY: "приоритетное окно",
        CAT_CONFIRMATION: "нет подтверждения",
    }

    main_reason = reason_map.get(
        top["rejection_category"] if top else CAT_CONFIDENCE,
        "фильтры качества",
    )

    return "\n".join([
        "Сегодня рынок спокойный.",
        "",
        "Кандидатов найдено",
        str(int(candidates or 0)),
        "",
        "Все отклонены фильтрами." if int(rejected or 0) >= int(candidates or 0) else f"Отклонено: {int(rejected or 0)}",
        "",
        "Главная причина",
        main_reason + ".",
    ])


def maybe_send_quiet_market_f73(conn: Any, *, now: int | None = None) -> bool:
    from bot.research.market_events.signal_intelligence.config import (
        F72_MORNING_HOUR_UTC,
        F73_ENABLED,
    )

    if not F73_ENABLED:
        return False

    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    day_key = dt.strftime("%Y-%m-%d")

    if dt.hour != F72_MORNING_HOUR_UTC or dt.minute >= 10:
        return False

    state = conn.execute(
        "SELECT value FROM market_events_f73_ops_state WHERE key = 'quiet_market_day'",
    ).fetchone()
    if state and state["value"] == day_key:
        return False

    msg = build_quiet_market_telegram_f73(conn)
    if not msg:
        return False

    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import _safe_alert

    sent = _safe_alert(
        conn,
        event_id=0,
        alert_type="QUIET_MARKET_F73",
        detail=day_key,
        message=msg,
        enabled=alert_shock_enabled(),
    )
    if sent:
        conn.execute(
            """
            INSERT OR REPLACE INTO market_events_f73_ops_state (key, value, updated_at)
            VALUES ('quiet_market_day', ?, ?)
            """,
            (day_key, now),
        )
    return sent
