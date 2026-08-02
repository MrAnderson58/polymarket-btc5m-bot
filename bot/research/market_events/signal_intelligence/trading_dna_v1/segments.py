"""PnL percentile segments + group feature profiles."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.trading_dna_v1.features import (
    DNA_CATEGORICAL,
    DNA_NUMERIC,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)

SEGMENT_DEFS: tuple[tuple[str, float, float], ...] = (
    ("TOP_5", 0.95, 1.01),
    ("TOP_10", 0.90, 1.01),
    ("TOP_20", 0.80, 1.01),
    ("MIDDLE", 0.20, 0.80),
    ("BOTTOM_20", 0.0, 0.20),
    ("BOTTOM_10", 0.0, 0.10),
    ("BOTTOM_5", 0.0, 0.05),
)


def split_segments(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split CLOSED trades by pnl percentiles (inclusive lower, exclusive upper except top)."""
    if not rows:
        return {name: [] for name, _, _ in SEGMENT_DEFS}
    pnls = np.array([float(r["pnl"]) for r in rows], dtype=float)
    order = np.argsort(pnls)  # ascending
    n = len(rows)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.linspace(0.0, 1.0, n, endpoint=False) if n else np.array([])
    # percentile rank in [0,1)
    out: dict[str, list[dict[str, Any]]] = {}
    for name, lo, hi in SEGMENT_DEFS:
        if name.startswith("TOP"):
            # top k% = highest pnl
            out[name] = [rows[i] for i in range(n) if ranks[i] >= lo]
        elif name.startswith("BOTTOM"):
            out[name] = [rows[i] for i in range(n) if ranks[i] < hi]
        else:
            out[name] = [rows[i] for i in range(n) if lo <= ranks[i] < hi]
    return out


def _num_summary(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(len(arr)),
        "mean": round(float(np.mean(arr)), 4),
        "median": round(float(np.median(arr)), 4),
        "p25": round(float(np.quantile(arr, 0.25)), 4),
        "p75": round(float(np.quantile(arr, 0.75)), 4),
    }


def profile_group(rows: list[dict[str, Any]], *, name: str) -> dict[str, Any]:
    metrics = trade_metrics([float(r["pnl"]) for r in rows])
    numeric = {}
    for k in DNA_NUMERIC:
        vals = [float(r[k]) for r in rows if r.get(k) is not None]
        numeric[k] = _num_summary(vals)
    categorical = {}
    for k in DNA_CATEGORICAL:
        c = Counter(str(r.get(k)) for r in rows if r.get(k) is not None)
        categorical[k] = [{"value": a, "n": b, "share": round(b / max(1, len(rows)), 4)} for a, b in c.most_common(8)]
    return {
        "segment": name,
        "metrics": metrics,
        "numeric": numeric,
        "categorical": categorical,
    }


def profile_all(segments: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [profile_group(segments[name], name=name) for name, _, _ in SEGMENT_DEFS]


def common_factors(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Diff TOP_10 vs BOTTOM_10 for common DNA factors."""
    top = next((p for p in profiles if p["segment"] == "TOP_10"), None)
    bot = next((p for p in profiles if p["segment"] == "BOTTOM_10"), None)
    if not top or not bot:
        return {}
    diffs = []
    for k in DNA_NUMERIC:
        tm = (top["numeric"].get(k) or {}).get("median")
        bm = (bot["numeric"].get(k) or {}).get("median")
        if tm is None or bm is None:
            continue
        diffs.append({
            "feature": k,
            "top10_median": tm,
            "bottom10_median": bm,
            "delta": round(float(tm) - float(bm), 4),
        })
    diffs.sort(key=lambda d: -abs(float(d["delta"])))
    cats = []
    for k in ("direction", "hour", "weekday", "regime", "pattern", "funding_sign", "oi_sign"):
        ttop = (top["categorical"].get(k) or [{}])[0]
        tbot = (bot["categorical"].get(k) or [{}])[0]
        cats.append({
            "feature": k,
            "top10_mode": ttop.get("value"),
            "top10_share": ttop.get("share"),
            "bottom10_mode": tbot.get("value"),
            "bottom10_share": tbot.get("share"),
        })
    return {"numeric_deltas": diffs[:12], "categorical_modes": cats}


__all__ = [
    "SEGMENT_DEFS",
    "common_factors",
    "profile_all",
    "profile_group",
    "split_segments",
]
