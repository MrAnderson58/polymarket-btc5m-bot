"""Feature filters for Experiment Engine (research only)."""

from __future__ import annotations

from typing import Any, Callable

from bot.research.market_events.expectancy_intelligence.stats import median
from bot.research.market_events.feature_validation_v1 import FEATURE_SPECS, build_feature_predicates

Predicate = Callable[[dict[str, Any]], bool]

# Map display / knowledge names → trade feature keys
_NAME_TO_KEY: dict[str, str] = {
    "funding": "funding",
    "trend": "trend",
    "oi": "oi_delta",
    "oi Δ": "oi_delta",
    "oi_delta": "oi_delta",
    "volatility": "volatility",
    "ai score": "ai_score",
    "ai_score": "ai_score",
    "fear & greed": "fear_greed",
    "fear_greed": "fear_greed",
    "neighbor ev": "neighbor_ev",
    "neighbor_ev": "neighbor_ev",
    "regime": "market_regime",
    "market_regime": "market_regime",
    "direction": "direction",
}


def resolve_feature_key(name: str) -> str | None:
    s = str(name or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in _NAME_TO_KEY:
        return _NAME_TO_KEY[low]
    for key, label, _ in FEATURE_SPECS:
        if label.lower() == low or key.lower() == low:
            return key
    return None


def high_median_pred(trades: list[dict[str, Any]], key: str) -> tuple[Predicate | None, str]:
    vals = [float(t[key]) for t in trades if isinstance(t.get(key), (int, float))]
    med = median(vals)
    if med is None:
        return None, f"{key}: no values"
    return (
        lambda t, k=key, m=med: isinstance(t.get(k), (int, float)) and float(t[k]) > float(m),
        f"{key} > median ({med})",
    )


def low_median_pred(trades: list[dict[str, Any]], key: str) -> tuple[Predicate | None, str]:
    vals = [float(t[key]) for t in trades if isinstance(t.get(key), (int, float))]
    med = median(vals)
    if med is None:
        return None, f"{key}: no values"
    return (
        lambda t, k=key, m=med: isinstance(t.get(k), (int, float)) and float(t[k]) <= float(m),
        f"{key} ≤ median ({med})",
    )


def predicate_for_feature(
    trades: list[dict[str, Any]],
    feature_name: str,
    *,
    prefer_rule: str | None = None,
) -> tuple[Predicate | None, str, str | None]:
    """Return (predicate, rule_text, feature_key) using FV best-half or rule hint."""
    key = resolve_feature_key(feature_name)
    if key is None:
        return None, f"unknown feature {feature_name}", None
    rule_hint = (prefer_rule or "").lower()
    if "≤" in rule_hint or "<=" in rule_hint or "low" in rule_hint or "≤ median" in rule_hint:
        pred, rule = low_median_pred(trades, key)
        return pred, rule, key
    if ">" in rule_hint or "high" in rule_hint:
        pred, rule = high_median_pred(trades, key)
        return pred, rule, key
    # Default: Feature Validation best-half
    preds = build_feature_predicates(trades)
    spec = preds.get(key) or {}
    return spec.get("predicate"), str(spec.get("rule") or key), key


def and_pred(a: Predicate, b: Predicate) -> Predicate:
    return lambda t: a(t) and b(t)


def apply_pred(trades: list[dict[str, Any]], pred: Predicate | None) -> list[dict[str, Any]]:
    if pred is None:
        return []
    return [t for t in trades if pred(t)]
