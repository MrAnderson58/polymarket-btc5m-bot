"""S48 — Telegram views backed by S51 SignalTruthRepository (single source)."""

from __future__ import annotations

import logging

from bot.research.ai_analyst.signal_consistency.repository import get_repository
from bot.research.ai_analyst.strategy_validation.daily_report import (
    day_start_ts,
    format_daily_report,
)
from bot.research.ai_analyst.strategy_validation.dashboard import format_dashboard
from bot.research.ai_analyst.strategy_validation.ranking import (
    build_ranking_report,
    format_leaderboard,
    format_ranking,
)
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


def format_signals_telegram(*, limit: int = 12) -> str:
    try:
        rows = get_repository().list_signals(limit=limit)
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
        rows = get_repository().list_open_trades(limit=20)
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
        rows = get_repository().list_outcomes(limit=limit)
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
        dash = get_repository().stats_dashboard(outcome_limit=500)
    except Exception as exc:
        logger.warning("s48 stats load failed: %s", exc)
        from bot.research.ai_analyst.strategy_validation.dashboard import build_dashboard
        dash = build_dashboard([], open_count=0)
    text = format_dashboard(dash)
    return truncate_telegram(
        section_header("Strategy Stats", "📊") + "\n\n" + escape(text)
    )


def format_leaderboard_telegram() -> str:
    try:
        outcomes = get_repository().list_outcomes(limit=500)
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
        repo = get_repository()
        start = day_start_ts()
        outcomes = repo.list_outcomes(limit=200, since_ts=start)
        signals_today = repo.count_signals_today()
        open_n = repo.count_open_trades()
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
