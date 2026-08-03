"""Decision Replay — counterfactual filter over production closed trades."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def _pnl(trade: dict[str, Any]) -> float:
    v = trade.get("pnl")
    if v is None:
        v = trade.get("pnl_pct")
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _dir(trade: dict[str, Any]) -> str:
    return str(trade.get("direction") or "").upper()


def _perf(pnls: Sequence[float]) -> dict[str, Any]:
    met = trade_metrics(pnls)
    if met.get("pf_inf"):
        signed = [float(p) for p in pnls if abs(float(p)) > 1e-12]
        if signed:
            met2 = trade_metrics(signed)
            if met2.get("pf") is not None:
                met["pf"] = met2["pf"]
                met["pf_inf"] = False
            # keep full-set WR/EV
            full = trade_metrics(pnls)
            met["wr"] = full.get("wr")
            met["ev"] = full.get("ev")
            met["n"] = full.get("n")
            met["confidence"] = full.get("confidence")
    extra = cluster_performance(list(pnls))
    return {
        "n": met.get("n"),
        "wr": met.get("wr"),
        "pf": met.get("pf"),
        "pf_inf": met.get("pf_inf"),
        "ev": met.get("ev"),
        "sharpe": extra.get("sharpe"),
        "max_dd": extra.get("max_dd"),
        "total": met.get("total"),
        "confidence": met.get("confidence"),
    }


def run_decision_replay(
    ctx: dict[str, Any],
    *,
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Apply Decision Engine to every CLOSED trade.
    Keep trade only if decision=TRADE and direction matches production direction.
    """
    rows = trades if trades is not None else list(ctx.get("trades") or [])
    production_pnls = [_pnl(t) for t in rows]
    kept_pnls: list[float] = []
    kept_ids: list[int] = []
    decisions: list[dict[str, Any]] = []
    long_n = short_n = 0

    for t in rows:
        d = decide_one(ctx, t)
        decisions.append({
            "trade_id": d.get("trade_id"),
            "decision": d.get("decision"),
            "direction": d.get("direction"),
            "confidence": d.get("confidence"),
            "why": d.get("why"),
        })
        prod_dir = _dir(t)
        if d.get("decision") == "TRADE" and d.get("direction") and d.get("direction") == prod_dir:
            kept_pnls.append(_pnl(t))
            kept_ids.append(int(d.get("trade_id") or 0))
            if d.get("direction") == "LONG":
                long_n += 1
            else:
                short_n += 1

    prod = _perf(production_pnls)
    filt = _perf(kept_pnls) if kept_pnls else _perf([])
    skipped = len(rows) - len(kept_pnls)

    # Latest decision sample for terminal WHY block
    sample = None
    if rows:
        latest = max(rows, key=lambda r: int(r.get("opened_at") or r.get("closed_at") or 0))
        sample = decide_one(ctx, latest)

    return {
        "ok": True,
        "production": {"label": "Production", **prod},
        "decision_engine": {
            "label": "Decision Engine",
            **filt,
            "long": long_n,
            "short": short_n,
        },
        "skipped": skipped,
        "n_production": len(rows),
        "n_kept": len(kept_pnls),
        "kept_trade_ids": kept_ids[:200],
        "sample_decision": sample,
        "n_decisions": len(decisions),
        "research_only": True,
    }


__all__ = ["run_decision_replay"]
