"""Score existing engines for one trade — no new features."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.similarity import (
    query_similarity,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.similarity import (
    match_current,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def _dir_map(d: str | None) -> str | None:
    x = str(d or "").upper()
    if x in ("LONG", "BUY"):
        return "LONG"
    if x in ("SHORT", "SELL"):
        return "SHORT"
    return None


def score_fingerprint(ctx: dict[str, Any], trade_id: int) -> dict[str, Any]:
    idx = ctx.get("fp_index") or {}
    snap = (ctx.get("fp_by_id") or {}).get(trade_id)
    if not idx.get("ok") or not snap:
        return {
            "ok": False,
            "module": "fingerprint",
            "similarity_pct": None,
            "reason": "Fingerprint mismatch",
            "pass": False,
        }
    # Exclude self: query k+1 then drop matching trade_id
    raw = query_similarity(
        idx,
        snap.get("vector") or [],
        k=min(101, int(idx.get("n") or 100)),
        assignments=ctx.get("fp_assignments") or {},
    )
    if not raw.get("ok"):
        return {
            "ok": False,
            "module": "fingerprint",
            "similarity_pct": None,
            "reason": "Fingerprint mismatch",
            "pass": False,
        }
    # Recompute excluding self using neighbors from a direct kneighbors call
    try:
        nn = idx["nn"]
        scaler = idx["scaler"]
        rows = idx["rows"]
        from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
            VECTOR_KEYS,
        )

        q = []
        vec = snap.get("vector") or []
        means = getattr(scaler, "mean_", np.zeros(len(VECTOR_KEYS)))
        for j in range(len(VECTOR_KEYS)):
            v = vec[j] if j < len(vec) else None
            q.append(float(means[j]) if v is None or not np.isfinite(float(v)) else float(v))
        q_norm = scaler.transform(np.array(q, dtype=float).reshape(1, -1))
        k = min(100, len(rows))
        dists, inds = nn.kneighbors(q_norm, n_neighbors=min(k + 5, len(rows)))
        neighbors = []
        sims = []
        for d, i in zip(dists[0], inds[0]):
            n = rows[int(i)]
            if int(n.get("trade_id") or 0) == int(trade_id):
                continue
            neighbors.append(n)
            sims.append(100.0 / (1.0 + float(d)))
            if len(neighbors) >= k:
                break
        if not neighbors:
            return {
                "ok": False,
                "module": "fingerprint",
                "similarity_pct": None,
                "reason": "Fingerprint mismatch",
                "pass": False,
            }
        pnls = [float(n["pnl"]) for n in neighbors]
        perf = cluster_performance(pnls)
        sim = round(float(np.mean(sims)), 2)
        assigns = ctx.get("fp_assignments") or {}
        closest = None
        for n in neighbors:
            tid = int(n.get("trade_id") or 0)
            if tid in assigns:
                closest = assigns[tid]
                break
        dirs = [_dir_map(n.get("direction")) for n in neighbors]
        dirs = [d for d in dirs if d]
        dir_mode = max(set(dirs), key=dirs.count) if dirs else None
        wr = float(perf.get("wr") or 0)
        ev = float(perf.get("ev") or 0)
        pf = perf.get("pf")
        pf_ok = (pf is not None and float(pf) >= 1.15) or (pf is None and ev > 0)
        passed = sim >= 5.0 and wr >= 55.0 and ev >= 0.15 and pf_ok
        return {
            "ok": True,
            "module": "fingerprint",
            "similarity_pct": sim,
            "historical_wr": perf.get("wr"),
            "historical_pf": pf if pf is not None else ("inf" if ev > 0 else None),
            "historical_ev": perf.get("ev"),
            "closest_fingerprint": closest,
            "direction": dir_mode,
            "pass": passed,
            "reason": (
                f"Fingerprint {sim}%"
                if passed
                else ("Fingerprint mismatch" if sim < 5.0 else "No historical edge")
            ),
        }
    except Exception:
        # fallback to raw (may include self)
        sim = float(raw.get("similarity_pct") or 0)
        wr = float(raw.get("historical_wr") or 0)
        ev = float(raw.get("historical_ev") or 0)
        passed = sim >= 40 and wr >= 55 and ev >= 0.25
        return {
            "ok": True,
            "module": "fingerprint",
            "similarity_pct": raw.get("similarity_pct"),
            "historical_wr": raw.get("historical_wr"),
            "historical_pf": raw.get("historical_pf"),
            "historical_ev": raw.get("historical_ev"),
            "closest_fingerprint": raw.get("closest_fingerprint"),
            "direction": _dir_map(snap.get("direction")),
            "pass": passed,
            "reason": f"Fingerprint {raw.get('similarity_pct')}%" if passed else "Fingerprint mismatch",
        }


def score_timeline(ctx: dict[str, Any], trade_id: int) -> dict[str, Any]:
    row = (ctx.get("tl_by_id") or {}).get(trade_id)
    chains = ctx.get("tl_chains") or []
    labels_map = ctx.get("tl_labels") or {}
    by_key = ctx.get("tl_chain_by_key") or {}
    if not row or not chains:
        return {
            "ok": False,
            "module": "timeline",
            "similarity_pct": None,
            "reason": "Timeline unknown",
            "pass": False,
        }
    labs = labels_map.get(trade_id)
    if labs is None:
        from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
            label_chain,
        )

        labs = label_chain(row)
    from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
        chain_key,
    )

    key = chain_key(labs)
    exact = by_key.get(key)
    if exact:
        sim = {
            "ok": True,
            "similarity_pct": 100.0,
            "closest_chain": exact.get("id"),
            "historical_wr": exact.get("wr"),
            "historical_pf": exact.get("pf") if exact.get("pf") is not None else ("inf" if exact.get("pf_inf") else None),
            "historical_ev": exact.get("ev"),
            "confidence": exact.get("confidence"),
            "current": {"direction": row.get("direction")},
        }
    else:
        # Fast path: only top chains (already truncated in context)
        sim = match_current([row], chains, current=row)
    if not sim.get("ok"):
        return {
            "ok": False,
            "module": "timeline",
            "similarity_pct": None,
            "reason": "Timeline unknown",
            "pass": False,
        }
    pct = float(sim.get("similarity_pct") or 0)
    wr = float(sim.get("historical_wr") or 0)
    ev = float(sim.get("historical_ev") or 0)
    pf = sim.get("historical_pf")
    pf_ok = True
    if isinstance(pf, (int, float)):
        pf_ok = float(pf) >= 1.15
    elif pf == "inf":
        pf_ok = ev > 0
    passed = pct >= 30.0 and wr >= 55.0 and ev >= 0.15 and pf_ok
    return {
        "ok": True,
        "module": "timeline",
        "similarity_pct": sim.get("similarity_pct"),
        "historical_wr": sim.get("historical_wr"),
        "historical_pf": sim.get("historical_pf"),
        "historical_ev": sim.get("historical_ev"),
        "closest_chain": sim.get("closest_chain"),
        "confidence": sim.get("confidence"),
        "direction": _dir_map((sim.get("current") or {}).get("direction") or row.get("direction")),
        "pass": passed,
        "reason": f"Timeline {sim.get('similarity_pct')}%" if passed else "Timeline mismatch",
    }


def score_dna(ctx: dict[str, Any], trade_id: int) -> dict[str, Any]:
    row = (ctx.get("enriched_by_id") or {}).get(trade_id)
    preds = ctx.get("dna_preds") or []
    if not row:
        return {"ok": False, "module": "dna", "pass": False, "reason": "DNA unknown", "match": None}
    best = None
    for p in preds:
        try:
            if p["pred"](row):
                best = p
                break
        except Exception:
            continue
    if not best:
        return {
            "ok": True,
            "module": "dna",
            "pass": False,
            "match": None,
            "reason": "DNA no match",
            "similarity_pct": None,
        }
    wr = float(best.get("wr") or 0)
    ev = float(best.get("ev") or 0)
    conf = float(best.get("confidence") or 0)
    sim = round(min(99.0, 55.0 + conf * 40.0), 2)
    passed = wr >= 55.0 and ev > 0
    return {
        "ok": True,
        "module": "dna",
        "pass": passed,
        "match": best.get("label"),
        "historical_wr": best.get("wr"),
        "historical_pf": best.get("pf"),
        "historical_ev": best.get("ev"),
        "similarity_pct": sim,
        "direction": _dir_map(row.get("direction")),
        "reason": f"DNA {sim}%" if passed else "DNA weak",
    }


def score_rules(ctx: dict[str, Any], trade_id: int) -> dict[str, Any]:
    row = (ctx.get("enriched_by_id") or {}).get(trade_id)
    if not row:
        return {
            "ok": False,
            "module": "rules",
            "pass": False,
            "blocked": False,
            "reason": "Rules unknown",
        }
    for i, p in enumerate(ctx.get("block_preds") or [], start=1):
        try:
            if p["pred"](row):
                return {
                    "ok": True,
                    "module": "rules",
                    "pass": False,
                    "blocked": True,
                    "rule": f"BLOCK #{i}",
                    "match": p.get("label"),
                    "reason": f"Rule BLOCK #{i} matched",
                }
        except Exception:
            continue
    for i, p in enumerate(ctx.get("ready_preds") or [], start=1):
        try:
            if p["pred"](row):
                return {
                    "ok": True,
                    "module": "rules",
                    "pass": True,
                    "blocked": False,
                    "rule": f"Rule #{i}",
                    "match": p.get("label"),
                    "historical_wr": p.get("wr"),
                    "historical_pf": p.get("pf"),
                    "historical_ev": p.get("ev"),
                    "reason": f"Rule #{i} matched",
                }
        except Exception:
            continue
    # Soft: DNA minimal already covered; rules without READY still allow other modules
    return {
        "ok": True,
        "module": "rules",
        "pass": False,
        "blocked": False,
        "reason": "No READY rule matched",
    }


def score_edge(ctx: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        opinion_from_edge,
    )

    op = opinion_from_edge(trade, ctx.get("edges") or [])
    meta = op.get("meta") or {}
    matched = bool(meta.get("matched"))
    ev = float(meta.get("expectancy") or 0)
    pf = meta.get("pf")
    conf = float(op.get("confidence") or 0)
    sim = round(conf * 100.0, 2)
    passed = matched and ev > 0 and conf >= 0.45
    return {
        "ok": True,
        "module": "edge",
        "pass": passed,
        "similarity_pct": sim,
        "historical_ev": ev,
        "historical_pf": pf,
        "direction": _dir_map(op.get("direction")),
        "matched": matched,
        "reason": ("Edge matched" if passed else ("No historical edge" if not matched else "Edge weak")),
        "opinion": op,
    }


def score_replay(ctx: dict[str, Any], trade_id: int, trade: dict[str, Any]) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        opinion_from_replay,
    )

    replay = (ctx.get("replays") or {}).get(trade_id)
    if not replay:
        return {
            "ok": False,
            "module": "replay",
            "pass": False,
            "reason": "Replay unknown",
            "similarity_pct": None,
        }
    op = opinion_from_replay(trade, replay)
    meta = op.get("meta") or {}
    sim_n = int(meta.get("similar_n") or 0)
    sim = round(float(meta.get("similarity") or 0) * 100.0, 2)
    passed = sim_n >= 20 and float(op.get("confidence") or 0) >= 0.5
    return {
        "ok": True,
        "module": "replay",
        "pass": passed,
        "similarity_pct": sim,
        "similar_n": sim_n,
        "direction": _dir_map(op.get("direction")),
        "reason": f"Replay {sim}%" if passed else "Replay weak",
        "opinion": op,
    }


def score_causality(ctx: dict[str, Any], trade_id: int, trade: dict[str, Any]) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        opinion_from_causality,
    )

    causal = (ctx.get("causality") or {}).get(trade_id)
    if not causal:
        return {
            "ok": False,
            "module": "causality",
            "pass": False,
            "reason": "Causality unknown",
            "confidence": None,
        }
    # Strip pnl from trade copy to avoid look-ahead in fallback paths
    trade_safe = {k: v for k, v in trade.items() if k not in ("pnl", "pnl_pct", "result")}
    op = opinion_from_causality(trade_safe, causal)
    conf = float(op.get("confidence") or 0)
    passed = conf >= 0.45
    return {
        "ok": True,
        "module": "causality",
        "pass": passed,
        "confidence": conf,
        "similarity_pct": round(conf * 100.0, 2),
        "direction": _dir_map(op.get("direction")),
        "primary_cause": causal.get("primary_cause"),
        "reason": f"Causality {round(conf * 100)}%" if passed else "Causality weak",
        "opinion": op,
    }


def score_brain(ctx: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.market_brain_v1.explain import (
        explain_decision,
    )
    from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
        bayesian_fusion,
        detect_conflict,
    )
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        collect_opinions,
    )

    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    trade_safe = {k: v for k, v in trade.items() if k not in ("pnl", "pnl_pct", "result")}
    opinions = collect_opinions(
        trade_safe,
        replay=(ctx.get("replays") or {}).get(tid),
        edges=ctx.get("edges") or [],
        causal=(ctx.get("causality") or {}).get(tid),
        optimizer_state=ctx.get("optimizer_state") or {},
        experiments=ctx.get("experiments") or [],
    )
    conflict = detect_conflict(opinions)
    fusion = bayesian_fusion(opinions, conflict=conflict)
    explanation = explain_decision(fusion, opinions, trade=trade_safe)
    decision = str(fusion.get("decision") or "HOLD").upper()
    direction = _dir_map(fusion.get("direction") or decision)
    conf = float(fusion.get("confidence_raw") or 0)
    conf = max(conf, abs(float(fusion.get("probability_buy") or 0.5) - 0.5) * 2.0)
    passed = decision in ("BUY", "SELL") and str((conflict or {}).get("level") or "") != "HIGH"
    return {
        "ok": True,
        "module": "brain",
        "pass": passed,
        "brain_decision": decision,
        "direction": direction,
        "confidence": round(min(1.0, conf), 4),
        "expected_ev": fusion.get("expected_ev"),
        "expected_pf": fusion.get("expected_pf"),
        "conflict": (conflict or {}).get("level"),
        "similarity_pct": round(min(99.0, conf * 100.0), 2),
        "reason": (
            "Brain NO_TRADE"
            if decision == "NO_TRADE"
            else (f"Brain {decision}" if passed else "Brain HOLD/conflict")
        ),
        "explanation": explanation,
        "fusion": fusion,
        "opinions": opinions,
    }


def hist_blend(scores: list[dict[str, Any]]) -> dict[str, Any]:
    wrs, pfs, evs = [], [], []
    for s in scores:
        if s.get("historical_wr") is not None:
            wrs.append(float(s["historical_wr"]))
        pf = s.get("historical_pf")
        if isinstance(pf, (int, float)):
            pfs.append(float(pf))
        if s.get("historical_ev") is not None:
            evs.append(float(s["historical_ev"]))
    return {
        "historical_wr": round(float(np.mean(wrs)), 2) if wrs else None,
        "historical_pf": round(float(np.mean(pfs)), 4) if pfs else None,
        "historical_ev": round(float(np.mean(evs)), 4) if evs else None,
    }


__all__ = [
    "hist_blend",
    "score_brain",
    "score_causality",
    "score_dna",
    "score_edge",
    "score_fingerprint",
    "score_replay",
    "score_rules",
    "score_timeline",
]
