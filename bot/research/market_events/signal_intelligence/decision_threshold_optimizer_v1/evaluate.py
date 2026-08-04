"""Vectorized threshold evaluation over cached module outputs."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.grid import (
    ThresholdSet,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def accept_mask(cache: dict[str, Any], thr: ThresholdSet) -> np.ndarray:
    """Boolean mask of accepted trades under thr — pure numpy, no DB."""
    fp_pass = cache["fp_sim"] >= thr.min_fingerprint
    tl_pass = cache["tl_sim"] >= thr.min_timeline
    rules_ok = cache["rules_n"] >= thr.min_rules
    supporting = (
        fp_pass.astype(np.int16)
        + tl_pass.astype(np.int16)
        + cache["dna_pass"].astype(np.int16)
        + (cache["rules_n"] >= 1).astype(np.int16)
        + cache["edge_pass"].astype(np.int16)
        + cache["replay_pass"].astype(np.int16)
        + cache["causal_pass"].astype(np.int16)
        + cache["brain_pass"].astype(np.int16)
    )
    hist_ok = (cache["hist_wr"] >= thr.min_hist_wr) & (cache["hist_pf"] >= thr.min_hist_pf)
    structure_ok = cache["dna_pass"] | (cache["rules_n"] >= 1) | cache["edge_pass"]
    sim_ok = fp_pass | tl_pass
    return (
        (~cache["blocked"])
        & (~cache["brain_no"])
        & (~cache["conflict"])
        & hist_ok
        & sim_ok
        & structure_ok
        & (supporting >= thr.min_supporting)
        & (cache["conf"] >= thr.min_confidence)
        & rules_ok
        & cache["dir_ok"]
    )


def _metrics_from_pnls(pnls: np.ndarray) -> dict[str, Any]:
    lst = [float(x) for x in pnls.tolist()] if len(pnls) else []
    met = trade_metrics(lst)
    extra = cluster_performance(lst) if lst else {}
    return {
        "trades": int(met.get("n") or 0),
        "wr": met.get("wr"),
        "pf": met.get("pf"),
        "pf_inf": met.get("pf_inf"),
        "ev": met.get("ev"),
        "sharpe": extra.get("sharpe"),
        "max_dd": extra.get("max_dd"),
        "total": met.get("total"),
    }


def evaluate_thresholds(
    cache: dict[str, Any],
    thr: ThresholdSet,
    *,
    baseline_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Score one threshold set: trading metrics + precision/recall/F1 vs profitable trades."""
    mask = accept_mask(cache, thr)
    pnl = cache["pnl"]
    accepted_pnls = pnl[mask]
    stats = _metrics_from_pnls(accepted_pnls)

    profitable = pnl > 0
    # Classification vs profitable baseline trades
    tp = int(np.sum(mask & profitable))
    fp = int(np.sum(mask & ~profitable))
    fn = int(np.sum(~mask & profitable))
    tn = int(np.sum(~mask & ~profitable))
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall)
        else 0.0
    )

    false_rejects = int(np.sum(~mask & profitable))
    false_approvals = int(np.sum(mask & ~profitable))
    rejected = int(np.sum(~mask))
    accepted = int(np.sum(mask))

    # Top rejection reason (coarse) among rejected
    reasons = _top_rejection_reason(cache, thr, ~mask)

    out = {
        "thresholds": thr.as_dict(),
        "label": thr.label(),
        **stats,
        "accepted": accepted,
        "rejected": rejected,
        "false_rejects": false_rejects,
        "false_approvals": false_approvals,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "top_rejection_reason": reasons,
    }
    if baseline_mask is not None:
        out["delta_vs_baseline_trades"] = accepted - int(np.sum(baseline_mask))
    return out


def _top_rejection_reason(
    cache: dict[str, Any],
    thr: ThresholdSet,
    rejected_mask: np.ndarray,
) -> str:
    if not np.any(rejected_mask):
        return "—"
    idx = np.where(rejected_mask)[0]
    counts: dict[str, int] = {}

    def add(name: str, m: np.ndarray) -> None:
        counts[name] = counts.get(name, 0) + int(np.sum(m))

    add("blocked/brain", cache["blocked"][idx] | cache["brain_no"][idx] | cache["conflict"][idx])
    add("hist_wr/pf", ~((cache["hist_wr"][idx] >= thr.min_hist_wr) & (cache["hist_pf"][idx] >= thr.min_hist_pf)))
    add("fingerprint/timeline", ~((cache["fp_sim"][idx] >= thr.min_fingerprint) | (cache["tl_sim"][idx] >= thr.min_timeline)))
    add("structure", ~(cache["dna_pass"][idx] | (cache["rules_n"][idx] >= 1) | cache["edge_pass"][idx]))
    add("supporting", True)  # filled below
    fp_pass = cache["fp_sim"] >= thr.min_fingerprint
    tl_pass = cache["tl_sim"] >= thr.min_timeline
    supporting = (
        fp_pass.astype(np.int16)
        + tl_pass.astype(np.int16)
        + cache["dna_pass"].astype(np.int16)
        + (cache["rules_n"] >= 1).astype(np.int16)
        + cache["edge_pass"].astype(np.int16)
        + cache["replay_pass"].astype(np.int16)
        + cache["causal_pass"].astype(np.int16)
        + cache["brain_pass"].astype(np.int16)
    )
    counts["supporting"] = int(np.sum(supporting[idx] < thr.min_supporting))
    add("confidence", cache["conf"][idx] < thr.min_confidence)
    add("rules", cache["rules_n"][idx] < thr.min_rules)
    add("direction", ~cache["dir_ok"][idx])
    if not counts:
        return "—"
    return max(counts.items(), key=lambda x: x[1])[0]


def binding_threshold(
    cache: dict[str, Any],
    i: int,
    thr: ThresholdSet,
) -> str | None:
    """Which single threshold gate rejects trade i under thr (first failing)."""
    if cache["blocked"][i] or cache["brain_no"][i] or cache["conflict"][i]:
        return "hard_veto"
    if not (cache["hist_wr"][i] >= thr.min_hist_wr and cache["hist_pf"][i] >= thr.min_hist_pf):
        if cache["hist_wr"][i] < thr.min_hist_wr:
            return "min_hist_wr"
        return "min_hist_pf"
    fp_ok = cache["fp_sim"][i] >= thr.min_fingerprint
    tl_ok = cache["tl_sim"][i] >= thr.min_timeline
    if not (fp_ok or tl_ok):
        if cache["fp_sim"][i] < thr.min_fingerprint and cache["tl_sim"][i] < thr.min_timeline:
            # pick the closer miss
            fp_gap = thr.min_fingerprint - cache["fp_sim"][i]
            tl_gap = thr.min_timeline - cache["tl_sim"][i]
            return "min_fingerprint" if fp_gap <= tl_gap else "min_timeline"
        return "min_fingerprint" if not fp_ok else "min_timeline"
    if not (cache["dna_pass"][i] or cache["rules_n"][i] >= 1 or cache["edge_pass"][i]):
        return "structure"
    supporting = int(
        fp_ok
        + tl_ok
        + bool(cache["dna_pass"][i])
        + (cache["rules_n"][i] >= 1)
        + bool(cache["edge_pass"][i])
        + bool(cache["replay_pass"][i])
        + bool(cache["causal_pass"][i])
        + bool(cache["brain_pass"][i])
    )
    if supporting < thr.min_supporting:
        return "min_supporting"
    if cache["conf"][i] < thr.min_confidence:
        return "min_confidence"
    if cache["rules_n"][i] < thr.min_rules:
        return "min_rules"
    if not cache["dir_ok"][i]:
        return "direction"
    return None


__all__ = ["accept_mask", "binding_threshold", "evaluate_thresholds"]
