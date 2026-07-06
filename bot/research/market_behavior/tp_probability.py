"""TP probability helpers — cumulative, monotonic by construction."""

from __future__ import annotations

from bot.research.market_behavior.config import EDGE_TP_LEVELS


def compute_cumulative_tp_hits(
    entry_price: float,
    max_bid: float,
    *,
    levels: tuple[float, ...] = EDGE_TP_LEVELS,
) -> dict[float, bool | None]:
    """Per-observation TP hits. None = ineligible (entry >= TP level)."""
    hits: dict[float, bool | None] = {}
    for tp in levels:
        if entry_price >= tp:
            hits[tp] = None
        else:
            hits[tp] = max_bid >= tp
    return hits


def aggregate_tp_probabilities(
    rows: list,
    *,
    levels: tuple[float, ...] = EDGE_TP_LEVELS,
    entry_attr: str = "entry_price",
    hits_attr: str = "tp_reached",
) -> dict[float, float]:
    """Aggregate eligible-sample probabilities; monotonic by construction."""
    probs: dict[float, float] = {}
    for tp in levels:
        eligible = [
            r for r in rows
            if getattr(r, hits_attr, {}).get(tp) is not None
        ]
        if not eligible:
            probs[tp] = 0.0
        else:
            probs[tp] = sum(
                1 for r in eligible if getattr(r, hits_attr)[tp]
            ) / len(eligible)
    return enforce_monotonic(probs, levels=levels)


def enforce_monotonic(
    probs: dict[float, float],
    *,
    levels: tuple[float, ...] = EDGE_TP_LEVELS,
) -> dict[float, float]:
    """Ensure TP55 >= TP60 >= ... by propagating higher hit rates downward."""
    ordered = sorted(levels)
    out = dict(probs)
    for i in range(len(ordered) - 2, -1, -1):
        lo, hi = ordered[i], ordered[i + 1]
        out[lo] = max(out.get(lo, 0.0), out.get(hi, 0.0))
    return out


def check_tp_monotonic(probs: dict[float, float]) -> list[str]:
    """Return warning messages if TP ordering is violated."""
    ordered = sorted(probs.keys())
    warnings: list[str] = []
    for i in range(len(ordered) - 1):
        lo, hi = ordered[i], ordered[i + 1]
        p_lo, p_hi = probs.get(lo, 0.0), probs.get(hi, 0.0)
        if p_lo + 1e-9 < p_hi:
            warnings.append(
                f"TP monotonicity violation: TP{int(lo*100)}={p_lo:.2%} < "
                f"TP{int(hi*100)}={p_hi:.2%}"
            )
        if p_lo < 0 or p_hi < 0:
            warnings.append("negative TP probability detected")
    return warnings
