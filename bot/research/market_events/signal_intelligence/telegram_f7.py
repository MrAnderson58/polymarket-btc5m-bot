"""Phase F.7 Task F — Professional Telegram Card v3."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.telegram_f3 import confidence_emoji, format_symbol_usdt

SEP = "──────────────"


def _stars(confidence: float) -> str:
    n = max(1, min(5, int(round(confidence / 2))))
    return "★" * n + "☆" * (5 - n)


def _human_factors(
    *,
    report: Any | None,
    trend: dict[str, Any] | None,
    liq_intel: dict[str, Any] | None,
    dominance: dict[str, Any] | None,
    long_trend: dict[str, Any] | None,
    image_intel: dict[str, Any] | None,
    historical_rate: float,
) -> list[str]:
    factors: list[str] = []

    factors.append("✔ Цена у входа")

    if dominance:
        regime = dominance.get("regime", "")
        btc_ret = float(dominance.get("btc_return") or 0)
        if regime in ("RISK ON", "ALT SEASON") or abs(btc_ret) < 1.5:
            factors.append("✔ BTC не мешает")
        elif regime == "CAPITAL INTO BTC":
            factors.append("⚠ BTC забирает капитал")

    fund = trend.get("funding") if trend else None
    if fund is not None and float(fund) < 0:
        factors.append("✔ Funding помогает")
    elif report and report.funding_regime == "flattening":
        factors.append("✔ Funding снижается")

    oi_delta = trend.get("open_interest_delta") if trend else None
    if oi_delta is not None and float(oi_delta) > 0:
        factors.append("✔ OI растёт")
    elif report and report.oi_regime == "rising":
        factors.append("✔ OI растёт")

    if liq_intel and (liq_intel.get("exhaustion") or liq_intel.get("regime") != "neutral"):
        factors.append("✔ Ликвидации заканчиваются")

    if historical_rate >= 0.5:
        factors.append(f"✔ Исторически похожие сделки дали откат в {int(historical_rate * 100)}%")

    if long_trend and long_trend.get("primary_stage") in ("Capitulation", "Panic", "Recovery"):
        factors.append(f"✔ {long_trend['primary_stage']} ({long_trend.get('primary_window')}m)")

    if image_intel and image_intel.get("agreement_pct", 0) >= 70:
        factors.append(f"✔ Согласие с автором: {int(image_intel['agreement_pct'])}%")

    return factors[:8]


def render_professional_telegram_f7(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    direction: str,
    final_confidence: float,
    success_probability: float,
    market_score: float,
    risk_reward: RiskRewardF5,
    ai_summary: str,
    report: Any | None,
    trend: dict[str, Any] | None,
    liq_intel: dict[str, Any] | None,
    dominance: dict[str, Any] | None,
    long_trend: dict[str, Any] | None,
    image_intel: dict[str, Any] | None,
    historical_rate: float,
) -> str:
    sym = format_symbol_usdt(symbol)
    side = "LONG" if direction == "UP" else "SHORT"
    emoji = confidence_emoji(final_confidence)
    stars = _stars(final_confidence)
    success_pct = int(round(success_probability * 100))

    factors = _human_factors(
        report=report,
        trend=trend,
        liq_intel=liq_intel,
        dominance=dominance,
        long_trend=long_trend,
        image_intel=image_intel,
        historical_rate=historical_rate,
    )

    lines = [
        f"🚨 {sym} {side}",
        "",
        f"{stars} {final_confidence:.1f} / 10",
        "",
        "Вероятность успеха",
        "",
        f"{success_pct}%",
        "",
        "Market Score",
        "",
        f"{int(round(market_score))} / 100",
        "",
        SEP,
        "",
        "Почему сейчас интересно",
        "",
    ]
    lines.extend(factors)

    lines.extend([
        "",
        SEP,
        "",
        "План",
        "",
        "Ждать R2",
        "",
        f"TP1 +{risk_reward.tp1_pct:.1f}%",
        f"TP2 +{risk_reward.tp2_pct:.1f}%",
        f"TP3 +{risk_reward.tp3_pct:.1f}%",
        "",
        f"SL −{risk_reward.stop_pct:.1f}%",
        "",
        SEP,
        "",
        "Автор",
        "",
    ])

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
        format_author_telegram_block,
        load_trader_performance_for_event,
        resolve_channel_for_event,
    )
    channel = resolve_channel_for_event(conn, event_id)
    trader_perf = load_trader_performance_for_event(conn, event_id)
    if trader_perf and trader_perf.sufficient:
        lines.extend([
            f"Win Rate {trader_perf.win_rate * 100:.0f}%",
            "",
            f"RR {trader_perf.avg_rr:.1f}",
        ])
    else:
        lines.extend(format_author_telegram_block(trader_perf, channel=channel)[4:7])

    if image_intel and image_intel.get("has_image"):
        lines.extend([
            "",
            SEP,
            "",
            "График автора",
            "",
            image_intel.get("author_view", ""),
            "",
            image_intel.get("agreement_text", ""),
        ])
        for div in image_intel.get("divergence") or []:
            lines.append(f"⚠ {div}")

    lines.extend([
        "",
        SEP,
        "",
        "ИИ",
        "",
    ])
    if ai_summary:
        first = ai_summary.split("\n")[0].strip()
        lines.append(first[:200] if first else "Анализ подтверждает setup.")
    else:
        lines.append("Анализ подтверждает setup.")

    lines.extend(["", "PAPER ONLY"])
    return "\n".join(lines)
