"""S48/S53 — Telegram views: Paper Trading = S42; AI signals = S48."""

from __future__ import annotations

import logging

from bot.research.ai_analyst.signal_consistency.repository import get_repository
from bot.research.ai_analyst.strategy_validation.daily_report import (
    day_start_ts,
    format_daily_report,
)
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
    """AI signal history (S48) — separate entity from Paper Trading opens."""
    try:
        rows = get_repository().list_signals(limit=limit)
    except Exception as exc:
        logger.warning("s48 signals load failed: %s", exc)
        rows = []
    lines = [section_header("AI Signals (S48)", "📡"), ""]
    if not rows:
        lines.append(escape("История сигналов пуста — появится после первого AI-сигнала."))
        lines.append(escape("Signal history is empty — will appear after the first AI signal."))
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
    """Paper Trading opens from S42 (same book as paper-performance / doctor)."""
    try:
        repo = get_repository()
        rows = repo.list_paper_open_trades(limit=20)
        pending = None if rows else repo.latest_signal()
    except Exception as exc:
        logger.warning("paper open load failed: %s", exc)
        rows, pending = [], None
    lines = [section_header("Paper Open (S42)", "📂"), ""]
    if not rows:
        lines.append(escape("Нет открытых paper-сделок / No open paper trades."))
        if pending and str(pending.get("status") or "").upper() in {
            "PENDING", "TRIGGERED", "",
        }:
            lines.extend([
                "",
                section_header("Current AI Signal (S48)", "📡"),
                "",
                f"<b>{escape(str(pending.get('market')))} "
                f"{escape(str(pending.get('direction')))}</b> "
                f"({escape(str(pending.get('status') or 'PENDING'))})",
                f"conf={escape(str(pending.get('confidence')))} "
                f"entry {escape(str(pending.get('entry_low')))}-"
                f"{escape(str(pending.get('entry_high')))}",
            ])
        return truncate_telegram("\n".join(lines))
    for r in rows:
        lines.extend([
            f"<b>{escape(str(r.get('symbol')))} {escape(str(r.get('direction')))}</b>",
            f"entry={escape(str(r.get('entry')))} "
            f"SL={escape(str(r.get('stop')))} TP1={escape(str(r.get('tp1')))}",
            f"MFE={escape(str(r.get('mfe_pct')))}% MAE={escape(str(r.get('mae_pct')))}%",
            "",
        ])
    return truncate_telegram("\n".join(lines))


def format_closed_telegram(*, limit: int = 15) -> str:
    """Closed AI validation outcomes (S48) — labeled separately from S42 paper."""
    try:
        rows = get_repository().list_outcomes(limit=limit)
    except Exception as exc:
        logger.warning("s48 closed load failed: %s", exc)
        rows = []
    lines = [section_header("AI Closed (S48)", "✅"), ""]
    if not rows:
        lines.append(escape("Статистика AI-сделок появится после первой закрытой сделки."))
        lines.append(escape("AI closed-trade stats appear after the first closed outcome."))
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
    """Paper Trading stats from S42 (aligned with paper-performance)."""
    try:
        dash = get_repository().paper_stats_dashboard()
    except Exception as exc:
        logger.warning("paper stats load failed: %s", exc)
        dash = {
            "open_trades": 0,
            "closed_trades": 0,
            "winrate_pct": 0.0,
            "today_pnl_usd": 0.0,
            "current_equity": 0.0,
            "weekly_pnl_usd": 0.0,
        }
    open_n = int(dash.get("open_trades") or 0)
    closed_n = int(dash.get("closed_trades") or 0)
    lines = [
        section_header("Paper Stats (S42)", "📊"),
        "",
        f"<b>Source</b>  {escape('market_events_paper_trades_s42')}",
        f"<b>Open</b>  {escape(str(open_n))}",
        f"<b>Closed</b>  {escape(str(closed_n))}",
    ]
    if closed_n <= 0 and open_n <= 0:
        lines.extend([
            "",
            escape("Статистика появится после первой закрытой сделки."),
            escape("Stats will appear after the first closed trade."),
        ])
    else:
        wr = float(dash.get("winrate_pct") or 0)
        pnl_t = float(dash.get("today_pnl_usd") or 0)
        pnl_w = float(dash.get("weekly_pnl_usd") or 0)
        eq = float(dash.get("current_equity") or 0)
        lines.extend([
            f"<b>Win Rate</b>  {escape(f'{wr:.1f}%')}",
            f"<b>PnL Today</b>  {escape(f'${pnl_t:+.2f}')}",
            f"<b>Weekly PnL</b>  {escape(f'${pnl_w:+.2f}')}",
            f"<b>Equity</b>  {escape(f'${eq:,.2f}')}",
        ])
    return truncate_telegram("\n".join(lines))


def format_leaderboard_telegram() -> str:
    try:
        outcomes = get_repository().list_outcomes(limit=500)
    except Exception as exc:
        logger.warning("s48 leaderboard load failed: %s", exc)
        outcomes = []
    payload = build_ranking_report(outcomes)
    body = format_leaderboard(payload) + "\n\n" + format_ranking(payload)
    return truncate_telegram(
        section_header("AI Leaderboard (S48)", "🏆") + "\n\n" + escape(body)
    )


def format_daily_telegram() -> str:
    try:
        repo = get_repository()
        start = day_start_ts()
        outcomes = repo.list_outcomes(limit=200, since_ts=start)
        signals_today = repo.count_signals_today()
        open_n = repo.count_ai_open_trades()
    except Exception as exc:
        logger.warning("s48 daily load failed: %s", exc)
        outcomes, signals_today, open_n = [], 0, 0
    text = format_daily_report(outcomes, signals_today=signals_today, open_count=open_n)
    return truncate_telegram(
        section_header("AI Daily (S48)", "📅") + "\n\n" + escape(text)
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
