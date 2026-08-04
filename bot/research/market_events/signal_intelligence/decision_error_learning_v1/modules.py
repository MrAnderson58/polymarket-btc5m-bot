"""Module attribution + recovered EV if module ignored."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.decision_error_learning_v1.classify import (
    MODULES,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def _module_mentioned(reasons: list[str], module: str) -> bool:
    text = " ".join(reasons).lower()
    aliases = {
        "replay": ("replay",),
        "fingerprint": ("fingerprint",),
        "timeline": ("timeline",),
        "dna": ("dna",),
        "rules": ("rule",),
        "edge": ("no historical edge", "edge weak", "edge "),
        "brain": ("brain",),
        "causality": ("causal",),
        "confidence": ("supporting modules", "confidence <", "min_confidence"),
        "regime": ("regime",),
        "direction": ("direction",),
    }
    return any(a in text for a in aliases.get(module, (module,)))


def recovered_if_module_ignored(
    records: Sequence[dict[str, Any]],
    module: str,
) -> dict[str, Any]:
    """
    Counterfactual: FN trades primarily blocked by `module` would have been taken.
    Recovered EV = mean pnl of those false rejects (+ FP avoided if module caused FA — skipped).
    """
    fr_pnls: list[float] = []
    fa_pnls: list[float] = []
    for r in records:
        reasons = r.get("reasons") or []
        blamed = (r.get("primary_module") == module) or _module_mentioned(reasons, module)
        if not blamed:
            continue
        pnl = r.get("pnl")
        if pnl is None:
            continue
        if r.get("confusion") == "FN":
            fr_pnls.append(float(pnl))
        elif r.get("confusion") == "FP":
            fa_pnls.append(float(pnl))

    fr_met = trade_metrics(fr_pnls) if fr_pnls else trade_metrics([])
    # Ignoring a module on FP doesn't recover — those were accepted; recovered EV focuses on FR
    recovered_ev = fr_met.get("ev")
    recovered_total = fr_met.get("total")
    return {
        "module": module,
        "false_reject_n": len(fr_pnls),
        "false_accept_n": len(fa_pnls),
        "false_reject_pct": None,  # filled by ranker with denominators
        "false_accept_pct": None,
        "recovered_ev": recovered_ev,
        "recovered_pf": fr_met.get("pf"),
        "recovered_wr": fr_met.get("wr"),
        "recovered_total_pnl": recovered_total,
        "recovered_sharpe": (cluster_performance(fr_pnls).get("sharpe") if fr_pnls else None),
    }


def module_error_ranking(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    n_fn = sum(1 for r in records if r.get("confusion") == "FN")
    n_fp = sum(1 for r in records if r.get("confusion") == "FP")
    rows: list[dict[str, Any]] = []
    for mod in MODULES:
        stats = recovered_if_module_ignored(records, mod)
        fr_n = int(stats["false_reject_n"] or 0)
        fa_n = int(stats["false_accept_n"] or 0)
        stats["false_reject_pct"] = round(100.0 * fr_n / n_fn, 2) if n_fn else 0.0
        stats["false_accept_pct"] = round(100.0 * fa_n / n_fp, 2) if n_fp else 0.0
        # rank score: higher recovered EV + FR share = worse (largest error source)
        stats["error_score"] = round(
            float(stats.get("recovered_total_pnl") or 0)
            + float(stats.get("false_reject_pct") or 0) * 10.0
            + float(stats.get("false_accept_pct") or 0) * 5.0,
            4,
        )
        rows.append(stats)
    rows.sort(key=lambda r: float(r.get("error_score") or 0), reverse=True)
    return rows


def adaptive_suggestions(ranking: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Research-only suggestions from module error ranking."""
    out: list[dict[str, Any]] = []
    if not ranking:
        return out
    worst = ranking[0]
    active = [
        r for r in ranking
        if int(r.get("false_reject_n") or 0) + int(r.get("false_accept_n") or 0) > 0
    ]
    best = min(active or ranking, key=lambda r: float(r.get("error_score") or 0))
    for r in ranking:
        mod = r["module"]
        fr = float(r.get("false_reject_pct") or 0)
        fa = float(r.get("false_accept_pct") or 0)
        rev = r.get("recovered_ev")
        if fr >= 15 and (rev is None or float(rev) > 0):
            verdict = "too strict"
        elif fa >= 15:
            verdict = "too weak"
        elif fr < 5 and fa < 5:
            verdict = "excellent"
        else:
            verdict = "acceptable"
        out.append({
            "module": mod,
            "verdict": verdict,
            "false_reject_pct": fr,
            "false_accept_pct": fa,
            "recovered_ev": rev,
        })
    # ensure worst/best annotated
    for s in out:
        if s["module"] == worst["module"]:
            s["note"] = "largest error source"
        if s["module"] == best["module"]:
            s["note"] = (s.get("note") or "") + " best module"
    return out


__all__ = [
    "adaptive_suggestions",
    "module_error_ranking",
    "recovered_if_module_ignored",
]
