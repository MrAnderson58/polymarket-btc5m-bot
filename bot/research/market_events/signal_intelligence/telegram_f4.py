"""Phase F.4 — unified final Telegram message after full analysis."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f3 import confidence_emoji, format_symbol_usdt
from bot.research.market_events.signal_intelligence.trend_shock_v2 import load_trend_shock_v2
from bot.research.market_events.signal_intelligence.visual_intel_f4 import load_visual_intel


def _reasons(conn: Any, event_id: int, trend: dict[str, Any] | None, report: Any) -> list[str]:
    lines: list[str] = []
    if trend:
        streak = int(trend.get("consecutive_bars") or 0)
        direction = str(trend.get("direction") or "DOWN")
        bear = direction == "DOWN"
        if streak >= 2:
            label = "медвежьих" if bear else "бычьих"
            lines.append(f"{streak} {label} свечей подряд")
        vol = float(trend.get("volume_multiple") or 0)
        if vol >= 1.5:
            lines.append(f"Объем {vol:.1f}× среднего")
        oi = trend.get("open_interest_delta")
        if oi is not None and float(oi) > 0:
            lines.append("OI растет")
        elif report and report.oi_regime in ("rising", "divergence"):
            lines.append("OI растет")
        fund = trend.get("funding")
        if fund is not None and float(fund) > 0:
            lines.append("Funding положительный")
        elif report and report.funding_regime == "accelerating":
            lines.append("Funding растёт")
        struct = report.market_structure_labels if report else []
        if any("LL" in s or "Lower Low" in s for s in struct):
            lines.append("Структура LL/LH")
        elif struct:
            lines.append(f"Структура {struct[0]}")

    visual = load_visual_intel(conn, event_id)
    if visual:
        scenario = (visual.get("chart_analysis") or {}).get("author_scenario")
        if scenario:
            lines.append(f"Автор из Telegram {scenario}")

    if not lines and report:
        lines.append(f"Volume {report.volume_label}")
    return lines[:6]


def _plan_line(trend: dict[str, Any] | None, visual: dict[str, Any] | None) -> list[str]:
    if visual and (visual.get("chart_analysis") or {}).get("demand_zones"):
        return [
            "Ждать реакцию в Demand-зоне.",
            "До подтверждения R2/R3 вход не рекомендуется.",
        ]
    if trend and trend.get("stage") == "Reversal Candidate":
        return [
            "Кандидат на разворот — ждать R2.",
            "До подтверждения R2/R3 вход не рекомендуется.",
        ]
    return [
        "Ждать реакцию в Demand-зоне.",
        "До подтверждения R2/R3 вход не рекомендуется.",
    ]


def render_final_telegram_f4(conn: Any, event_id: int) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    report = load_signal_report_f2(conn, event_id)
    trend = load_trend_shock_v2(conn, event_id)

    if not row:
        return "SHOCK: событие не найдено"

    sym = format_symbol_usdt(str(row["symbol"]))
    ret = float(row["return_pct"] or 0)
    arrow = "▼" if ret < 0 else "▲"

    conf = report.confidence_score if report else 5.0
    emoji = confidence_emoji(conf)

    if trend:
        wm = int(trend.get("window_minutes") or 30)
        stage = trend.get("stage") or "Trend Shock"
        cont = int(float(trend.get("continuation_probability") or 0.5) * 100)
        move = float(trend.get("cumulative_return_pct") or ret)
    else:
        wm = max(1, int(row["trigger_window_seconds"] or 900) // 60)
        stage = "Trend Shock"
        cont = int((1 - (report.reversal_probability if report else 0.5)) * 100) if report else 50
        move = ret

    reasons = _reasons(conn, event_id, trend, report)
    plan = _plan_line(trend, load_visual_intel(conn, event_id))

    lines = [
        f"🚨 {sym}",
        "",
        f"{stage} ({wm}m)",
        "",
        f"{arrow} {move:+.1f}%",
        "",
        f"Уверенность: {emoji} {conf:.1f}/10",
        "",
        f"Вероятность продолжения: {cont}%",
        "",
        "Причина:",
    ]
    for r in reasons:
        lines.append(f"• {r}")
    lines.extend([
        "",
        "План:",
        *plan,
        "",
        "PAPER ONLY",
    ])
    return "\n".join(lines)


def format_shock_f4(conn: Any, event_id: int) -> str:
    return render_final_telegram_f4(conn, event_id)
