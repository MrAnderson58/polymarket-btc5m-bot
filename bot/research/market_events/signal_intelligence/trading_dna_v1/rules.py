"""Minimal profitable rule set + forbidden setups."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    pf_sort_key,
    trade_metrics,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import DNARule


def find_minimal_rules(
    rows: list[dict[str, Any]],
    atomic_rules: list[DNARule],
    masks: dict[str, np.ndarray],
    *,
    min_n: int = 80,
    max_rules: int = 5,
    target_pf: float = 1.8,
) -> list[dict[str, Any]]:
    """Greedy forward selection for compact high-PF rule sets."""
    n = len(rows)
    if n == 0:
        return []

    pool: list[tuple[DNARule, dict[str, Any]]] = []
    for rule in atomic_rules:
        m = masks.get(rule.id)
        if m is None:
            continue
        if int(m.sum()) < min_n:
            continue
        met = trade_metrics([float(rows[i]["pnl"]) for i in np.nonzero(m)[0]])
        pf = met.get("pf")
        pf_v = 99.0 if (pf is None and met.get("pf_inf")) else float(pf or 0.0)
        if pf_v < 1.05 and float(met.get("ev") or 0) <= 0:
            continue
        pool.append((rule, met))
    pool.sort(key=lambda x: pf_sort_key(x[1]), reverse=True)
    pool = pool[:30]

    results: list[dict[str, Any]] = []
    for rule, met in pool[:12]:
        if int(met["n"]) < min_n:
            continue
        pf = met.get("pf")
        pf_v = 99.0 if (pf is None and met.get("pf_inf")) else float(pf or 0)
        if pf_v >= 1.3:
            results.append({
                "rules": [rule.label],
                "n": met["n"],
                "pf": met["pf"],
                "ev": met["ev"],
                "wr": met["wr"],
                "confidence": met["confidence"],
                "size": 1,
            })

    selected: list[DNARule] = []
    current = np.ones(n, dtype=bool)
    used_feats: set[str] = set()
    for _ in range(max_rules):
        best = None
        best_score = -1e18
        best_met = None
        best_mask = None
        for rule, _ in pool:
            if rule in selected:
                continue
            if rule.features[0] in used_feats:
                continue
            cand = current & masks[rule.id]
            nn = int(cand.sum())
            if nn < min_n:
                continue
            met = trade_metrics([float(rows[i]["pnl"]) for i in np.nonzero(cand)[0]])
            pf = met.get("pf")
            pf_v = 99.0 if (pf is None and met.get("pf_inf")) else float(pf or 0)
            score = pf_v * np.log1p(nn) * (1.0 if float(met.get("ev") or 0) > 0 else 0.2)
            if score > best_score:
                best_score = score
                best = rule
                best_met = met
                best_mask = cand
        if best is None or best_met is None or best_mask is None:
            break
        selected.append(best)
        used_feats.add(best.features[0])
        current = best_mask
        results.append({
            "rules": [r.label for r in selected],
            "n": best_met["n"],
            "pf": best_met["pf"],
            "ev": best_met["ev"],
            "wr": best_met["wr"],
            "confidence": best_met["confidence"],
            "size": len(selected),
        })
        pf = best_met.get("pf")
        pf_v = 99.0 if (pf is None and best_met.get("pf_inf")) else float(pf or 0)
        if pf_v >= target_pf and len(selected) >= 2:
            break

    uniq: dict[tuple[str, ...], dict[str, Any]] = {}
    for r in results:
        key = tuple(r["rules"])
        prev = uniq.get(key)
        if prev is None or pf_sort_key(r) > pf_sort_key(prev):
            uniq[key] = r
    out = list(uniq.values())
    out.sort(key=pf_sort_key, reverse=True)
    return out[:20]


def find_forbidden(
    losing_setups: list[dict[str, Any]],
    *,
    max_pf: float = 0.70,
    min_n: int = 40,
    limit: int = 30,
) -> list[dict[str, Any]]:
    out = []
    for s in losing_setups:
        pf = s.get("pf")
        if pf is None:
            continue
        if float(pf) > max_pf:
            continue
        if int(s.get("n") or 0) < min_n:
            continue
        if float(s.get("ev") or 0) >= 0:
            continue
        if int(s.get("n_losses") or 0) < 5:
            continue
        out.append({
            "setup": s.get("setup"),
            "n": s.get("n"),
            "pf": s.get("pf"),
            "ev": s.get("ev"),
            "wr": s.get("wr"),
            "action": "BLOCK",
            "confidence": s.get("confidence"),
        })
    out.sort(key=lambda r: (float(r.get("pf") or 0), float(r.get("ev") or 0)))
    return out[:limit]


__all__ = ["find_forbidden", "find_minimal_rules"]
