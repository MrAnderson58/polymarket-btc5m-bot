"""Mine top False Reject / False Accept patterns."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence


def _pattern_key(r: dict[str, Any]) -> str:
    reasons = r.get("reasons") or []
    head = "|".join(str(x)[:60] for x in reasons[:3]) or "none"
    return f"{r.get('primary_module')}:{r.get('error_class')}:{head}"


def mine_patterns(
    records: Sequence[dict[str, Any]],
    *,
    kind: str,
    top_n: int = 100,
) -> list[dict[str, Any]]:
    """kind = false_reject | false_accept."""
    if kind == "false_reject":
        pool = [r for r in records if r.get("confusion") == "FN"]
    else:
        pool = [r for r in records if r.get("confusion") == "FP"]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pool:
        buckets[_pattern_key(r)].append(r)

    scored: list[dict[str, Any]] = []
    for key, items in buckets.items():
        pnls = [float(x["pnl"]) for x in items if x.get("pnl") is not None]
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls) if pnls else 0.0
        mean = total / len(pnls) if pnls else 0.0
        scored.append({
            "pattern_key": key,
            "kind": kind,
            "pattern": key,
            "primary_module": items[0].get("primary_module"),
            "error_class": items[0].get("error_class"),
            "n": len(items),
            "total_pnl": round(total, 4),
            "mean_pnl": round(mean, 4),
            "wr": round(100.0 * wins / len(pnls), 2) if pnls else None,
            "recovered_ev": round(mean, 4) if kind == "false_reject" else round(-abs(mean), 4),
            "sample_trade_ids": [int(x["trade_id"]) for x in items[:5]],
            "sample_reasons": (items[0].get("reasons") or [])[:4],
        })
    if kind == "false_reject":
        scored.sort(key=lambda r: (float(r.get("total_pnl") or 0), int(r.get("n") or 0)), reverse=True)
    else:
        # most expensive false accepts first (most negative total)
        scored.sort(key=lambda r: (float(r.get("total_pnl") or 0), -int(r.get("n") or 0)))
    return scored[:top_n]


__all__ = ["mine_patterns"]
