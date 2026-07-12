"""Phase F.7.2 — yesterday report and morning Telegram digest."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any


def _day_bounds(day_offset: int = 1) -> tuple[int, int, str]:
    """day_offset=1 → yesterday UTC."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_offset == 0:
        start = int(today_start.timestamp())
        return start, int(now.timestamp()), today_start.strftime("%Y-%m-%d")
    yesterday_start = today_start.timestamp() - 86400 * day_offset
    yesterday_end = today_start.timestamp()
    label = datetime.fromtimestamp(yesterday_start, tz=timezone.utc).strftime("%Y-%m-%d")
    return int(yesterday_start), int(yesterday_end), label


def yesterday_report(conn: Any) -> str:
    start, end, day_key = _day_bounds(day_offset=1)

    signals = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ? AND event_ts < ?",
        (start, end),
    ).fetchone()["n"]

    sent = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72
        WHERE entry_time >= ? AND entry_time < ?
        """,
        (start, end),
    ).fetchone()["n"]

    closed = conn.execute(
        """
        SELECT * FROM market_events_signal_outcomes_f72
        WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        """,
        (start, end),
    ).fetchall()

    tp1 = sum(1 for r in closed if r["tp1_hit_time"] and r["tp1_hit_time"] >= start)
    tp2 = sum(1 for r in closed if r["tp2_hit_time"] and r["tp2_hit_time"] >= start)
    tp3 = sum(1 for r in closed if r["exit_reason"] == "TP3")
    sl = sum(1 for r in closed if r["exit_reason"] == "SL")

    wins = sum(1 for r in closed if float(r["pnl_pct"] or 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0.0
    avg_rr = sum(float(r["risk_reward"] or 0) for r in closed) / len(closed) if closed else 0.0

    best_author_row = conn.execute(
        """
        SELECT author_channel, COUNT(*) AS n,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS w
        FROM market_events_signal_outcomes_f72
        WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
          AND author_channel IS NOT NULL
        GROUP BY author_channel
        ORDER BY w DESC, n DESC LIMIT 1
        """,
        (start, end),
    ).fetchone()

    best_pattern = conn.execute(
        """
        SELECT pattern_json, AVG(pnl_pct) AS avg_pnl, COUNT(*) AS n
        FROM market_events_signal_outcomes_f72
        WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        GROUP BY pattern_json
        HAVING n >= 1
        ORDER BY avg_pnl DESC LIMIT 1
        """,
        (start, end),
    ).fetchone()

    best_setup = "—"
    if best_pattern:
        pat = json.loads(best_pattern["pattern_json"] or "{}")
        best_setup = pat.get("trend_stage") or pat.get("long_trend_stage") or "Trend Shock"

    lines = [
        f"Вчера — {day_key}",
        "",
        "Сигналов",
        str(int(signals or 0)),
        "",
        "Отправлено",
        str(int(sent or 0)),
        "",
        "TP1",
        str(tp1),
        "",
        "TP2",
        str(tp2),
        "",
        "TP3",
        str(tp3),
        "",
        "SL",
        str(sl),
        "",
        "Win Rate",
        "",
        f"{win_rate:.0f}%",
        "",
        "Средний RR",
        "",
        f"{avg_rr:.1f}",
        "",
        "Лучший автор",
        str(best_author_row["author_channel"]) if best_author_row else "—",
        "",
        "Лучший сетап",
        best_setup,
    ]
    return "\n".join(lines)


def build_morning_telegram_f72(conn: Any) -> str:
    """Morning digest for Telegram (covers yesterday)."""
    start, end, day_key = _day_bounds(day_offset=1)
    body = yesterday_report(conn)

    closed = conn.execute(
        """
        SELECT symbol, pnl_pct, signal_score FROM market_events_signal_outcomes_f72
        WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        ORDER BY pnl_pct DESC
        """,
        (start, end),
    ).fetchall()

    best = closed[0] if closed else None
    worst = closed[-1] if closed else None

    paper_pnl = conn.execute(
        """
        SELECT SUM(net_return) AS s FROM paper_strategy_runs
        WHERE exit_ts >= ? AND exit_ts < ?
        """,
        (start, end),
    ).fetchone()["s"]

    patterns = conn.execute(
        """
        SELECT pattern_key, wins, signals_count, avg_pnl
        FROM market_events_pattern_stats_f72
        WHERE updated_at >= ? ORDER BY avg_pnl DESC LIMIT 3
        """,
        (start,),
    ).fetchall()

    wins = sum(1 for r in closed if float(r["pnl_pct"] or 0) > 0)
    wr = (wins / len(closed) * 100) if closed else 0.0

    lines = [
        f"☀️ Отчёт за сутки — {day_key}",
        "",
        body,
        "",
        "─" * 20,
        "",
        f"Доходность paper: {float(paper_pnl or 0):+.1f}%",
        f"Win Rate сигналов: {wr:.0f}%",
    ]
    if best:
        lines.append(f"Лучший сигнал: {best['symbol']} {float(best['pnl_pct']):+.1f}%")
    if worst and worst != best:
        lines.append(f"Худший сигнал: {worst['symbol']} {float(worst['pnl_pct']):+.1f}%")
    if patterns:
        lines.extend(["", "Новые закономерности:"])
        for p in patterns:
            wr_p = int(p["wins"]) / int(p["signals_count"]) * 100 if p["signals_count"] else 0
            lines.append(f"  • {p['pattern_key'][:40]} WR {wr_p:.0f}% avg {float(p['avg_pnl']):+.1f}%")
    return "\n".join(lines)


def maybe_send_morning_digest_f72(conn: Any, *, now: int | None = None) -> bool:
    from bot.research.market_events.signal_intelligence.config import F72_ENABLED, F72_MORNING_HOUR_UTC

    if not F72_ENABLED:
        return False

    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    day_key = dt.strftime("%Y-%m-%d")

    if dt.hour != F72_MORNING_HOUR_UTC or dt.minute >= 10:
        return False

    state = conn.execute(
        "SELECT value FROM market_events_f72_ops_state WHERE key = 'morning_digest_day'",
    ).fetchone()
    if state and state["value"] == day_key:
        return False

    msg = build_morning_telegram_f72(conn)
    from bot.research.market_events.market_event_alerts import _safe_alert
    from bot.research.market_events.alert_config import alert_shock_enabled

    sent = _safe_alert(
        conn,
        event_id=0,
        alert_type="MORNING_DIGEST",
        detail=day_key,
        message=msg,
        enabled=alert_shock_enabled(),
    )
    if sent:
        conn.execute(
            """
            INSERT OR REPLACE INTO market_events_f72_ops_state (key, value, updated_at)
            VALUES ('morning_digest_day', ?, ?)
            """,
            (day_key, now),
        )
    return sent


def outcome_dashboard_stats(conn: Any) -> dict[str, Any]:
    start, _, _ = _day_bounds(day_offset=0)
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72 WHERE status != 'CLOSED'",
    ).fetchone()["n"]
    closed_today = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_outcomes_f72
        WHERE status = 'CLOSED' AND exit_time >= ?
        """,
        (start,),
    ).fetchone()["n"]
    closed_all = conn.execute(
        "SELECT pnl_pct, risk_reward, holding_seconds FROM market_events_signal_outcomes_f72 WHERE status = 'CLOSED'",
    ).fetchall()
    wins = sum(1 for r in closed_all if float(r["pnl_pct"] or 0) > 0)
    wr = wins / len(closed_all) if closed_all else 0.0
    avg_rr = sum(float(r["risk_reward"] or 0) for r in closed_all) / len(closed_all) if closed_all else 0.0
    avg_hold = sum(int(r["holding_seconds"] or 0) for r in closed_all) / len(closed_all) if closed_all else 0
    paper_pnl = conn.execute(
        """
        SELECT SUM(net_return) AS s FROM paper_strategy_runs
        WHERE exit_ts >= ? AND net_return IS NOT NULL
        """,
        (start,),
    ).fetchone()["s"]

    return {
        "active_signals": int(active or 0),
        "closed_today": int(closed_today or 0),
        "win_rate": round(wr * 100, 1),
        "avg_rr": round(avg_rr, 2),
        "avg_hold_seconds": int(avg_hold),
        "total_paper_pnl_today": round(float(paper_pnl or 0), 2),
    }
