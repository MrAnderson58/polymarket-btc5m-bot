"""S47/S51 — rule-based signal builder: machine direction + concrete reasons."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.signal_consistency.confidence import calibrate_from_repository
from bot.research.ai_analyst.signal_consistency.direction import (
    lock_direction,
    score_direction_from_context,
)
from bot.research.ai_analyst.signal_consistency.reasons import (
    merge_concrete_reasons,
    reasons_from_direction_factors,
)


def build_signal_from_context(
    ctx: dict[str, Any],
    *,
    strategy: str = "ai_context",
    risk_pct: float = 1.0,
    proposed_direction: str | None = None,
    llm_reasons: list[str] | None = None,
    calibrate: bool = True,
) -> TradingSignal | None:
    """
    Build a BTC paper signal from context.

    Direction is locked by machine score (LLM cannot flip it).
    Reasons must be concrete (ETF / Funding / OI / …) — vague phrases dropped.
    """
    btc = ctx.get("btc") or {}
    price = btc.get("price")
    if price is None:
        return None
    price = float(price)

    decision = score_direction_from_context(ctx)
    direction = lock_direction(proposed_direction, decision)
    if direction == "WAIT" or not decision.is_actionable:
        return None

    if direction == "LONG":
        entry_low = round(price * 0.998, 2)
        entry_high = round(price * 1.002, 2)
        stop = round(price * 0.985, 2)
        tp1 = round(price * 1.01, 2)
        tp2 = round(price * 1.02, 2)
        tp3 = round(price * 1.035, 2)
    else:
        entry_low = round(price * 0.998, 2)
        entry_high = round(price * 1.002, 2)
        stop = round(price * 1.015, 2)
        tp1 = round(price * 0.99, 2)
        tp2 = round(price * 0.98, 2)
        tp3 = round(price * 0.965, 2)

    reasons = merge_concrete_reasons(
        reasons_from_direction_factors(decision.factors),
        llm_reasons,
        limit=6,
    )
    if not reasons:
        # Last-resort concrete placeholders from score factors only
        reasons = reasons_from_direction_factors(decision.factors) or [
            f"Machine score {decision.score:+.0f}",
        ]

    conf = float(decision.confidence_raw)
    if calibrate:
        try:
            conf = calibrate_from_repository(conf)
        except Exception:
            pass

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
        confidence=min(100.0, max(0.0, conf)),
        reasons=reasons,
        strategy=strategy,
    )
    errs = sig.validate()
    if errs:
        return None
    return sig
