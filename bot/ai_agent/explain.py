"""Explainability Engine — rule-based decision reasons (no LLM)."""

from __future__ import annotations

from typing import Any


def build_explanation(
    features: dict[str, Any],
    similar: dict[str, Any],
    *,
    decision: str,
    ai_score: float,
    confidence: float,
) -> dict[str, Any]:
    positive: list[str] = []
    negative: list[str] = []

    regime = features.get("market_regime") or features.get("regime_label") or "Range"
    if regime in ("Range", "Mean Reversion"):
        positive.append(f"+ {regime} regime")
    elif regime in ("Panic", "News Spike", "Low Liquidity"):
        negative.append(f"− {regime} regime")

    accel = features.get("btc_acceleration")
    if accel is not None:
        if abs(accel) < 3:
            positive.append("+ BTC slowing")
        elif abs(accel) > 12:
            negative.append("− BTC acceleration medium")

    entry = features.get("entry_bucket") or features.get("entry_price")
    if entry is not None:
        positive.append(f"+ Entry {float(entry):.2f}")

    hist_pf = similar.get("profit_factor", 0)
    if similar.get("similar_count", 0) >= 5:
        pf_s = f"{hist_pf:.2f}" if hist_pf != float("inf") else "inf"
        if hist_pf >= 1.5:
            positive.append(f"+ Historical PF {pf_s}")
        elif hist_pf < 1.0:
            negative.append(f"− Historical PF {pf_s}")

    spread = features.get("spread")
    if spread is not None:
        if spread <= 0.015:
            positive.append("+ Tight spread")
        elif spread > 0.025:
            negative.append("− Spread slightly elevated")

    btc_dir = features.get("btc_direction", "flat")
    side = features.get("side", "NO")
    if (side == "NO" and btc_dir == "down") or (side == "YES" and btc_dir == "up"):
        positive.append(f"+ BTC direction aligned ({btc_dir})")
    elif btc_dir in ("up", "down"):
        negative.append(f"− BTC direction {btc_dir} vs {side}")

    n = similar.get("similar_count", 0)
    if n < 30:
        negative.append(f"− Low similar sample (n={n})")

    return {
        "decision": decision,
        "ai_score": ai_score,
        "confidence_pct": confidence,
        "samples": n,
        "positive": positive,
        "negative": negative,
        "reasons": positive + negative,
    }
