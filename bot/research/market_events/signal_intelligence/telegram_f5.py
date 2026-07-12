"""Phase F.5 — Professional Signal Engine Telegram layout."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.confidence_f1 import (
    ENTRY_SCALE_IN_25,
    ENTRY_WAIT_R2,
)
from bot.research.market_events.signal_intelligence.historical_examples_f5 import format_historical_block
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.telegram_f3 import confidence_emoji, format_symbol_usdt

SEP = "──────────────"


def _plan_lines(entry_quality: str, entry_rec: str) -> list[str]:
    rec = (entry_rec or "").upper()
    if entry_quality == "SKIP":
        return ["Пропустить — качество входа низкое."]
    lines = ["Не покупать сейчас.", "", "Ждать реакцию R2.", "", "После подтверждения:"]
    if ENTRY_SCALE_IN_25 in rec or "SCALE" in rec:
        lines.extend([
            "• 25% позиции",
            "• далее 50%",
            "• остаток после закрепления",
        ])
    else:
        lines.extend([
            "• 25% позиции",
            "• далее 50%",
            "• остаток после закрепления",
        ])
    return lines


def render_professional_telegram_f5(
    conn: Any,
    *,
    event_id: int,
    dynamic_confidence: float,
    reversal_probability: float,
    continuation_probability: float,
    entry_quality: str,
    signal_cause: str,
    interest_factors: list[str],
    historical_examples: list[dict[str, Any]],
    risk_reward: RiskRewardF5,
    ai_summary: str,
    trend: dict[str, Any] | None,
    report: Any,
) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return "SHOCK: событие не найдено"

    sym = format_symbol_usdt(str(row["symbol"]))
    ret = float(row["return_pct"] or 0)
    arrow = "▼" if ret < 0 else "▲"

    if trend:
        wm = int(trend.get("window_minutes") or 45)
        stage = "TREND SHOCK"
        move = float(trend.get("cumulative_return_pct") or ret)
    else:
        wm = max(1, int(row["trigger_window_seconds"] or 2700) // 60)
        stage = "TREND SHOCK"
        move = ret

    rev_pct = int(round(reversal_probability * 100))
    cont_pct = int(round(continuation_probability * 100))
    emoji = confidence_emoji(dynamic_confidence)

    lines = [
        f"🚨 {sym}",
        "",
        f"{stage} ({wm}m)",
        "",
        f"{arrow} {move:+.1f}%",
        "",
        "Уверенность",
        f"{emoji} {dynamic_confidence:.1f} / 10",
        "",
        f"Вероятность отката",
        f"{rev_pct}%",
        "",
        f"Вероятность продолжения",
        f"{cont_pct}%",
        "",
        SEP,
        "",
        "Почему бот считает это интересным",
        "",
    ]
    for factor in interest_factors:
        lines.append(factor if factor.startswith("✔") else f"✔ {factor}")

    lines.extend([
        "",
        f"Качество входа: {entry_quality}",
        "",
        SEP,
        "",
        *format_historical_block(historical_examples),
        "",
        SEP,
        "",
        "План",
        "",
        *_plan_lines(entry_quality, report.entry_recommendation if report else ENTRY_WAIT_R2),
        "",
        f"TP1 +{risk_reward.tp1_pct:.1f}%  (вероятность {risk_reward.tp1_prob:.0f}%)",
        f"TP2 +{risk_reward.tp2_pct:.1f}%  (вероятность {risk_reward.tp2_prob:.0f}%)",
        f"TP3 +{risk_reward.tp3_pct:.1f}%  (вероятность {risk_reward.tp3_prob:.0f}%)",
        "",
        f"SL −{risk_reward.stop_pct:.1f}%",
        "",
        f"RR = {risk_reward.risk_reward:.1f}",
        "",
        SEP,
        "",
        "Причина",
        "",
        signal_cause,
        "",
        SEP,
        "",
        "ИИ",
        "",
    ])
    if ai_summary:
        for part in ai_summary.split("\n")[:3]:
            if part.strip():
                lines.append(part.strip())
    else:
        lines.append("Анализ в процессе — ждать подтверждения по R2.")

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
        format_author_telegram_block,
        load_trader_performance_for_event,
        resolve_channel_for_event,
    )
    channel = resolve_channel_for_event(conn, event_id)
    trader_perf = load_trader_performance_for_event(conn, event_id)
    lines.extend(format_author_telegram_block(trader_perf, channel=channel))
    lines.extend(["", "PAPER ONLY"])
    return "\n".join(lines)


def format_shock_f5(conn: Any, event_id: int) -> str:
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import (
        load_professional_signal_f5,
        run_signal_engine_f5,
    )
    signal = load_professional_signal_f5(conn, event_id)
    if signal:
        return signal.telegram_rendered
    built = run_signal_engine_f5(conn, event_id)
    return built.telegram_rendered if built else "SHOCK: intel недоступен"
