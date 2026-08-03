"""Regime transition probabilities."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

CANON = ("BULL", "RANGE", "BEAR", "PANIC", "RECOVERY", "UNK")


def _canon_regime(raw: Any) -> str:
    s = str(raw or "UNK").upper()
    if "PANIC" in s or "CRASH" in s:
        return "PANIC"
    if "RECOVER" in s:
        return "RECOVERY"
    if "BULL" in s or "RISK_ON" in s or "UPTREND" in s:
        return "BULL"
    if "BEAR" in s or "RISK_OFF" in s or "DOWNTREND" in s:
        return "BEAR"
    if "RANGE" in s or "SIDE" in s or "CHOP" in s:
        return "RANGE"
    if s in CANON:
        return s
    return "UNK"


def regime_transitions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: int(r.get("closed_at") or r.get("opened_at") or 0))
    regs = [_canon_regime(r.get("regime")) for r in ordered]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for a, b in zip(regs, regs[1:]):
        counts[a][b] += 1
    matrix: dict[str, dict[str, float]] = {}
    for a in CANON:
        total = sum(counts[a].values()) or 0
        matrix[a] = {}
        for b in CANON:
            c = int(counts[a].get(b) or 0)
            matrix[a][b] = round(c / total, 4) if total else 0.0
    return {
        "states": list(CANON),
        "counts": {a: dict(counts[a]) for a in CANON if counts[a]},
        "probs": matrix,
        "n_transitions": max(0, len(regs) - 1),
    }


__all__ = ["regime_transitions"]
