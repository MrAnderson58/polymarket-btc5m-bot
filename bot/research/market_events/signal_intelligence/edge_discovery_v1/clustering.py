"""Cluster discovered edges into market regimes / styles."""

from __future__ import annotations

from typing import Any

CLUSTERS: tuple[str, ...] = (
    "Liquidity",
    "Momentum",
    "Mean Reversion",
    "Breakout",
    "Trend",
    "Panic",
    "Volatility Expansion",
    "Compression",
)


def _cluster_for(rule: dict[str, Any]) -> str:
    text = " ".join(str(x) for x in (rule.get("features") or [])) + " " + str(rule.get("rule") or "")
    t = text.lower()
    if "funding" in t or "oi_delta" in t:
        if "fear" in t or "rsi_os" in t or "rsi in [0" in t:
            return "Panic"
        return "Liquidity"
    if "atr_pct" in t or "volatility" in t:
        if "<=q25" in t or "<=median" in t:
            return "Compression"
        return "Volatility Expansion"
    if "rsi_os" in t or "rsi in [0" in t or "rsi_low" in t or "rsi in [30" in t:
        return "Mean Reversion"
    if "rsi_ob" in t or "rsi in [70" in t or "rsi_high" in t:
        if "ema20" in t and (">median" in t or ">=q75" in t):
            return "Breakout"
        return "Momentum"
    if "trend" in t and (">median" in t or ">=q75" in t):
        return "Trend"
    if "ema20" in t and (">median" in t or ">=q75" in t):
        return "Momentum"
    if "ema20" in t and ("<=median" in t or "<=q25" in t):
        return "Mean Reversion"
    if "fear" in t and ("<=median" in t or "<=q25" in t or "fear_greed<=q25" in t):
        return "Panic"
    return "Trend"


def cluster_edges(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {c: [] for c in CLUSTERS}
    for rule in candidates:
        name = _cluster_for(rule)
        rule["cluster"] = name
        buckets.setdefault(name, []).append({
            "rule": rule.get("rule"),
            "edge_score": rule.get("edge_score"),
            "n": rule.get("n"),
            "status": rule.get("status"),
            "pf": rule.get("pf"),
            "expectancy": rule.get("expectancy"),
        })
    summary = {
        name: {
            "n_rules": len(items),
            "best_score": max((float(x.get("edge_score") or 0) for x in items), default=0.0),
            "rules": items[:20],
        }
        for name, items in buckets.items()
    }
    return {"clusters": summary, "cluster_names": list(CLUSTERS)}


__all__ = ["CLUSTERS", "cluster_edges"]
