"""Atomic predicates + 2/3/4-feature edge combination search."""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v1.dataset import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.metrics import (
    apply_fdr,
    evaluate_mask,
    pnl_of,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

Predicate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class EdgeAtom:
    id: str
    feature: str
    label: str
    pred: Predicate


def _rsi_atoms() -> list[EdgeAtom]:
    out: list[EdgeAtom] = []
    for name, lo, hi in (
        ("rsi_os", 0.0, 30.0),
        ("rsi_low", 30.0, 45.0),
        ("rsi_mid", 45.0, 55.0),
        ("rsi_high", 55.0, 70.0),
        ("rsi_ob", 70.0, 100.0),
    ):
        out.append(EdgeAtom(
            id=name,
            feature="rsi",
            label=f"rsi in [{lo},{hi})",
            pred=lambda r, lo=lo, hi=hi: (
                _safe_float(r.get("rsi")) is not None and lo <= float(r["rsi"]) < hi
            ),
        ))
    return out


def generate_atoms(rows: list[dict[str, Any]]) -> list[EdgeAtom]:
    atoms: list[EdgeAtom] = []
    atoms.extend(_rsi_atoms())

    for feat in NUMERIC_FEATURES:
        if feat == "rsi":
            continue
        filled = [float(v) for v in (_safe_float(r.get(feat)) for r in rows) if v is not None]
        if len(filled) < 8:
            continue
        if len({round(v, 6) for v in filled}) < 3:
            continue
        med = float(np.median(filled))
        q25, q75 = float(np.quantile(filled, 0.25)), float(np.quantile(filled, 0.75))
        for label, pred in (
            (f"{feat}<=median({med:.4g})",
             lambda r, f=feat, t=med: _safe_float(r.get(f)) is not None and float(r[f]) <= t),
            (f"{feat}>median({med:.4g})",
             lambda r, f=feat, t=med: _safe_float(r.get(f)) is not None and float(r[f]) > t),
            (f"{feat}<=q25({q25:.4g})",
             lambda r, f=feat, t=q25: _safe_float(r.get(f)) is not None and float(r[f]) <= t),
            (f"{feat}>=q75({q75:.4g})",
             lambda r, f=feat, t=q75: _safe_float(r.get(f)) is not None and float(r[f]) >= t),
        ):
            atoms.append(EdgeAtom(
                id=f"{feat}_{label}",
                feature=feat,
                label=label,
                pred=pred,
            ))

    for feat in CATEGORICAL_FEATURES:
        counts: dict[str, int] = {}
        for r in rows:
            v = str(r.get(feat) or "").strip()
            if not v or v.upper() in ("NULL", "NONE", ""):
                continue
            counts[v] = counts.get(v, 0) + 1
        for val, n in sorted(counts.items(), key=lambda x: -x[1])[:10]:
            if n < 3:
                continue
            atoms.append(EdgeAtom(
                id=f"{feat}_eq_{val}",
                feature=feat if feat != "weekday" else "time",
                label=f"{feat}=={val}",
                pred=lambda r, f=feat, v=val: str(r.get(f) or "") == v,
            ))
    # hour time bins
    hour_bins = [(0, 6), (6, 12), (12, 18), (18, 24)]
    for lo, hi in hour_bins:
        atoms.append(EdgeAtom(
            id=f"hour_{lo}_{hi}",
            feature="time",
            label=f"hour in [{lo},{hi})",
            pred=lambda r, lo=lo, hi=hi: (
                _safe_float(r.get("hour")) is not None and lo <= float(r["hour"]) < hi
            ),
        ))
    return atoms


def _and_pred(*preds: Predicate) -> Predicate:
    def _p(r: dict[str, Any], ps: tuple[Predicate, ...] = preds) -> bool:
        return all(p(r) for p in ps)

    return _p


def generate_combos(
    atoms: list[EdgeAtom],
    *,
    max_total: int | None = None,
) -> list[tuple[str, tuple[str, ...], str, Predicate]]:
    """Yield (id, features, label, pred) for 2/3/4-feature AND combos."""
    max_total = int(max_total or os.environ.get("EDGE_DISCOVERY_MAX_RULES", "6000"))
    by_feat: dict[str, list[EdgeAtom]] = {}
    for a in atoms:
        by_feat.setdefault(a.feature, []).append(a)
    # Cap atoms per feature to keep 4-way search tractable.
    for f in list(by_feat):
        by_feat[f] = by_feat[f][:5]
    feats = sorted(by_feat.keys())
    out: list[tuple[str, tuple[str, ...], str, Predicate]] = []

    def add(combo: tuple[EdgeAtom, ...]) -> None:
        feats_t = tuple(a.feature for a in combo)
        if len(set(feats_t)) != len(feats_t):
            return
        label = " AND ".join(f"({a.label})" for a in combo)
        rid = f"{len(combo)}|" + "|".join(a.id for a in combo)
        out.append((rid, feats_t, label, _and_pred(*(a.pred for a in combo))))

    # 2-feature
    for fa, fb in itertools.combinations(feats, 2):
        for ra in by_feat[fa]:
            for rb in by_feat[fb]:
                add((ra, rb))
                if len(out) >= max_total:
                    return out
    # 3-feature
    for fa, fb, fc in itertools.combinations(feats, 3):
        for ra in by_feat[fa][:3]:
            for rb in by_feat[fb][:3]:
                for rc in by_feat[fc][:3]:
                    add((ra, rb, rc))
                    if len(out) >= max_total:
                        return out
    # 4-feature
    for combo_feats in itertools.combinations(feats, 4):
        pools = [by_feat[f][:2] for f in combo_feats]
        for atoms4 in itertools.product(*pools):
            add(tuple(atoms4))
            if len(out) >= max_total:
                return out
    return out


def _mask_for(rows: list[dict[str, Any]], pred: Predicate) -> list[bool]:
    return [bool(pred(r)) for r in rows]


def _overlap(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def mine_edges(
    rows: list[dict[str, Any]],
    *,
    min_n: int | None = None,
    research_min_n: int = 100,
) -> dict[str, Any]:
    """Search 2/3/4-feature edges; filter; dedupe by overlap; score."""
    n_rows = len(rows)
    baseline_pnls = pnl_of(rows)
    base_m = evaluate_mask(rows, [True] * n_rows, baseline_pnls, seed=1, n_boot=80, n_perm=40)
    baseline_pf = float(base_m.get("pf") or 0.0)
    if base_m.get("pf_inf"):
        baseline_pf = 10.0
    baseline_ev = float(base_m.get("expectancy") or 0.0)

    # Adaptive sample floor for small local books; research target remains 100.
    if min_n is None:
        if n_rows < 200:
            min_n = max(8, n_rows // 5)
        else:
            min_n = research_min_n
    min_n = int(min_n)

    atoms = generate_atoms(rows)
    combos = generate_combos(atoms)
    raw: list[dict[str, Any]] = []
    for i, (rid, feats, label, pred) in enumerate(combos):
        mask = _mask_for(rows, pred)
        n = sum(1 for m in mask if m)
        if n < min_n:
            continue
        metrics = evaluate_mask(rows, mask, baseline_pnls, seed=i % 10_000)
        pf = float(metrics.get("pf") or 0.0)
        if metrics.get("pf_inf"):
            pf = 10.0
        ev = float(metrics.get("expectancy") or 0.0)
        p = metrics.get("p_value")
        # Hard filters (research brief)
        if pf < baseline_pf:
            continue
        if ev <= baseline_ev:
            continue
        if p is not None and float(p) > 0.05:
            continue
        if not metrics.get("stable"):
            # soft: keep for TEST status later if edge_score high
            pass
        ids = {j for j, m in enumerate(mask) if m}
        raw.append({
            "id": rid,
            "rule": label,
            "features": list(feats),
            "k": len(feats),
            "trade_ids": ids,
            **metrics,
            "pf_num": pf,
            "baseline_pf": baseline_pf,
            "baseline_ev": baseline_ev,
        })

    apply_fdr(raw, alpha=0.05)

    # Sort by edge_score then dedupe overlap > 80%
    raw.sort(key=lambda c: (-float(c.get("edge_score") or 0), -int(c.get("n") or 0)))
    kept: list[dict[str, Any]] = []
    kept_sets: list[set[int]] = []
    for c in raw:
        s = c["trade_ids"]
        if any(_overlap(s, prev) > 0.80 for prev in kept_sets):
            c["reject_reason"] = "overlap>80%"
            continue
        # Instability hard reject for READY path
        kept.append(c)
        kept_sets.append(s)

    # Assign status
    for c in kept:
        n = int(c.get("n") or 0)
        p_ok = c.get("p_value") is None or float(c["p_value"]) <= 0.05
        fdr_ok = bool(c.get("fdr_ok"))
        stable = bool(c.get("stable"))
        wf_ok = bool(c.get("wf_ok"))
        oos_ok = bool(c.get("oos_ok"))
        research_n_ok = n >= research_min_n
        if (
            research_n_ok
            and p_ok
            and fdr_ok
            and stable
            and wf_ok
            and oos_ok
            and float(c.get("pf_num") or 0) >= baseline_pf
            and float(c.get("expectancy") or 0) > baseline_ev
        ):
            c["status"] = "READY"
        elif (
            n >= min_n
            and p_ok
            and float(c.get("pf_num") or 0) >= baseline_pf
            and float(c.get("expectancy") or 0) > baseline_ev
        ):
            c["status"] = "TEST"
        else:
            c["status"] = "REJECT"

    # Drop REJECT from scoreboard but keep count
    scoreboard = [c for c in kept if c.get("status") in ("READY", "TEST")]
    scoreboard.sort(key=lambda c: (-float(c.get("edge_score") or 0), -int(c.get("n") or 0)))

    # Serialize trade_ids away
    for c in scoreboard:
        c.pop("trade_ids", None)

    return {
        "n_rows": n_rows,
        "min_n_applied": min_n,
        "research_min_n": research_min_n,
        "n_atoms": len(atoms),
        "n_combos_tested": len(combos),
        "n_raw_pass_metrics": len(raw),
        "n_after_overlap": len(kept),
        "baseline": {
            "n": base_m.get("n"),
            "pf": base_m.get("pf"),
            "expectancy": base_m.get("expectancy"),
            "winrate": base_m.get("winrate"),
            "sharpe": base_m.get("sharpe"),
        },
        "candidates": scoreboard[:100],
        "n_ready": sum(1 for c in scoreboard if c.get("status") == "READY"),
        "n_test": sum(1 for c in scoreboard if c.get("status") == "TEST"),
    }


__all__ = ["generate_atoms", "generate_combos", "mine_edges"]
