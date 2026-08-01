"""Cluster trades by causal explanation."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

CLUSTERS: tuple[str, ...] = (
    "funding_driven",
    "liquidity_driven",
    "momentum_driven",
    "news_driven",
    "mean_reversion",
    "trend_continuation",
    "noise",
)


def assign_causal_cluster(row: dict[str, Any]) -> str:
    primary = str(row.get("primary_cause") or "")
    secondary = str(row.get("secondary_cause") or "")
    contrib = row.get("contributions") or {}

    if primary == "noise":
        return "noise"

    def pct(*keys: str) -> float:
        return sum(float(contrib.get(k) or 0.0) for k in keys)

    funding = pct("funding")
    liq = pct("oi", "volume")
    mom = pct("ema", "macd", "adx")
    news = pct("news", "ai")
    mr = pct("rsi")
    trend = pct("ema", "macd", "adx", "regime")
    atr = pct("atr")

    scores = {
        "funding_driven": funding + (10.0 if primary == "funding" else 0.0),
        "liquidity_driven": liq + atr * 0.3 + (10.0 if primary in ("oi", "volume") else 0.0),
        "momentum_driven": mom + atr * 0.5 + (10.0 if primary in ("macd", "adx", "atr") else 0.0),
        "news_driven": news + (10.0 if primary in ("news", "ai") else 0.0),
        "mean_reversion": mr + (10.0 if primary == "rsi" else 0.0),
        "trend_continuation": trend + (5.0 if secondary in ("ema", "regime") else 0.0),
        "noise": 5.0 if primary == "noise" else 0.0,
    }
    # Default noise if all weak
    best = max(scores.items(), key=lambda t: t[1])
    if best[1] < 12.0:
        return "noise"
    return best[0]


def cluster_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        name = assign_causal_cluster(r)
        r["causal_cluster"] = name
        buckets[name].append({
            "trade_id": r.get("trade_id"),
            "primary_cause": r.get("primary_cause"),
            "secondary_cause": r.get("secondary_cause"),
            "pnl": r.get("pnl"),
            "confidence": r.get("confidence"),
        })
    summary = {}
    for name in CLUSTERS:
        items = buckets.get(name) or []
        pnls = [float(x.get("pnl") or 0) for x in items]
        summary[name] = {
            "n": len(items),
            "share": round(len(items) / max(1, len(rows)), 4),
            "mean_pnl": round(sum(pnls) / len(pnls), 4) if pnls else None,
            "primary_mode": (
                Counter(x.get("primary_cause") for x in items).most_common(1)[0][0]
                if items else None
            ),
            "examples": items[:10],
        }
    top = sorted(
        ({"cluster": k, **v} for k, v in summary.items()),
        key=lambda x: -int(x.get("n") or 0),
    )
    return {"clusters": summary, "top_clusters": top, "cluster_names": list(CLUSTERS)}


__all__ = ["CLUSTERS", "assign_causal_cluster", "cluster_trades"]
