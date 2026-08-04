"""Similarity search over transition patterns."""

from __future__ import annotations

from typing import Any


def pattern_similarity(a: str, b: str) -> float:
    """Simple LW-pattern similarity 0–100 (Hamming / length)."""
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    n = max(len(a), len(b))
    m = min(len(a), len(b))
    same = sum(1 for i in range(m) if a[i] == b[i])
    return round(100.0 * same / n, 2)


def find_similar_transitions(
    query: dict[str, Any],
    library: list[dict[str, Any]],
    *,
    k: int = 10,
) -> list[dict[str, Any]]:
    q_pat = str(query.get("pattern") or "")
    q_from = str(query.get("from_state") or "")
    q_to = str(query.get("to_state") or "")
    scored: list[dict[str, Any]] = []
    for row in library:
        sim = 0.0
        if q_pat and row.get("pattern"):
            sim = pattern_similarity(q_pat, str(row["pattern"]))
        else:
            # state-edge similarity
            sim = 50.0 if row.get("from_state") == q_from else 0.0
            if row.get("to_state") == q_to:
                sim += 50.0
        scored.append({**row, "similarity_pct": sim})
    scored.sort(key=lambda r: float(r.get("similarity_pct") or 0), reverse=True)
    return scored[:k]


__all__ = ["find_similar_transitions", "pattern_similarity"]
