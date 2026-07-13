"""Phase F.7 Task F — Professional Telegram Card v3 + F.7.1 trade plan."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.telegram_f3 import format_symbol_usdt
from bot.research.market_events.signal_intelligence.trade_plan_f71 import TradePlanF71

SEP = "──────────────"


def _stars(confidence: float) -> str:
    n = max(1, min(5, int(round(confidence / 2))))
    return "★" * n + "☆" * (5 - n)


def _short_factors(
    *,
    report: Any | None,
    trend: dict[str, Any] | None,
    liq_intel: dict[str, Any] | None,
    dominance: dict[str, Any] | None,
    historical_rate: float,
) -> list[str]:
    factors: list[str] = []
    if liq_intel and liq_intel.get("regime") not in (None, "neutral"):
        factors.append("✔ ликвидации")
    fund = trend.get("funding") if trend else None
    if (fund is not None and float(fund) < 0) or (report and report.funding_regime in ("flattening", "accelerating")):
        factors.append("✔ Funding")
    oi_delta = trend.get("open_interest_delta") if trend else None
    if (oi_delta is not None and float(oi_delta) > 0) or (report and report.oi_regime == "rising"):
        factors.append("✔ OI")
    if report and getattr(report, "market_structure_labels", None):
        if any("demand" in s.lower() or "HL" in s for s in report.market_structure_labels):
            factors.append("✔ Demand")
    if dominance:
        btc_ret = abs(float(dominance.get("btc_return") or 0))
        if btc_ret < 1.5:
            factors.append("✔ BTC neutral")
    if historical_rate >= 0.5:
        factors.append(f"✔ Исторически {int(historical_rate * 100)}%")
    if not factors:
        factors.append("✔ setup подтверждён")
    return factors[:8]


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
    return _short_factors(
        report=report, trend=trend, liq_intel=liq_intel,
        dominance=dominance, historical_rate=historical_rate,
    )


def _fmt_price(v: float) -> str:
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


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
    trade_plan: TradePlanF71 | None = None,
) -> str:
    from bot.research.market_events.signal_intelligence.trade_plan_f71 import build_trade_plan_for_event

    sym = format_symbol_usdt(symbol)
    stars = _stars(final_confidence)

    if trade_plan is None:
        trade_plan = build_trade_plan_for_event(
            conn,
            event_id=event_id,
            symbol=symbol,
            shock_direction=direction,
            risk_reward=risk_reward,
            final_confidence=final_confidence,
        )

    factors = _short_factors(
        report=report, trend=trend, liq_intel=liq_intel,
        dominance=dominance, historical_rate=historical_rate,
    )
    try:
        from bot.research.market_events.signal_intelligence.liquidity_trend_g1 import (
            load_liquidity_trend_g1,
        )
        g1 = load_liquidity_trend_g1(conn, event_id)
        if g1 and g1.reversal_probability >= 0.6:
            factors.append(f"✔ G1 откат {g1.reversal_probability * 100:.0f}%")
    except Exception:
        pass

    lines = [
        f"🚨 {sym}",
        "",
        f"{stars} {final_confidence:.1f}",
        "",
        "Цена рядом со входом",
        "",
    ]

    if trade_plan:
        lines.extend([
            "Вход",
            _fmt_price(trade_plan.entry),
            "",
            "SL",
            _fmt_price(trade_plan.sl),
            "",
            "TP1",
            _fmt_price(trade_plan.tp1),
            "",
            "TP2",
            _fmt_price(trade_plan.tp2),
            "",
            "TP3",
            _fmt_price(trade_plan.tp3),
            "",
            "RR",
            f"{trade_plan.risk_reward:.1f}",
            "",
            "Размер позиции",
            f"{trade_plan.position_size_pct:.0f}%",
            "",
        ])
    else:
        lines.extend([
            f"TP1 +{risk_reward.tp1_pct:.1f}%",
            f"TP2 +{risk_reward.tp2_pct:.1f}%",
            f"TP3 +{risk_reward.tp3_pct:.1f}%",
            "",
            f"SL −{risk_reward.stop_pct:.1f}%",
            "",
            f"RR {risk_reward.risk_reward:.1f}",
            "",
        ])

    lines.extend([
        "Причина",
        "",
    ])
    lines.extend(factors)

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
        load_trader_performance_for_event,
    )
    trader_perf = load_trader_performance_for_event(conn, event_id)
    if trader_perf and trader_perf.sufficient:
        lines.extend([
            "",
            "Автор",
            f"Win Rate {trader_perf.win_rate * 100:.0f}%  RR {trader_perf.avg_rr:.1f}",
        ])

    if image_intel and image_intel.get("has_image"):
        lines.extend([
            "",
            image_intel.get("agreement_text", ""),
        ])

    lines.extend([
        "",
        "ИИ",
        "",
    ])
    if ai_summary:
        first = ai_summary.split("\n")[0].strip()
        lines.append(first[:200] if first else "Анализ подтверждает setup.")
    else:
        lines.append("Анализ подтверждает setup.")

    try:
        from bot.research.market_events.signal_intelligence.config import G2_TELEGRAM_FORMAT
        if G2_TELEGRAM_FORMAT:
            from bot.research.market_events.signal_intelligence.research_g2 import load_research_g2
            g2 = load_research_g2(conn, event_id)
            if g2 and g2.telegram_block:
                lines.extend(["", g2.telegram_block])
    except Exception:
        pass

    lines.extend(["", "PAPER ONLY"])
    return "\n".join(lines)
