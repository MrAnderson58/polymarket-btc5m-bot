"""Counterfactual estimates: what if a feature were absent?"""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    CAUSE_FEATURES,
)


def _metrics(pnls: np.ndarray) -> dict[str, float | None]:
    if len(pnls) == 0:
        return {"n": 0, "ev": None, "wr": None, "pf": None}
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gw = float(wins.sum()) if len(wins) else 0.0
    gl = float(abs(losses.sum())) if len(losses) else 0.0
    pf = (gw / gl) if gl > 1e-12 else (None if gw > 0 else 0.0)
    return {
        "n": int(len(pnls)),
        "ev": round(float(pnls.mean()), 6),
        "wr": round(100.0 * float(np.mean(pnls > 0)), 4),
        "pf": round(pf, 6) if pf is not None else None,
    }


def counterfactual_ablation(
    rows: list[dict[str, Any]],
    *,
    feature: str,
    threshold_pct: float = 10.0,
) -> dict[str, Any]:
    """
    Estimate metrics when ablating trades where `feature` contribution >= threshold.

    Interprets as "without this causal driver" cohort vs baseline.
    """
    pnls = np.asarray([float(r.get("pnl") or 0.0) for r in rows], dtype=float)
    base = _metrics(pnls)
    if feature not in CAUSE_FEATURES or not rows:
        return {
            "feature": feature,
            "baseline": base,
            "without": base,
            "delta_ev": 0.0,
            "delta_wr": 0.0,
            "delta_pf": None,
        }

    keep = []
    for i, r in enumerate(rows):
        c = float((r.get("contributions") or {}).get(feature) or 0.0)
        if c < threshold_pct:
            keep.append(pnls[i])
    without = _metrics(np.asarray(keep, dtype=float)) if keep else _metrics(np.asarray([], dtype=float))

    def d(a, b):
        if a is None or b is None:
            return None
        return round(float(a) - float(b), 6)

    return {
        "feature": feature,
        "threshold_pct": threshold_pct,
        "baseline": base,
        "without": without,
        "delta_ev": d(without.get("ev"), base.get("ev")),
        "delta_wr": d(without.get("wr"), base.get("wr")),
        "delta_pf": d(without.get("pf"), base.get("pf")),
        "n_ablated": int(base["n"] or 0) - int(without["n"] or 0),
    }


def trade_counterfactuals(
    row: dict[str, Any],
    *,
    features: tuple[str, ...] = ("funding", "oi", "atr", "volume", "news"),
) -> dict[str, Any]:
    """
    Per-trade local counterfactual: scale expected outcome by removing contribution share.
    """
    pnl = float(row.get("pnl") or 0.0)
    contrib = row.get("contributions") or {}
    out: dict[str, Any] = {}
    for f in features:
        share = float(contrib.get(f) or 0.0) / 100.0
        # Residual expectancy proxy
        residual = pnl * (1.0 - share)
        out[f] = {
            "contribution_pct": round(share * 100.0, 4),
            "observed_pnl": round(pnl, 6),
            "without_feature_pnl": round(residual, 6),
            "delta_pnl": round(residual - pnl, 6),
        }
    return out


def portfolio_counterfactuals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = ("funding", "oi", "atr", "volume", "fear", "news", "rsi", "ema")
    return [counterfactual_ablation(rows, feature=f) for f in targets]


__all__ = [
    "counterfactual_ablation",
    "portfolio_counterfactuals",
    "trade_counterfactuals",
]
