"""Phase F.3 Trend Shock — compact Telegram Premium v2 card."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.entry_stages_f3 import (
    STAGE_ENTRY,
    STAGE_PREPARE,
    STAGE_READY,
    STAGE_WATCH,
    load_entry_stage,
)
from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f3 import confidence_emoji, format_symbol_usdt, reversal_pct
from bot.research.market_events.signal_intelligence.trend_shock import load_trend_shock


def _short_reasons(conn: Any, event_id: int, report: Any) -> str:
    tags: list[str] = []
    if report.rvol_20 >= 2 or report.rvol_100 >= 2:
        tags.append("Volume")
    peers = report.correlation_snapshot.get("peers") or []
    if any(p.get("peer") == "BTC" for p in peers):
        tags.append("BTC")
    if report.funding_regime not in ("unknown", "stable", ""):
        tags.append("Funding")
    if report.oi_regime not in ("unknown", "stable", ""):
        tags.append("OI")
    if report.atr_percentile >= 70:
        tags.append("ATR")
    for label in report.market_structure_labels:
        if label not in ("range", "insufficient_data"):
            tags.append(label.split()[0] if " " in label else label)
            break
    if not tags:
        tags = ["Volume", "Structure"]
    return " · ".join(tags[:5])


def _plan_line(stage: Any, entry_rec: str) -> str:
    if stage.stage == STAGE_ENTRY or entry_rec == "READY_TO_ENTER":
        return "Можно начинать набор"
    if stage.stage == STAGE_READY:
        return "Ждать R3 → 50%"
    if stage.stage == STAGE_PREPARE:
        return "Ждать R2 → 25%"
    if entry_rec == "WAIT_R3":
        return "Лучше пропустить"
    return "Ждать R2 → 25%"


def _tp_sl(conn: Any, event_id: int, report: Any) -> tuple[str, str]:
    row = conn.execute("SELECT direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
    snap = conn.execute(
        """
        SELECT price FROM market_event_snapshots
        WHERE event_id = ? ORDER BY snapshot_ts DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    price = float(snap["price"]) if snap and snap["price"] else None
    if not price:
        return f"+{report.expected_target_pct:.1f}%", f"−{report.expected_stop_pct:.1f}%"

    tp = report.expected_target_pct / 100.0
    sp = report.expected_stop_pct / 100.0
    direction = row["direction"] if row else "DOWN"
    if direction == "UP":
        target = price * (1 - tp)
        stop = price * (1 + sp)
    else:
        target = price * (1 + tp)
        stop = price * (1 - sp)
    return f"{target:.2f}", f"{stop:.2f}"


def _ai_line(summary: str) -> str:
    for line in summary.split("\n"):
        line = line.strip()
        if line:
            return line[:120]
    return "Без катализатора."


def render_trend_premium_v2(conn: Any, event_id: int) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    report = load_signal_report_f2(conn, event_id)
    trend = load_trend_shock(conn, event_id)
    stage = load_entry_stage(conn, event_id)

    if not row:
        return "SHOCK: событие не найдено"
    if not report:
        from bot.research.market_events.signal_intelligence.telegram_f3 import format_shock_f3
        return format_shock_f3(conn, event_id)

    sym = format_symbol_usdt(str(row["symbol"]))
    ret = float(row["return_pct"] or 0)
    emoji = confidence_emoji(report.confidence_score)
    rev = reversal_pct(report.reversal_probability)
    tp, sl = _tp_sl(conn, event_id, report)
    reasons = _short_reasons(conn, event_id, report)
    plan = _plan_line(stage, report.entry_recommendation) if stage else "Ждать R2 → 25%"
    ai = _ai_line(report.ai_summary_v2_ru)

    trend_tag = ""
    if trend:
        wm = int(trend.get("window_minutes") or 0)
        tc = trend.get("trend_class", "")
        trend_tag = f"  |  {tc} M{wm}"

    stage_tag = f" [{stage.stage}]" if stage else ""

    lines = [
        f"🚨 {sym}  {ret:+.1f}%  {emoji} {report.confidence_score:.1f}{stage_tag}",
        "",
        f"Откат {rev}%{trend_tag}",
        "",
        f"Причины: {reasons}",
        "",
        f"TP {tp}  SL {sl}",
        plan,
        "",
        f"ИИ: {ai}",
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def format_shock_trend_premium(conn: Any, event_id: int) -> str:
    return render_trend_premium_v2(conn, event_id)
