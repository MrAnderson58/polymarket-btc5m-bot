"""S47 — optional rule-based signal builder from market context (no LLM required)."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.paper_trading.signals import TradingSignal


def build_signal_from_context(
    ctx: dict[str, Any],
    *,
    strategy: str = "ai_context",
    risk_pct: float = 1.0,
) -> TradingSignal | None:
    """
    Build a BTC paper signal from context when price + bias are available.
    Returns None when insufficient data.
    """
    btc = ctx.get("btc") or {}
    price = btc.get("price")
    if price is None:
        return None
    price = float(price)
    quality = ctx.get("analysis_quality") or {}
    confidence = float(quality.get("confidence") or ctx.get("context_completeness") or 50)
    trend = str(btc.get("trend") or "").lower()
    change = btc.get("change_24h_pct")
    reasons: list[str] = []

    if trend == "bullish" or (change is not None and float(change) > 0):
        direction = "LONG"
        entry_low = round(price * 0.998, 2)
        entry_high = round(price * 1.002, 2)
        stop = round(price * 0.985, 2)
        tp1 = round(price * 1.01, 2)
        tp2 = round(price * 1.02, 2)
        tp3 = round(price * 1.035, 2)
        reasons.append("BTC trend/bias supportive for long paper setup")
    elif trend == "bearish" or (change is not None and float(change) < 0):
        direction = "SHORT"
        entry_low = round(price * 0.998, 2)
        entry_high = round(price * 1.002, 2)
        stop = round(price * 1.015, 2)
        tp1 = round(price * 0.99, 2)
        tp2 = round(price * 0.98, 2)
        tp3 = round(price * 0.965, 2)
        reasons.append("BTC trend/bias supportive for short paper setup")
    else:
        return None

    etf = ((ctx.get("etf") or {}).get("btc_etf") or {})
    if etf.get("netflow_5d") is not None:
        nf = float(etf["netflow_5d"])
        if nf > 0 and direction == "LONG":
            reasons.append(f"Positive ETF 5d netflow ({nf})")
        elif nf < 0 and direction == "SHORT":
            reasons.append(f"Negative ETF 5d netflow ({nf})")

    if not reasons:
        reasons.append("Derived from available market context only")

    sig = TradingSignal(
        symbol="BTC",
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        risk_pct=risk_pct,
        confidence=min(100.0, max(0.0, confidence)),
        reasons=reasons,
        strategy=strategy,
    )
    errs = sig.validate()
    if errs:
        return None
    return sig
