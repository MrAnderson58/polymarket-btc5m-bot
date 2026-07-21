"""S48 — Telegram views backed by SQLite (or empty fallbacks)."""

from __future__ import annotations

import logging
from typing import Any

from bot.research.ai_analyst.strategy_validation.daily_report import (
    day_start_ts,
    format_daily_report,
)
from bot.research.ai_analyst.strategy_validation.dashboard import (
    build_dashboard,
    format_dashboard,
)
from bot.research.ai_analyst.strategy_validation.ranking import (
    build_ranking_report,
    format_leaderboard,
    format_ranking,
)
from bot.research.ai_analyst.strategy_validation.store import load_outcomes, load_signal_history
from bot.research.ai_analyst.telegram_formatter import escape, section_header, truncate_telegram

logger = logging.getLogger(__name__)

S48_COMMANDS = frozenset({
    "/signals",
    "/open",
    "/closed",
    "/stats",
    "/leaderboard",
    "/daily",
})


def _conn_readonly():
    from bot.research.market_events.db import market_events_readonly_connection
    return market_events_readonly_connection()


def format_signals_telegram(*, limit: int = 12) -> str:
    try:
        with _conn_readonly() as conn:
            rows = load_signal_history(conn, limit=limit)
    except Exception as exc:
        logger.warning("s48 signals load failed: %s", exc)
        rows = []
    lines = [section_header("Signals", "📡"), ""]
    if not rows:
        lines.append(escape("(no signals in history)"))
        return truncate_telegram("\n".join(lines))
    for r in rows:
        lines.extend([
            f"<b>{escape(r['market'])} {escape(r['direction'])}</b> "
            f"({escape(str(r.get('status')))})",
            f"conf={escape(str(r.get('confidence')))} score={escape(str(r.get('score')))}",
            f"entry {escape(str(r.get('entry_low')))}-{escape(str(r.get('entry_high')))} "
            f"SL {escape(str(r.get('stop_loss')))}",
            f"TP {escape(str(r.get('tp1')))}/{escape(str(r.get('tp2')))}/{escape(str(r.get('tp3')))}",
            escape(str(r.get("reasoning") or "")[:160]),
            "",
        ])
    return truncate_telegram("\n".join(lines))


def format_open_telegram() -> str:
    try:
        with _conn_readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ai_paper_trades_s47
                WHERE status = 'OPEN'
                ORDER BY opened_at DESC LIMIT 20
                """,
            ).fetchall()
            rows = [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("s48 open load failed: %s", exc)
        rows = []
    lines = [section_header("Open Trades", "📂"), ""]
    if not rows:
        lines.append(escape("(none open)"))
        return truncate_telegram("\n".join(lines))
    for r in rows:
        lines.extend([
            f"<b>{escape(r['symbol'])} {escape(r['direction'])}</b>",
            f"entry={escape(str(r['entry']))} rem={escape(str(r['size_remaining']))}",
            f"MFE={escape(str(r.get('mfe_pct')))}% MAE={escape(str(r.get('mae_pct')))}%",
            "",
        ])
    return truncate_telegram("\n".join(lines))


def format_closed_telegram(*, limit: int = 15) -> str:
    try:
        with _conn_readonly() as conn:
            rows = load_outcomes(conn, limit=limit)
    except Exception as exc:
        logger.warning("s48 closed load failed: %s", exc)
        rows = []
    lines = [section_header("Closed Outcomes", "✅"), ""]
    if not rows:
        lines.append(escape("(none closed)"))
        return truncate_telegram("\n".join(lines))
    for o in rows:
        lines.extend([
            f"<b>{escape(o['market'])} {escape(o['direction'])}</b> {escape(o['result'])}",
            f"R={escape(str(o['r_multiple']))} PnL=${escape(str(o['pnl_usd']))}",
            f"level={escape(str(o.get('tp_level_reached')))} hold={escape(str(o['hold_time_sec']))}s",
            "",
        ])
    return truncate_telegram("\n".join(lines))


def format_stats_telegram() -> str:
    try:
        with _conn_readonly() as conn:
            outcomes = load_outcomes(conn, limit=500)
            try:
                open_n = int(conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_paper_trades_s47 WHERE status='OPEN'",
                ).fetchone()["n"] or 0)
            except Exception:
                open_n = 0
    except Exception as exc:
        logger.warning("s48 stats load failed: %s", exc)
        outcomes, open_n = [], 0
    dash = build_dashboard(outcomes, open_count=open_n)
    text = format_dashboard(dash)
    return truncate_telegram(
        section_header("Strategy Stats", "📊") + "\n\n" + escape(text)
    )


def format_leaderboard_telegram() -> str:
    try:
        with _conn_readonly() as conn:
            outcomes = load_outcomes(conn, limit=500)
    except Exception as exc:
        logger.warning("s48 leaderboard load failed: %s", exc)
        outcomes = []
    payload = build_ranking_report(outcomes)
    body = format_leaderboard(payload) + "\n\n" + format_ranking(payload)
    return truncate_telegram(
        section_header("Leaderboard", "🏆") + "\n\n" + escape(body)
    )


def format_daily_telegram() -> str:
    try:
        with _conn_readonly() as conn:
            start = day_start_ts()
            outcomes = load_outcomes(conn, limit=200, since_ts=start)
            signals = load_signal_history(conn, limit=200)
            signals_today = sum(1 for s in signals if int(s.get("created_at") or 0) >= start)
            try:
                open_n = int(conn.execute(
                    "SELECT COUNT(*) AS n FROM ai_paper_trades_s47 WHERE status='OPEN'",
                ).fetchone()["n"] or 0)
            except Exception:
                open_n = 0
    except Exception as exc:
        logger.warning("s48 daily load failed: %s", exc)
        outcomes, signals_today, open_n = [], 0, 0
    text = format_daily_report(outcomes, signals_today=signals_today, open_count=open_n)
    return truncate_telegram(
        section_header("Daily Report", "📅") + "\n\n" + escape(text)
    )


def handle_s48_command(cmd: str) -> str:
    if cmd == "/signals":
        return format_signals_telegram()
    if cmd == "/open":
        return format_open_telegram()
    if cmd == "/closed":
        return format_closed_telegram()
    if cmd == "/stats":
        return format_stats_telegram()
    if cmd == "/leaderboard":
        return format_leaderboard_telegram()
    if cmd == "/daily":
        return format_daily_telegram()
    raise ValueError(f"unknown s48 command {cmd}")
