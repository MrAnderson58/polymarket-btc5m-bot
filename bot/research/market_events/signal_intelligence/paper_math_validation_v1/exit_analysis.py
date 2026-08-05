"""Exit analysis + self-learning logs (no strategy changes)."""

from __future__ import annotations

from typing import Any, Sequence


def analyze_exit(row: dict[str, Any]) -> dict[str, Any]:
    """Compare expected vs actual for a closed paper trade."""
    pnl = row.get("pnl")
    try:
        pnl_f = float(pnl) if pnl is not None else None
    except Exception:
        pnl_f = None
    actual_result = row.get("result")
    if actual_result is None and pnl_f is not None:
        actual_result = "WIN" if pnl_f > 0 else ("LOSS" if pnl_f < 0 else "FLAT")

    exp_wr = row.get("historical_wr")
    exp_ev = row.get("historical_ev")
    exp_dd = row.get("expected_drawdown")
    actual_ev = pnl_f  # per-trade EV realization
    actual_dd = None
    if pnl_f is not None and pnl_f < 0:
        actual_dd = abs(pnl_f)

    miss = None
    try:
        ewr = float(exp_wr) if exp_wr is not None else None
        if ewr is not None and ewr > 1.5:
            ewr = ewr / 100.0
        if ewr is not None and ewr >= 0.7 and actual_result == "LOSS":
            miss = "false_positive_expected_win"
        elif ewr is not None and ewr < 0.5 and actual_result == "WIN":
            miss = "false_negative_unexpected_win"
    except Exception:
        pass
    if miss is None and exp_ev is not None and pnl_f is not None:
        try:
            if float(exp_ev) > 0 and pnl_f < 0:
                miss = miss or "ev_miss"
        except Exception:
            pass

    return {
        "trade_id": int(row.get("trade_id") or 0),
        "book": row.get("book"),
        "expected_wr": exp_wr,
        "actual_result": actual_result,
        "expected_ev": exp_ev,
        "actual_ev": actual_ev,
        "expected_dd": exp_dd,
        "actual_dd": actual_dd,
        "miss_reason": miss,
        "pnl": pnl_f,
    }


def learning_events(
    candidates: Sequence[dict[str, Any]],
    decisions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Log false positive / false negative / module disagreement / confidence error.
    Does NOT change strategy.
    """
    events: list[dict[str, Any]] = []
    by_id = {int(d.get("trade_id") or 0): d for d in decisions}
    for c in candidates:
        tid = int(c.get("trade_id") or 0)
        d = by_id.get(tid) or c
        accepted = int(c.get("accepted") or 0) == 1
        pnl = d.get("pnl")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except Exception:
            pnl_f = None
        if accepted and pnl_f is not None and pnl_f < 0:
            events.append({
                "type": "false_positive",
                "trade_id": tid,
                "detail": "math_accepted_but_loss",
            })
        if (not accepted) and pnl_f is not None and pnl_f > 0:
            # only if rejection was math-side
            events.append({
                "type": "false_negative",
                "trade_id": tid,
                "detail": "math_rejected_but_win",
            })
        brain = c.get("brain_score") if c.get("brain_score") is not None else c.get("brain")
        try:
            if brain is not None and float(brain) < 0.5 and accepted:
                events.append({
                    "type": "module_disagreement",
                    "trade_id": tid,
                    "detail": "brain_low_but_accepted",
                })
        except Exception:
            pass
        conf = c.get("confidence") or c.get("decision_score")
        try:
            if conf is not None and float(conf) >= 0.75 and pnl_f is not None and pnl_f < 0:
                events.append({
                    "type": "confidence_error",
                    "trade_id": tid,
                    "detail": "high_conf_loss",
                })
        except Exception:
            pass
    return events


__all__ = ["analyze_exit", "learning_events"]
