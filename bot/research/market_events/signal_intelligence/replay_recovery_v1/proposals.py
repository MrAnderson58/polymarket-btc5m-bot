"""Minimal Replay-floor change proposals — research-only, do not apply."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_stats_from_pnls,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.classify import (
    REPLAY_FLOOR,
    pnl_of,
    replay_score,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.features import (
    max_drawdown,
)


def _accepts(score: float | None, floor: float) -> bool:
    if score is None:
        return False
    return score >= floor


def simulate_floor(
    rows: list[dict[str, Any]],
    *,
    floor: float,
) -> dict[str, Any]:
    accepted_pnls: list[float] = []
    n_acc = 0
    n_missed_winners = 0
    n_saved_losers = 0
    recovered_ev = 0.0
    protected_ev = 0.0
    for r in rows:
        score = replay_score(r)
        pnl = pnl_of(r)
        acc = _accepts(score, floor)
        if acc:
            n_acc += 1
            if pnl is not None:
                accepted_pnls.append(pnl)
        else:
            if pnl is not None and pnl > 0:
                n_missed_winners += 1
                recovered_ev += pnl  # EV currently lost at this floor
            elif pnl is not None and pnl < 0:
                n_saved_losers += 1
                protected_ev += abs(pnl)

    stats = book_stats_from_pnls(accepted_pnls)
    return {
        "floor": floor,
        "n_accepted": n_acc,
        "n_rejected": len(rows) - n_acc,
        "wr": stats.get("wr"),
        "pf": stats.get("pf"),
        "ev": stats.get("ev"),
        "sharpe": stats.get("sharpe"),
        "total_pnl": stats.get("total"),
        "max_dd": max_drawdown(accepted_pnls),
        "missed_winners": n_missed_winners,
        "saved_losers": n_saved_losers,
        "recoverable_ev_if_reject": round(recovered_ev, 6),
        "protected_ev_if_reject": round(protected_ev, 6),
    }


def propose_minimal_floors(
    rows: list[dict[str, Any]],
    *,
    floors: tuple[float, ...] = (0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.10, 0.0),
    max_dd_increase_pct: float = 25.0,
) -> dict[str, Any]:
    """
    Compare alternative floors to baseline REPLAY_FLOOR.
    Prefer higher recoverable EV with max_dd not rising more than max_dd_increase_pct.
    Does NOT apply changes — research proposals only.
    """
    baseline = simulate_floor(rows, floor=REPLAY_FLOOR)
    base_dd = float(baseline.get("max_dd") or 0.0)
    candidates: list[dict[str, Any]] = []
    for fl in floors:
        sim = simulate_floor(rows, floor=fl)
        dd = float(sim.get("max_dd") or 0.0)
        if base_dd <= 1e-12:
            dd_ok = dd <= base_dd + 1e-9 or fl >= REPLAY_FLOOR
            dd_increase_pct = 0.0 if dd <= base_dd else 999.0
        else:
            dd_increase_pct = round(100.0 * (dd - base_dd) / base_dd, 4)
            dd_ok = dd_increase_pct <= max_dd_increase_pct
        # EV recovered vs baseline = missed winners EV at baseline minus at new floor
        base_lost = float(baseline.get("recoverable_ev_if_reject") or 0)
        new_lost = float(sim.get("recoverable_ev_if_reject") or 0)
        ev_recovered = round(base_lost - new_lost, 6)
        candidates.append({
            **sim,
            "dd_increase_pct_vs_baseline": dd_increase_pct,
            "dd_ok": dd_ok,
            "ev_recovered_vs_baseline": ev_recovered,
            "is_baseline": abs(fl - REPLAY_FLOOR) < 1e-12,
        })

    feasible = [c for c in candidates if c.get("dd_ok") and not c.get("is_baseline")]
    feasible.sort(key=lambda x: float(x.get("ev_recovered_vs_baseline") or 0), reverse=True)
    best = feasible[0] if feasible else None
    return {
        "baseline": baseline,
        "grid": candidates,
        "best_feasible": best,
        "max_dd_increase_pct": max_dd_increase_pct,
        "note": "research_only_do_not_apply",
    }


__all__ = ["propose_minimal_floors", "simulate_floor"]
