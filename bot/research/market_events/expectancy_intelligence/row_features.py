"""Build feature dicts from S55 feature rows."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.expectancy_intelligence.stats import safe_float


def parse_features_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        blob = json.loads(raw) if isinstance(raw, str) else {}
        return dict(blob) if isinstance(blob, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def row_to_features(row: Any) -> dict[str, Any]:
    blob = parse_features_json(row["features_json"] if "features_json" in row.keys() else None)
    out = dict(blob)
    for col in (
        "symbol", "direction", "hour", "weekday", "volatility", "atr", "rsi",
        "funding", "oi_delta", "etf_flow", "macro_score", "news_score", "ai_score",
        "trend", "volume", "fear_greed", "btc_dominance", "spread", "funding_sign",
        "market_regime", "shock_score", "decision_confidence", "confidence",
    ):
        try:
            if col in row.keys() and row[col] is not None:
                out[col] = row[col]
        except Exception:
            pass
    if out.get("symbol") is None and "symbol" in row.keys():
        out["symbol"] = row["symbol"]
    if out.get("direction") is None and "direction" in row.keys():
        out["direction"] = row["direction"]
    if out.get("confidence") is None:
        out["confidence"] = out.get("decision_confidence")
    return out


def estimate_from_blob(row: Any, blob: dict[str, Any]) -> dict[str, Any]:
    est = blob.get("gate_estimate") if isinstance(blob.get("gate_estimate"), dict) else {}
    return {
        "expected_pnl_pct": safe_float(
            est.get("expected_pnl_pct", row["gate_expected_pnl_pct"] if "gate_expected_pnl_pct" in row.keys() else None)
        ),
        "winrate": safe_float(est.get("winrate")),
        "similar_count": int(
            est.get("similar_count")
            or est.get("n")
            or (row["similar_count"] if "similar_count" in row.keys() else 0)
            or 0
        ),
    }
