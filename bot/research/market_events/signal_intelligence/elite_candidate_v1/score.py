"""Candidate Score 0..100 + category labels (research-only)."""

from __future__ import annotations

from typing import Any

# Weights sum to 100
WEIGHTS: dict[str, float] = {
    "decision": 30.0,
    "brain": 20.0,
    "replay": 10.0,
    "fingerprint": 10.0,
    "timeline": 10.0,
    "dna": 10.0,
    "rules": 5.0,
    "regime": 5.0,
}

# Soft pass targets — journal modules rarely sit near 1.0; scale so stack-pass → high score.
PASS_TARGETS: dict[str, float] = {
    "decision": 0.55,
    "brain": 0.35,
    "replay": 0.45,
    "fingerprint": 0.55,
    "timeline": 0.60,
    "dna": 0.55,
    "rules": 1.0,
    "regime": 0.40,
}

STORE_CATEGORIES = frozenset({"ELITE", "A+", "A"})
ALL_CATEGORIES = ("ELITE", "A+", "A", "B", "IGNORE")


def _raw01(v: Any) -> float:
    try:
        x = float(v)
    except Exception:
        return 0.0
    if x != x:  # NaN
        return 0.0
    # Journal is 0..1; similarity_pct style is 10..100
    if x > 1.0:
        if x >= 10.0:
            x = x / 100.0
        else:
            x = 1.0
    return max(0.0, min(1.0, x))


def _soft_pass(raw: float, target: float) -> float:
    if target <= 0:
        return _raw01(raw)
    return max(0.0, min(1.0, float(raw) / float(target)))


def component_scores(
    *,
    decision_confidence: float | None,
    brain: float | None,
    replay: float | None,
    fingerprint: float | None,
    timeline: float | None,
    dna: float | None,
    rules: float | None,
    regime: float | None,
    decision: str | None = None,
    edge: float | None = None,
    causality: float | None = None,
) -> dict[str, float]:
    """Normalize each pipeline module to 0..1 (soft pass vs research targets)."""
    d = _raw01(decision_confidence)
    b = _raw01(brain)
    # Journal brain is often ~0 — fall back to causality / decision confidence.
    if b < 0.05:
        b = max(b, _raw01(causality), d * 0.65)
    r = _raw01(replay)
    if r < 0.05:
        r = max(r, _raw01(edge), (_raw01(fingerprint) + _raw01(timeline)) / 2.0)
    fp = _raw01(fingerprint)
    tl = _raw01(timeline)
    dn = _raw01(dna)
    ru = _raw01(rules if rules is not None else 0.0)
    rg = _raw01(regime)

    if decision and str(decision).upper() in ("NO TRADE", "NO_TRADE", "REJECT"):
        d *= 0.35

    return {
        "decision": _soft_pass(d, PASS_TARGETS["decision"]),
        "brain": _soft_pass(b, PASS_TARGETS["brain"]),
        "replay": _soft_pass(r, PASS_TARGETS["replay"]),
        "fingerprint": _soft_pass(fp, PASS_TARGETS["fingerprint"]),
        "timeline": _soft_pass(tl, PASS_TARGETS["timeline"]),
        "dna": _soft_pass(dn, PASS_TARGETS["dna"]),
        "rules": _soft_pass(ru, PASS_TARGETS["rules"]),
        "regime": _soft_pass(rg, PASS_TARGETS["regime"]),
    }


def candidate_score(components: dict[str, float]) -> float:
    total = 0.0
    for key, w in WEIGHTS.items():
        total += w * float(components.get(key) or 0.0)
    return round(max(0.0, min(100.0, total)), 4)


def categorize(score: float) -> str:
    s = float(score)
    if s >= 95.0:
        return "ELITE"
    if s >= 90.0:
        return "A+"
    if s >= 80.0:
        return "A"
    if s >= 70.0:
        return "B"
    return "IGNORE"


def should_store(category: str) -> bool:
    return category in STORE_CATEGORIES


def score_breakdown(components: dict[str, float]) -> dict[str, float]:
    return {k: round(WEIGHTS[k] * float(components.get(k) or 0.0), 4) for k in WEIGHTS}


__all__ = [
    "ALL_CATEGORIES",
    "PASS_TARGETS",
    "STORE_CATEGORIES",
    "WEIGHTS",
    "candidate_score",
    "categorize",
    "component_scores",
    "score_breakdown",
    "should_store",
]
