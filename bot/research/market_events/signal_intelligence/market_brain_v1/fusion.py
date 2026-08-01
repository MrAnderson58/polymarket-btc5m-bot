"""Voting + Bayesian fusion + conflict detection + calibration."""

from __future__ import annotations

from typing import Any

import numpy as np


def _signed(direction: str) -> float:
    d = str(direction or "HOLD").upper()
    if d == "BUY":
        return 1.0
    if d == "SELL":
        return -1.0
    return 0.0


def vote_summary(opinions: list[dict[str, Any]]) -> dict[str, Any]:
    buys = sells = holds = 0
    for o in opinions:
        d = o.get("direction")
        if d == "BUY":
            buys += 1
        elif d == "SELL":
            sells += 1
        else:
            holds += 1
    return {"buy": buys, "sell": sells, "hold": holds, "n": len(opinions)}


def detect_conflict(opinions: list[dict[str, Any]], *, conf_floor: float = 0.55) -> dict[str, Any]:
    """High conflict when multiple strong modules disagree BUY vs SELL."""
    strong = [
        o for o in opinions
        if float(o.get("confidence") or 0) >= conf_floor and o.get("direction") in ("BUY", "SELL")
    ]
    strong_buy = [o for o in strong if o["direction"] == "BUY"]
    strong_sell = [o for o in strong if o["direction"] == "SELL"]
    # Require at least one strong module on each side
    conflict = bool(strong_buy) and bool(strong_sell)
    buy_mass = sum(
        float(o["confidence"]) * float(o["weight"]) * float(o["quality"])
        for o in opinions if o.get("direction") == "BUY"
    )
    sell_mass = sum(
        float(o["confidence"]) * float(o["weight"]) * float(o["quality"])
        for o in opinions if o.get("direction") == "SELL"
    )
    total = buy_mass + sell_mass + 1e-12
    balance = abs(buy_mass - sell_mass) / total
    conflict_score = round(1.0 - balance, 4) if conflict else round(max(0.0, 0.35 - balance), 4)
    # HIGH only when both sides have ≥2 strong votes OR masses are near-parity
    both_sides_deep = len(strong_buy) >= 2 and len(strong_sell) >= 2
    near_parity = conflict and conflict_score >= 0.55
    if conflict and (both_sides_deep or near_parity):
        level = "HIGH"
    elif conflict:
        level = "MED"
    else:
        level = "LOW"
    return {
        "conflict": conflict,
        "level": level,
        "conflict_score": conflict_score,
        "buy_mass": round(buy_mass, 4),
        "sell_mass": round(sell_mass, 4),
        "strong_modules": [
            {"module": o["module"], "direction": o["direction"], "confidence": o["confidence"]}
            for o in strong
        ],
    }


def bayesian_fusion(
    opinions: list[dict[str, Any]],
    *,
    conflict: dict[str, Any] | None = None,
    prior_buy: float = 0.5,
) -> dict[str, Any]:
    """
    Fuse module opinions into BUY/SELL/HOLD with probability, EV, PF, risk, confidence.

    Each module contributes log-odds proportional to weight * confidence * quality.
    """
    conflict = conflict or detect_conflict(opinions)
    # Hard veto on HIGH conflict
    if conflict.get("level") == "HIGH":
        return {
            "decision": "NO_TRADE",
            "direction": "HOLD",
            "probability_buy": 0.5,
            "expected_ev": 0.0,
            "expected_pf": 1.0,
            "risk": "HIGH",
            "confidence_raw": 0.15,
            "conflict": conflict,
            "votes": vote_summary(opinions),
        }

    log_odds = float(np.log(prior_buy / (1.0 - prior_buy)))
    edge_acc = 0.0
    w_sum = 0.0
    for o in opinions:
        w = float(o.get("weight") or 0.0) * float(o.get("confidence") or 0.0) * float(o.get("quality") or 0.0)
        if w <= 0:
            continue
        s = _signed(str(o.get("direction")))
        # Map confidence to probability tilt
        p_mod = 0.5 + 0.45 * s * float(o.get("confidence") or 0.0)
        p_mod = min(0.98, max(0.02, p_mod))
        log_odds += w * float(np.log(p_mod / (1.0 - p_mod)))
        edge_acc += w * float(o.get("edge") or 0.0)
        w_sum += w

    prob_buy = float(1.0 / (1.0 + np.exp(-log_odds)))
    if prob_buy >= 0.58:
        decision = "BUY"
        direction = "BUY"
    elif prob_buy <= 0.42:
        decision = "SELL"
        direction = "SELL"
    else:
        decision = "HOLD"
        direction = "HOLD"

    expected_ev = round(edge_acc / max(w_sum, 1e-12), 4)
    # PF proxy from |EV| and win-prob
    p_win = prob_buy if direction == "BUY" else (1.0 - prob_buy if direction == "SELL" else 0.5)
    expected_pf = round(max(0.1, p_win / max(1e-6, 1.0 - p_win)), 4)
    conf_raw = abs(prob_buy - 0.5) * 2.0
    # Soften by conflict
    conf_raw *= 1.0 - 0.5 * float(conflict.get("conflict_score") or 0.0)
    risk = "LOW" if conf_raw >= 0.65 and conflict.get("level") == "LOW" else (
        "MED" if conf_raw >= 0.4 else "HIGH"
    )
    return {
        "decision": decision,
        "direction": direction,
        "probability_buy": round(prob_buy, 4),
        "expected_ev": expected_ev,
        "expected_pf": expected_pf,
        "risk": risk,
        "confidence_raw": round(max(0.0, min(1.0, conf_raw)), 4),
        "conflict": conflict,
        "votes": vote_summary(opinions),
    }


def calibrate_confidence(
    raw_scores: list[float],
    outcomes_win: list[bool],
    *,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Build reliability table: bin raw confidence → empirical winrate."""
    if not raw_scores:
        return []
    xs = np.asarray(raw_scores, dtype=float)
    ys = np.asarray(outcomes_win, dtype=float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    table = []
    for i in range(n_bins):
        lo, hi = float(bins[i]), float(bins[i + 1])
        mask = (xs >= lo) & (xs < hi if i < n_bins - 1 else xs <= hi)
        if not np.any(mask):
            table.append({"lo": lo, "hi": hi, "n": 0, "emp_winrate": None, "mean_raw": None})
            continue
        table.append({
            "lo": lo,
            "hi": hi,
            "n": int(mask.sum()),
            "emp_winrate": round(float(ys[mask].mean()), 4),
            "mean_raw": round(float(xs[mask].mean()), 4),
        })
    return table


def apply_calibration(raw: float, table: list[dict[str, Any]]) -> float:
    if not table:
        return float(raw)
    for row in table:
        lo, hi = float(row["lo"]), float(row["hi"])
        if lo <= raw <= hi and row.get("emp_winrate") is not None:
            # Blend raw with empirical
            emp = float(row["emp_winrate"])
            return round(0.4 * float(raw) + 0.6 * emp, 4)
    return float(raw)


def agreement_matrix(opinions: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Pairwise agreement in [-1,1] based on signed directions."""
    names = [o["module"] for o in opinions]
    signed = {o["module"]: _signed(o["direction"]) for o in opinions}
    mat: dict[str, dict[str, float]] = {}
    for a in names:
        mat[a] = {}
        for b in names:
            if a == b:
                mat[a][b] = 1.0
            else:
                sa, sb = signed[a], signed[b]
                if sa == 0 or sb == 0:
                    mat[a][b] = 0.0
                elif sa == sb:
                    mat[a][b] = 1.0
                else:
                    mat[a][b] = -1.0
    return mat


__all__ = [
    "agreement_matrix",
    "apply_calibration",
    "bayesian_fusion",
    "calibrate_confidence",
    "detect_conflict",
    "vote_summary",
]
