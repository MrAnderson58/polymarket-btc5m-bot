"""Distribution / concept / target / feature drift detectors."""

from __future__ import annotations

from typing import Any

import numpy as np

FEATURE_KEYS = (
    "rsi",
    "funding",
    "atr_pct",
    "atr",
    "oi_delta",
    "fear_greed",
    "news_score",
    "ai_score",
    "confidence",
)


def _psi(expected: np.ndarray, actual: np.ndarray, *, bins: int = 10) -> float:
    """Population Stability Index between two 1d samples."""
    if expected.size < 5 or actual.size < 5:
        return 0.0
    qs = np.linspace(0, 100, bins + 1)
    cuts = np.unique(np.percentile(expected, qs))
    if cuts.size < 3:
        return 0.0
    e_hist, _ = np.histogram(expected, bins=cuts)
    a_hist, _ = np.histogram(actual, bins=cuts)
    e = e_hist.astype(float) + 1e-6
    a = a_hist.astype(float) + 1e-6
    e /= e.sum()
    a /= a.sum()
    return float(np.sum((a - e) * np.log(a / e)))


def _split(occurrences: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(occurrences) < 10:
        return occurrences, occurrences
    mid = len(occurrences) // 2
    return occurrences[:mid], occurrences[mid:]


def detect_drift(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compare early vs recent half of chronological occurrences.
    Returns distribution/concept/target/feature drift scores.
    """
    ordered = sorted(occurrences, key=lambda o: float(o.get("ts") or 0))
    early, late = _split(ordered)
    # Target drift: pnl distribution
    e_pnl = np.array([float(o["pnl"]) for o in early if o.get("pnl") is not None], dtype=float)
    l_pnl = np.array([float(o["pnl"]) for o in late if o.get("pnl") is not None], dtype=float)
    target_psi = _psi(e_pnl, l_pnl) if e_pnl.size and l_pnl.size else 0.0
    target_mean_shift = None
    if e_pnl.size and l_pnl.size:
        target_mean_shift = round(float(l_pnl.mean() - e_pnl.mean()), 6)

    # Concept drift: winrate shift
    e_wr = float((e_pnl > 0).mean()) if e_pnl.size else None
    l_wr = float((l_pnl > 0).mean()) if l_pnl.size else None
    concept = abs((l_wr or 0) - (e_wr or 0)) if e_wr is not None and l_wr is not None else 0.0

    # Feature drift per key
    feature_scores: dict[str, float] = {}
    for key in FEATURE_KEYS:
        ev = np.array([float(o[key]) for o in early if o.get(key) is not None], dtype=float)
        lv = np.array([float(o[key]) for o in late if o.get(key) is not None], dtype=float)
        if ev.size >= 5 and lv.size >= 5:
            feature_scores[key] = round(_psi(ev, lv), 4)

    dist_score = round(float(np.mean(list(feature_scores.values()))) if feature_scores else target_psi, 4)
    # Aggregate drift 0..1-ish
    drift_score = round(
        min(
            1.0,
            0.35 * min(1.0, target_psi / 0.25)
            + 0.35 * min(1.0, concept / 0.15)
            + 0.30 * min(1.0, dist_score / 0.25),
        ),
        4,
    )
    level = "HIGH" if drift_score >= 0.55 else ("MED" if drift_score >= 0.30 else "LOW")
    return {
        "distribution_drift": round(dist_score, 4),
        "concept_drift": round(concept, 4),
        "target_drift": round(target_psi, 4),
        "target_mean_shift": target_mean_shift,
        "feature_drift": feature_scores,
        "drift_score": drift_score,
        "drift_level": level,
        "early_n": len(early),
        "late_n": len(late),
    }


__all__ = ["detect_drift"]
