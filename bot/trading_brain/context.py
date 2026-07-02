"""Decision Context and Explainability Context for AI Agent (no decisions)."""

from __future__ import annotations

from typing import Any


def build_decision_context(
    *,
    trade_id: int,
    features: dict[str, Any],
    similar: dict[str, Any],
    knowledge: list[dict[str, Any]],
    memory_refs: dict[str, Any],
) -> dict[str, Any]:
    """Full context for AI Agent — does NOT include ALLOW/SKIP decision."""
    outcome_block = None
    if features.get("pnl") is not None:
        outcome_block = {
            "pnl": features.get("pnl"),
            "pnl_usdc": features.get("pnl_usdc"),
            "exit_reason": features.get("exit_reason"),
            "label": features.get("outcome"),
        }

    return {
        "trade_id": trade_id,
        "observe_only": True,
        "signal": {
            "strategy": features.get("strategy_name"),
            "side": features.get("side"),
            "entry_price": features.get("entry_price"),
            "market_regime": features.get("market_regime") or features.get("regime_label"),
            "btc_move_30s": features.get("btc_move_30s"),
            "btc_acceleration": features.get("btc_acceleration"),
            "btc_direction": features.get("btc_direction"),
            "spread": features.get("spread"),
            "seconds_from_start": features.get("seconds_from_start"),
            "distance_to_strike": features.get("distance_to_strike"),
            "volatility": features.get("volatility"),
            "entry_bucket": features.get("entry_bucket"),
            "holding_bucket": features.get("holding_bucket"),
        },
        "similar_trades": {
            "count": similar.get("similar_count", 0),
            "win_rate": similar.get("win_rate", 0),
            "profit_factor": similar.get("profit_factor", 0),
            "avg_pnl": similar.get("avg_pnl", 0),
            "avg_mae": similar.get("avg_mae", 0),
            "avg_mfe": similar.get("avg_mfe", 0),
            "top_regime": similar.get("top_regime"),
            "engine": similar.get("engine", "v2"),
        },
        "knowledge_links": knowledge,
        "memory": memory_refs,
        "outcome": outcome_block,
    }


def build_explainability_context(
    features: dict[str, Any],
    similar: dict[str, Any],
    knowledge: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pre-decision explainability hints for future ALLOW/SKIP reasoning."""
    supportive: list[str] = []
    opposing: list[str] = []

    regime = features.get("market_regime") or features.get("regime_label")
    if regime in ("Range", "Mean Reversion"):
        supportive.append(f"Range regime ({regime})")
    elif regime in ("Panic", "News Spike", "Low Liquidity"):
        opposing.append(f"Risk regime ({regime})")

    n = similar.get("similar_count", 0)
    if n >= 30:
        pf = similar.get("profit_factor", 0)
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        if pf >= 1.5:
            supportive.append(f"Historical PF {pf_s} over {n} similar trades")
        elif pf < 1.0:
            opposing.append(f"Historical PF {pf_s} over {n} similar trades")
    else:
        opposing.append(f"Low similar sample (n={n})")

    for link in knowledge:
        text = f"{link['condition']}: {link['direction']} effect {link['effect']:+.1f}%"
        if link["direction"] == "positive":
            supportive.append(text)
        else:
            opposing.append(text)

    spread = features.get("spread")
    if spread is not None:
        if spread <= 0.015:
            supportive.append("Tight spread")
        elif spread > 0.025:
            opposing.append("Spread elevated")

    entry = features.get("entry_bucket") or features.get("entry_price")
    if entry is not None:
        supportive.append(f"Entry bucket {float(entry):.2f}")

    accel = features.get("btc_acceleration")
    if accel is not None and abs(accel) < 3:
        supportive.append("BTC slowing")
    elif accel is not None and abs(accel) > 12:
        opposing.append("BTC acceleration elevated")

    return {
        "supportive_reasons": supportive,
        "opposing_reasons": opposing,
        "all_reasons": supportive + opposing,
        "hint_allow_if": len(supportive) > len(opposing) + 1,
        "hint_skip_if": len(opposing) > len(supportive) + 1,
    }
