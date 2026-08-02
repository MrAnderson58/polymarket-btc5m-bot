"""Attribution metrics: ΔEV, ΔPF, ΔWR, MI, IG, Precision/Recall/F1."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    realized_ev,
    signal_correct,
)


def _pf(pnls: list[float]) -> float | None:
    gains = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    if losses <= 1e-12:
        return None if gains <= 0 else None
    return round(gains / losses, 4)


def _wr(hits: list[bool]) -> float | None:
    if not hits:
        return None
    return round(sum(1 for h in hits if h) / len(hits), 4)


def _entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return float(-(p * np.log2(p) + (1 - p) * np.log2(1 - p)))


def mutual_information_binary(pred: list[bool], truth: list[bool]) -> float:
    """MI between binary prediction and binary outcome (bits)."""
    n = len(pred)
    if n == 0 or len(truth) != n:
        return 0.0
    # joint
    counts = {(False, False): 0, (False, True): 0, (True, False): 0, (True, True): 0}
    for p, t in zip(pred, truth):
        counts[(bool(p), bool(t))] += 1
    mi = 0.0
    for (p, t), c in counts.items():
        if c == 0:
            continue
        p_xy = c / n
        p_x = sum(counts[(p, tt)] for tt in (False, True)) / n
        p_y = sum(counts[(pp, t)] for pp in (False, True)) / n
        if p_x <= 0 or p_y <= 0:
            continue
        mi += p_xy * np.log2(p_xy / (p_x * p_y))
    return round(float(max(0.0, mi)), 6)


def information_gain(pred: list[bool], truth: list[bool]) -> float:
    """IG = H(Y) - H(Y|X) for binary labels."""
    n = len(truth)
    if n == 0:
        return 0.0
    p_y = sum(1 for t in truth if t) / n
    h_y = _entropy(p_y)
    # H(Y|X)
    h_yx = 0.0
    for xv in (False, True):
        idx = [i for i, p in enumerate(pred) if bool(p) == xv]
        if not idx:
            continue
        p_x = len(idx) / n
        p_y_x = sum(1 for i in idx if truth[i]) / len(idx)
        h_yx += p_x * _entropy(p_y_x)
    return round(float(max(0.0, h_y - h_yx)), 6)


def precision_recall_f1(pred_take: list[bool], truth_win: list[bool]) -> dict[str, float | None]:
    """Precision/Recall/F1 for TAKE decisions predicting wins."""
    tp = fp = fn = 0
    for p, t in zip(pred_take, truth_win):
        if p and t:
            tp += 1
        elif p and not t:
            fp += 1
        elif (not p) and t:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    if prec is None or rec is None or (prec + rec) == 0:
        f1 = None
    else:
        f1 = 2 * prec * rec / (prec + rec)
    return {
        "precision": round(prec, 4) if prec is not None else None,
        "recall": round(rec, 4) if rec is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def score_actions(
    actions: list[str],
    pnls: list[float],
) -> dict[str, Any]:
    """Aggregate EV/PF/WR + classification metrics for a list of actions."""
    assert len(actions) == len(pnls)
    if not actions:
        return {
            "n": 0,
            "ev": 0.0,
            "mean_ev": None,
            "pf": None,
            "wr": None,
            "mutual_information": 0.0,
            "information_gain": 0.0,
            "precision": None,
            "recall": None,
            "f1": None,
        }
    evs = [realized_ev(a, p) for a, p in zip(actions, pnls)]
    hits = [signal_correct(a, p) for a, p in zip(actions, pnls)]
    correct_buys = sum(1 for a, p in zip(actions, pnls) if str(a).upper() == "BUY" and p > 0)
    correct_sells = sum(1 for a, p in zip(actions, pnls) if str(a).upper() == "SELL" and p < 0)
    takes = sum(1 for a in actions if str(a).upper() in ("BUY", "SELL"))
    correct_takes = correct_buys + correct_sells
    prec = correct_takes / takes if takes else None
    buy_opp = sum(1 for p in pnls if p > 0)
    sell_opp = sum(1 for p in pnls if p < 0)
    rec = correct_takes / max(1, buy_opp + sell_opp) if (buy_opp + sell_opp) else None
    if prec is not None and rec is not None and (prec + rec) > 0:
        f1 = 2 * prec * rec / (prec + rec)
    else:
        f1 = None

    mi = mutual_information_binary(hits, [p > 0 for p in pnls])
    ig = information_gain(hits, [p > 0 for p in pnls])
    return {
        "n": len(actions),
        "ev": round(float(sum(evs)), 6),
        "mean_ev": round(float(np.mean(evs)), 6) if evs else None,
        "pf": _pf(evs),
        "wr": _wr(hits),
        "mutual_information": mi,
        "information_gain": ig,
        "precision": round(prec, 4) if prec is not None else None,
        "recall": round(rec, 4) if rec is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def delta_metrics(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    def d(key: str) -> float | None:
        a, b = base.get(key), other.get(key)
        if a is None or b is None:
            return None
        try:
            return round(float(b) - float(a), 6)
        except Exception:
            return None

    return {
        "delta_ev": d("ev"),
        "delta_pf": d("pf"),
        "delta_wr": d("wr"),
        "delta_mi": d("mutual_information"),
        "delta_ig": d("information_gain"),
        "delta_f1": d("f1"),
    }


__all__ = [
    "delta_metrics",
    "information_gain",
    "mutual_information_binary",
    "precision_recall_f1",
    "score_actions",
]
