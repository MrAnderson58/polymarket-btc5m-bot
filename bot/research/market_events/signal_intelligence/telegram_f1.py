"""Phase F.1 — trader-grade Russian Telegram research reports."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.market_event_alerts import PAPER_LABEL
from bot.research.market_events.signal_intelligence.signal_report_f1 import load_signal_report_f1


def _ai_summary_ru(conn: Any, event_id: int) -> str:
    row = conn.execute(
        """
        SELECT summary_ru, summary_en, bias FROM market_event_ai_analyses_f0
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if row and row["summary_ru"]:
        return str(row["summary_ru"]).split(".")[0][:80]
    if row and row["summary_en"]:
        return str(row["summary_en"]).split(".")[0][:80]
    row = conn.execute(
        """
        SELECT structured_output_json FROM market_event_ai_analyses
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if row:
        try:
            data = json.loads(row["structured_output_json"] or "{}")
            c = (data.get("short_commentary") or "").strip()
            if c:
                return c.split("\n")[0][:80]
        except Exception:
            pass
    return "Анализ в очереди"


def _matching_block(matching: list[dict[str, Any]]) -> list[str]:
    if not matching:
        return ["Похожих кейсов мало"]
    lines = ["Похожие кейсы:"]
    for e in matching[:3]:
        rev = "✓" if e.get("confirmed_reversal") else "—"
        lines.append(
            f"  #{e.get('event_id')} {e.get('return_pct'):+.1f}% → {rev}",
        )
    return lines


def format_shock_f1(conn: Any, event_id: int) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    report = load_signal_report_f1(conn, event_id)
    if not row:
        return "SHOCK: событие не найдено"
    if not report:
        from bot.research.market_events.signal_intelligence.telegram_f0 import format_shock_f0
        return format_shock_f0(conn, event_id)

    sym = row["symbol"]
    ret = float(row["return_pct"] or 0)
    window = int(row["trigger_window_seconds"] or 60)
    win_label = f"{window // 60}m" if window >= 60 else f"{window}s"
    expl = report.explanation
    reasons = expl.get("reasons_ru") or []

    lines = [
        f"🚨 SHOCK · {sym}",
        f"{ret:+.1f}% · {win_label}",
        "",
        f"Уверенность {report.confidence_score:.1f}/10",
        f"Вероятность разворота {int(report.reversal_probability * 100)}%",
        "",
        "Почему:",
    ]
    for r in reasons[:6]:
        lines.append(f"• {r}")
    lines.extend(_matching_block(report.matching_events))
    lines.extend([
        "",
        f"История: {int(report.historical_reversal_rate * 100)}% reversal",
        f"Ожидаемое движение: {report.expected_target_pct:+.1f}%",
        "",
        f"План: {report.entry_recommendation}",
        f"Цель: {report.expected_target_pct:+.1f}%",
        f"Стоп: −{report.expected_stop_pct:.1f}%",
        "",
        "AI",
        _ai_summary_ru(conn, event_id),
        "",
        "PAPER ONLY",
    ])
    return "\n".join(lines)


def format_entry_f1(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    reversal_variant: str,
    entry: float,
    stop: float,
    target: float,
) -> str:
    report = load_signal_report_f1(conn, event_id)
    conf = f"{report.confidence_score:.1f}/10" if report else "—"
    plan = report.entry_recommendation if report else reversal_variant
    lines = [
        f"✅ ENTRY · {symbol}",
        reversal_variant,
        "",
        f"Entry {entry:.4g}",
        f"Stop {stop:.4g}",
        f"Target {target:.4g}",
        "",
        f"Уверенность {conf}",
        f"План {plan}",
        "",
        "AI",
        _ai_summary_ru(conn, event_id),
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def format_result_f1(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    pnl_pct: float,
    holding_min: int,
    exit_variant: str,
    ai_agreed: bool | None = None,
    hist_matched: bool | None = None,
) -> str:
    report = load_signal_report_f1(conn, event_id)
    conf = f"{report.confidence_score:.1f}/10" if report else "—"
    expected = report.expected_target_pct if report else None

    ai_label = "AI ✓" if ai_agreed else "AI ✗" if ai_agreed is False else "AI —"
    hist_label = "История ✓" if hist_matched else "История ✗" if hist_matched is False else "История —"

    lines = [
        f"🏁 RESULT · {symbol}",
        f"{pnl_pct:+.1f}%",
        "",
        f"Holding {holding_min}m",
        exit_variant,
        "",
        f"Уверенность {conf}",
        ai_label,
        hist_label,
    ]
    if expected is not None:
        lines.append(f"Ожидали {expected:+.1f}%")
    lines.extend(["", "PAPER ONLY"])
    return "\n".join(lines)
