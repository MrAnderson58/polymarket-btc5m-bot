"""Rolling metrics, calibration curves, and promotion recommendation."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.shadow_live_v1.actions import (
    action_hit,
    realized_ev_for_action,
)

ROLLING_WINDOWS = (50, 100, 500, 1000)
CONFIDENCE_LEVELS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


def _pf(pnls: list[float]) -> float | None:
    gains = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    if losses <= 1e-12:
        return None if gains <= 0 else float("inf")
    return round(gains / losses, 4)


def _wr(hits: list[bool]) -> float | None:
    if not hits:
        return None
    return round(sum(1 for h in hits if h) / len(hits), 4)


def evaluate_row(row: dict[str, Any]) -> dict[str, Any]:
    pnl = row.get("pnl")
    if pnl is None:
        return {**row, "evaluated": False, "winner": "PENDING"}
    pnl_f = float(pnl)
    p_hit = action_hit(str(row.get("production_action")), pnl_f)
    b_hit = action_hit(str(row.get("brain_action")), pnl_f)
    from bot.research.market_events.signal_intelligence.shadow_live_v1.actions import pick_winner

    winner = pick_winner(
        production_action=str(row.get("production_action")),
        brain_action=str(row.get("brain_action")),
        pnl=pnl_f,
    )
    return {
        **row,
        "evaluated": True,
        "production_hit": 1 if p_hit else 0 if p_hit is not None else None,
        "brain_hit": 1 if b_hit else 0 if b_hit is not None else None,
        "winner": winner,
        "production_ev": realized_ev_for_action(str(row.get("production_action")), pnl_f),
        "brain_ev": realized_ev_for_action(str(row.get("brain_action")), pnl_f),
    }


def slice_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [r for r in rows if r.get("evaluated") or r.get("pnl") is not None]
    if not evaluated:
        return {
            "n": 0,
            "brain_hit_rate": None,
            "production_hit_rate": None,
            "delta_wr": None,
            "delta_ev": None,
            "delta_pf": None,
            "false_positives": 0,
            "false_negatives": 0,
            "brain_ev": 0.0,
            "production_ev": 0.0,
        }
    b_hits: list[bool] = []
    p_hits: list[bool] = []
    b_evs: list[float] = []
    p_evs: list[float] = []
    fp = fn = 0
    for r in evaluated:
        pnl = float(r.get("pnl") or 0.0)
        ba = str(r.get("brain_action") or "").upper()
        pa = str(r.get("production_action") or "").upper()
        bh = action_hit(ba, pnl)
        ph = action_hit(pa, pnl)
        if bh is not None:
            b_hits.append(bh)
        if ph is not None:
            p_hits.append(ph)
        b_evs.append(realized_ev_for_action(ba, pnl))
        p_evs.append(realized_ev_for_action(pa, pnl))
        # FP: brain TAKE wrong side / losing take; FN: brain SKIP when TAKE would win
        if ba in ("BUY", "SELL") and bh is False:
            fp += 1
        if ba in ("SKIP", "HOLD", "NO_TRADE") and pnl > 0 and pa in ("BUY", "SELL"):
            fn += 1

    b_wr = _wr(b_hits)
    p_wr = _wr(p_hits)
    b_ev = round(float(np.sum(b_evs)), 4)
    p_ev = round(float(np.sum(p_evs)), 4)
    b_pf = _pf(b_evs)
    p_pf = _pf(p_evs)
    delta_pf = None
    if b_pf is not None and p_pf is not None and np.isfinite(b_pf) and np.isfinite(p_pf):
        delta_pf = round(b_pf - p_pf, 4)
    return {
        "n": len(evaluated),
        "brain_hit_rate": b_wr,
        "production_hit_rate": p_wr,
        "delta_wr": round((b_wr or 0) - (p_wr or 0), 4) if b_wr is not None and p_wr is not None else None,
        "brain_ev": b_ev,
        "production_ev": p_ev,
        "delta_ev": round(b_ev - p_ev, 4),
        "brain_pf": b_pf if b_pf != float("inf") else None,
        "production_pf": p_pf if p_pf != float("inf") else None,
        "delta_pf": delta_pf,
        "false_positives": fp,
        "false_negatives": fn,
        "brain_wins": sum(1 for r in evaluated if r.get("winner") == "BRAIN"),
        "production_wins": sum(1 for r in evaluated if r.get("winner") == "PRODUCTION"),
        "ties": sum(1 for r in evaluated if r.get("winner") == "TIE"),
    }


def rolling_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows newest-first or oldest-first — we take the most recent N by ts."""
    ordered = sorted(rows, key=lambda r: int(r.get("ts") or 0), reverse=True)
    out: dict[str, Any] = {}
    for w in ROLLING_WINDOWS:
        out[f"last_{w}"] = slice_metrics(ordered[:w])
    out["all"] = slice_metrics(ordered)
    return out


def confidence_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map confidence thresholds → actual win rate among brain TAKE decisions."""
    curve: list[dict[str, Any]] = []
    for thr in CONFIDENCE_LEVELS:
        subset = [
            r for r in rows
            if float(r.get("brain_confidence") or 0) >= thr
            and str(r.get("brain_action") or "").upper() in ("BUY", "SELL")
            and r.get("pnl") is not None
        ]
        hits = []
        for r in subset:
            h = action_hit(str(r.get("brain_action")), float(r["pnl"]))
            if h is not None:
                hits.append(h)
        emp = _wr(hits)
        curve.append({
            "confidence": thr,
            "n": len(subset),
            "actual_wr": emp,
            "reliable": bool(emp is not None and emp + 1e-9 >= thr - 0.10 and len(subset) >= 5),
        })
    return curve


def calibration_summary(curve: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [c for c in curve if c.get("n", 0) >= 5 and c.get("actual_wr") is not None]
    if not usable:
        return {"n_bins": 0, "mean_gap": None, "reliable_bins": 0, "reliability_score": 0.0}
    gaps = [abs(float(c["actual_wr"]) - float(c["confidence"])) for c in usable]
    reliable = sum(1 for c in usable if c.get("reliable"))
    return {
        "n_bins": len(usable),
        "mean_gap": round(float(np.mean(gaps)), 4),
        "reliable_bins": reliable,
        "reliability_score": round(reliable / max(1, len(usable)), 4),
    }


def promotion_recommendation(
    rolling: dict[str, Any],
    calibration: dict[str, Any],
) -> dict[str, Any]:
    """
    Recommendation only — never auto-promotes.

    Stages:
      NOT_READY → PROMISING → BEATS_PRODUCTION → READY_FOR_PAPER_AB
    """
    all_m = rolling.get("all") or {}
    last100 = rolling.get("last_100") or {}
    last500 = rolling.get("last_500") or {}
    n = int(all_m.get("n") or 0)
    d_ev = last100.get("delta_ev")
    d_wr = last100.get("delta_wr")
    b_wr = last100.get("brain_hit_rate")
    p_wr = last100.get("production_hit_rate")
    rel = float(calibration.get("reliability_score") or 0.0)
    mean_gap = calibration.get("mean_gap")

    reasons: list[str] = []
    status = "NOT_READY"

    if n < 50:
        reasons.append(f"insufficient_evaluated_n={n} (need ≥50)")
        return {
            "status": status,
            "recommendation": "Brain is NOT ready — keep shadow collecting.",
            "ready_for_paper_ab": False,
            "auto_promotion": False,
            "reasons": reasons,
            "n": n,
        }

    beats_100 = (
        d_ev is not None and d_ev > 0
        and d_wr is not None and d_wr >= 0
        and b_wr is not None and p_wr is not None and b_wr >= p_wr
    )
    if beats_100:
        status = "PROMISING"
        reasons.append("last_100: brain ΔEV>0 and hit-rate ≥ production")
    else:
        if d_ev is not None and d_ev > 0:
            reasons.append(f"last_100: ΔEV={d_ev} positive but ΔWR={d_wr} not yet ≥0")
        else:
            reasons.append(f"last_100: brain does not beat production (ΔEV={d_ev}, ΔWR={d_wr})")
        return {
            "status": status,
            "recommendation": "Brain is NOT ready — shadow shows no clear edge yet.",
            "ready_for_paper_ab": False,
            "auto_promotion": False,
            "reasons": reasons,
            "n": n,
        }

    n500 = int((last500.get("n") or 0))
    beats_500 = (
        n500 >= 100
        and (last500.get("delta_ev") or 0) > 0
        and (last500.get("delta_wr") or 0) >= 0
    )
    if beats_500:
        status = "BEATS_PRODUCTION"
        reasons.append("last_500: brain beats production on ΔEV/WR")
    else:
        reasons.append("need more history (last_500) confirming edge")
        return {
            "status": status,
            "recommendation": "Brain is promising — continue shadow; not yet confirmed vs production.",
            "ready_for_paper_ab": False,
            "auto_promotion": False,
            "reasons": reasons,
            "n": n,
        }

    cal_ok = rel >= 0.5 and (mean_gap is None or float(mean_gap) <= 0.20)
    n_all_ok = n >= 500
    if cal_ok and n_all_ok:
        status = "READY_FOR_PAPER_AB"
        reasons.append("calibration reliability ok and n≥500")
        return {
            "status": status,
            "recommendation": "Ready for paper A/B (recommendation only — no auto promotion).",
            "ready_for_paper_ab": True,
            "auto_promotion": False,
            "reasons": reasons,
            "n": n,
        }

    if not cal_ok:
        reasons.append(f"calibration not reliable enough (score={rel}, mean_gap={mean_gap})")
    if not n_all_ok:
        reasons.append(f"need n≥500 evaluated (have {n})")
    return {
        "status": "BEATS_PRODUCTION",
        "recommendation": "Brain beats production — improve calibration / sample size before paper A/B.",
        "ready_for_paper_ab": False,
        "auto_promotion": False,
        "reasons": reasons,
        "n": n,
    }


__all__ = [
    "CONFIDENCE_LEVELS",
    "ROLLING_WINDOWS",
    "calibration_summary",
    "confidence_curve",
    "evaluate_row",
    "promotion_recommendation",
    "rolling_statistics",
    "slice_metrics",
]
