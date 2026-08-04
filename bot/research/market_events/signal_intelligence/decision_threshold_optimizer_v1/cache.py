"""Cache module outputs once — reuse for all threshold combos (no N+1)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
)


def _f(v: Any, default: float = -1.0) -> float:
    try:
        if v is None or v == "inf":
            return default if v is None else 99.0
        return float(v)
    except Exception:
        return default


def _pnl(trade: dict[str, Any]) -> float:
    v = trade.get("pnl")
    if v is None:
        v = trade.get("pnl_pct")
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _rules_matched(ctx: dict[str, Any], trade: dict[str, Any]) -> tuple[int, bool]:
    """Count READY rule matches (cap 3) + blocked flag."""
    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    row = (ctx.get("enriched_by_id") or {}).get(tid) or trade
    for p in ctx.get("block_preds") or []:
        try:
            if p["pred"](row):
                return 0, True
        except Exception:
            continue
    n = 0
    for p in ctx.get("ready_preds") or []:
        try:
            if p["pred"](row):
                n += 1
                if n >= 3:
                    break
        except Exception:
            continue
    return n, False


def build_feature_cache(
    ctx: dict[str, Any],
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    One decide_one (+ rules count) per trade.
    Returns numpy arrays for vectorized threshold search.
    """
    rows = trades if trades is not None else list(ctx.get("trades") or [])
    n = len(rows)
    fp_sim = np.full(n, -1.0)
    tl_sim = np.full(n, -1.0)
    hist_wr = np.full(n, -1.0)
    hist_pf = np.full(n, -1.0)
    hist_ev = np.full(n, -1.0)
    conf = np.zeros(n)
    rules_n = np.zeros(n, dtype=np.int16)
    dna_pass = np.zeros(n, dtype=bool)
    edge_pass = np.zeros(n, dtype=bool)
    replay_pass = np.zeros(n, dtype=bool)
    causal_pass = np.zeros(n, dtype=bool)
    brain_pass = np.zeros(n, dtype=bool)
    blocked = np.zeros(n, dtype=bool)
    brain_no = np.zeros(n, dtype=bool)
    conflict = np.zeros(n, dtype=bool)
    dir_ok = np.zeros(n, dtype=bool)
    pnl = np.zeros(n)
    trade_ids = np.zeros(n, dtype=np.int64)
    symbols: list[str] = []
    why_baseline: list[list[str]] = []

    for i, trade in enumerate(rows):
        d = decide_one(ctx, trade)
        scores = d.get("scores") or {}
        fp = scores.get("fingerprint") or {}
        tl = scores.get("timeline") or {}
        dna = scores.get("dna") or {}
        rules = scores.get("rules") or {}
        edge = scores.get("edge") or {}
        replay = scores.get("replay") or {}
        causal = scores.get("causality") or {}
        brain = scores.get("brain") or {}

        fp_sim[i] = _f(fp.get("similarity_pct"))
        tl_sim[i] = _f(tl.get("similarity_pct"))
        hist_wr[i] = _f(d.get("historical_wr"))
        hist_pf[i] = _f(d.get("historical_pf"), default=-1.0)
        hist_ev[i] = _f(d.get("historical_ev"))
        conf[i] = float(d.get("confidence") or 0.0)
        rn, blk = _rules_matched(ctx, trade)
        if rules.get("blocked"):
            blk = True
            rn = 0
        rules_n[i] = int(rn)
        dna_pass[i] = bool(dna.get("pass"))
        edge_pass[i] = bool(edge.get("pass"))
        replay_pass[i] = bool(replay.get("pass"))
        causal_pass[i] = bool(causal.get("pass"))
        brain_pass[i] = bool(brain.get("pass"))
        blocked[i] = bool(blk)
        brain_no[i] = str(brain.get("brain_decision") or "") == "NO_TRADE"
        conflict[i] = str(brain.get("conflict") or "") == "HIGH"
        dir_ok[i] = d.get("direction") in ("LONG", "SHORT") or any(
            (scores.get(m) or {}).get("direction") in ("LONG", "SHORT")
            for m in ("brain", "fingerprint", "timeline", "edge", "replay", "causality", "dna")
        )
        # Prefer voted direction existence from decide
        if d.get("direction") in ("LONG", "SHORT") or d.get("decision") == "TRADE":
            dir_ok[i] = True
        elif d.get("decision") == "NO TRADE" and any(
            "Direction unclear" in str(x) for x in (d.get("why") or [])
        ):
            dir_ok[i] = False
        else:
            # soft: any module direction
            dir_ok[i] = any(
                (scores.get(m) or {}).get("direction") in ("LONG", "SHORT")
                for m in ("brain", "fingerprint", "timeline", "edge", "dna")
            )
        pnl[i] = _pnl(trade)
        trade_ids[i] = int(d.get("trade_id") or trade.get("trade_id") or 0)
        symbols.append(str(trade.get("symbol") or d.get("symbol") or ""))
        why_baseline.append(list(d.get("why") or []))

    return {
        "ok": True,
        "n": n,
        "fp_sim": fp_sim,
        "tl_sim": tl_sim,
        "hist_wr": hist_wr,
        "hist_pf": hist_pf,
        "hist_ev": hist_ev,
        "conf": conf,
        "rules_n": rules_n,
        "dna_pass": dna_pass,
        "edge_pass": edge_pass,
        "replay_pass": replay_pass,
        "causal_pass": causal_pass,
        "brain_pass": brain_pass,
        "blocked": blocked,
        "brain_no": brain_no,
        "conflict": conflict,
        "dir_ok": dir_ok,
        "pnl": pnl,
        "trade_ids": trade_ids,
        "symbols": symbols,
        "why_baseline": why_baseline,
    }


__all__ = ["build_feature_cache"]
