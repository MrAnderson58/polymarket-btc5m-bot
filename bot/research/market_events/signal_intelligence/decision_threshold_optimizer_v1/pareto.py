"""Pareto frontier + named profiles (Aggressive → Very Conservative)."""

from __future__ import annotations

from typing import Any

PROFILE_NAMES = (
    "Aggressive",
    "Balanced",
    "Conservative",
    "Very Conservative",
)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """a dominates b on EV/WR/Sharpe/F1/precision/max_dd/false_rejects."""
    keys_max = ("ev", "wr", "sharpe", "f1", "precision")
    better_or_eq = True
    strictly = False
    for k in keys_max:
        av, bv = _num(a.get(k)), _num(b.get(k))
        if av < bv - 1e-9:
            better_or_eq = False
            break
        if av > bv + 1e-9:
            strictly = True
    if not better_or_eq:
        return False
    add, bdd = _num(a.get("max_dd"), -1e9), _num(b.get("max_dd"), -1e9)
    if add < bdd - 1e-9:
        return False
    if add > bdd + 1e-9:
        strictly = True
    afr, bfr = _num(a.get("false_rejects"), 1e9), _num(b.get("false_rejects"), 1e9)
    if afr > bfr + 1e-9:
        return False
    if afr < bfr - 1e-9:
        strictly = True
    return strictly


def pareto_frontier(results: list[dict[str, Any]], *, min_trades: int = 30) -> list[dict[str, Any]]:
    cand = [r for r in results if int(r.get("trades") or 0) >= min_trades]
    if not cand:
        cand = list(results)
    # Cap dominance checks — full O(n^2) on 36k is too slow.
    if len(cand) > 2500:
        cand = sorted(cand, key=score_profile, reverse=True)[:2500]
    front: list[dict[str, Any]] = []
    for r in cand:
        if any(dominates(o, r) for o in cand if o is not r):
            continue
        front.append(r)
    front.sort(
        key=lambda x: (_num(x.get("ev")), _num(x.get("wr")), int(x.get("trades") or 0)),
        reverse=True,
    )
    return front


def score_profile(r: dict[str, Any]) -> float:
    return (
        _num(r.get("ev")) * 2.0
        + _num(r.get("wr")) / 100.0
        + _num(r.get("sharpe"))
        + _num(r.get("f1"))
        - _num(r.get("false_rejects")) / 10000.0
    )


def select_profiles(
    frontier: list[dict[str, Any]],
    all_results: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    pool = frontier or list(all_results or [])
    if not pool:
        return {n: {} for n in PROFILE_NAMES}

    by_recall = sorted(
        pool, key=lambda x: (_num(x.get("recall")), int(x.get("trades") or 0)), reverse=True
    )
    by_score = sorted(pool, key=score_profile, reverse=True)
    by_precision = sorted(
        pool,
        key=lambda x: (_num(x.get("precision")), _num(x.get("wr")), _num(x.get("pf") or 0)),
        reverse=True,
    )
    by_safe = sorted(
        [r for r in pool if int(r.get("trades") or 0) >= 20] or pool,
        key=lambda x: (_num(x.get("wr")), _num(x.get("pf") or 0), -int(x.get("trades") or 0)),
        reverse=True,
    )

    profiles = {
        "Aggressive": by_recall[0],
        "Balanced": by_score[0],
        "Conservative": by_precision[0],
        "Very Conservative": by_safe[0],
    }
    used: set[tuple[Any, Any]] = set()
    out: dict[str, dict[str, Any]] = {}
    lists = {
        "Aggressive": by_recall,
        "Balanced": by_score,
        "Conservative": by_precision,
        "Very Conservative": by_safe,
    }
    for name, primary in profiles.items():
        key = (primary.get("label"), primary.get("trades"))
        if key not in used:
            out[name] = primary
            used.add(key)
            continue
        chosen = primary
        for cand in lists[name]:
            ck = (cand.get("label"), cand.get("trades"))
            if ck not in used:
                chosen = cand
                break
        out[name] = chosen
        used.add((chosen.get("label"), chosen.get("trades")))
    return out


def best_overall(
    profiles: dict[str, dict[str, Any]], frontier: list[dict[str, Any]]
) -> dict[str, Any]:
    bal = profiles.get("Balanced") or {}
    if bal:
        return bal
    if frontier:
        return max(frontier, key=score_profile)
    return {}


__all__ = [
    "PROFILE_NAMES",
    "best_overall",
    "dominates",
    "pareto_frontier",
    "score_profile",
    "select_profiles",
]
