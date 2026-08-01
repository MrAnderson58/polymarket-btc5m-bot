"""Vectorized combinatorial edge mining (2–8 features) for Edge Discovery V3."""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v3.dataset import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    matrix_from_rows,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.metrics import (
    apply_fdr,
    cheap_screen,
    full_validate,
)


@dataclass
class Atom:
    id: str
    feature: str
    label: str
    mask: np.ndarray  # bool, length n


def _quantile_atoms(feat: str, arr: np.ndarray) -> list[Atom]:
    valid = np.isfinite(arr)
    if int(valid.sum()) < 8:
        return []
    filled = arr[valid]
    if len(np.unique(np.round(filled, 6))) < 3:
        return []
    med = float(np.median(filled))
    q25, q75 = float(np.quantile(filled, 0.25)), float(np.quantile(filled, 0.75))
    out: list[Atom] = []
    specs = (
        (f"{feat}<=median({med:.4g})", arr <= med),
        (f"{feat}>median({med:.4g})", arr > med),
        (f"{feat}<=q25({q25:.4g})", arr <= q25),
        (f"{feat}>=q75({q75:.4g})", arr >= q75),
    )
    for label, m in specs:
        mm = m & valid
        out.append(Atom(id=f"{feat}_{label}", feature=feat, label=label, mask=mm))
    return out


def _rsi_atoms(arr: np.ndarray) -> list[Atom]:
    valid = np.isfinite(arr)
    out: list[Atom] = []
    for name, lo, hi in (
        ("rsi_os", 0.0, 30.0),
        ("rsi_low", 30.0, 45.0),
        ("rsi_mid", 45.0, 55.0),
        ("rsi_high", 55.0, 70.0),
        ("rsi_ob", 70.0, 100.0),
    ):
        m = valid & (arr >= lo) & (arr < hi)
        out.append(Atom(id=name, feature="rsi", label=f"rsi in [{lo},{hi})", mask=m))
    return out


def generate_atoms(
    numeric: dict[str, np.ndarray],
    cats: dict[str, np.ndarray],
) -> list[Atom]:
    atoms: list[Atom] = []
    if "rsi" in numeric:
        atoms.extend(_rsi_atoms(numeric["rsi"]))
    for feat in NUMERIC_FEATURES:
        if feat == "rsi":
            continue
        if feat not in numeric:
            continue
        atoms.extend(_quantile_atoms(feat, numeric[feat]))
    for feat in CATEGORICAL_FEATURES:
        vals = cats.get(feat)
        if vals is None:
            continue
        # top values
        uniq, counts = np.unique(vals, return_counts=True)
        order = np.argsort(-counts)
        kept = 0
        for i in order:
            v = str(uniq[i])
            n = int(counts[i])
            if not v or v.upper() in ("NULL", "NONE", ""):
                continue
            if n < 3:
                continue
            atoms.append(Atom(
                id=f"{feat}_eq_{v}",
                feature=feat if feat != "weekday" else "time",
                label=f"{feat}=={v}",
                mask=(vals == v),
            ))
            kept += 1
            if kept >= 8:
                break
    # hour bins
    hour = numeric.get("hour")
    if hour is not None:
        valid = np.isfinite(hour)
        for lo, hi in ((0, 6), (6, 12), (12, 18), (18, 24)):
            atoms.append(Atom(
                id=f"hour_{lo}_{hi}",
                feature="time",
                label=f"hour in [{lo},{hi})",
                mask=valid & (hour >= lo) & (hour < hi),
            ))
    return atoms


def _atom_ev(pnls: np.ndarray, atom: Atom) -> float:
    n = int(atom.mask.sum())
    if n < 3:
        return -999.0
    return float(pnls[atom.mask].mean())


def mine_edges_v3(
    rows: list[dict[str, Any]],
    *,
    min_n: int | None = None,
    research_min_n: int = 30,
    max_full_validate: int | None = None,
    max_k: int = 8,
) -> dict[str, Any]:
    """
    Combinatorial search 2–8 features via vectorized masks.

    Counts every screened combo as tested (can reach millions via beam expansion).
    Full bootstrap/permutation only on top screened survivors.
    """
    n_rows = len(rows)
    if n_rows == 0:
        return {
            "n_rows": 0,
            "n_atoms": 0,
            "n_combos_tested": 0,
            "n_edges_tested": 0,
            "candidates": [],
            "interactions": [],
            "n_ready": 0,
            "n_test": 0,
            "baseline": {},
        }

    pnls, closed, numeric, cats = matrix_from_rows(rows)
    atoms = generate_atoms(numeric, cats)
    if min_n is None:
        if n_rows < 200:
            min_n = max(8, n_rows // 5)
        else:
            min_n = research_min_n
    min_n = int(min_n)
    max_full = int(
        max_full_validate
        or os.environ.get("EDGE_V3_MAX_FULL", "400")
    )
    beam = int(os.environ.get("EDGE_V3_BEAM", "80"))

    base_mask = np.ones(n_rows, dtype=bool)
    base = cheap_screen(pnls, base_mask) or {"n": n_rows, "expectancy": 0.0, "pf": 0.0}
    baseline_ev = float(base.get("expectancy") or 0.0)
    baseline_pf = float(base.get("pf") or 0.0) if base.get("pf") is not None else 10.0

    # Cap atoms per feature
    by_feat: dict[str, list[Atom]] = {}
    for a in atoms:
        by_feat.setdefault(a.feature, []).append(a)
    for f in list(by_feat):
        # keep highest |EV-baseline| atoms
        by_feat[f] = sorted(
            by_feat[f],
            key=lambda a: abs(_atom_ev(pnls, a) - baseline_ev),
            reverse=True,
        )[:8]
    feats = sorted(by_feat.keys())
    atom_index = {a.id: a for pool in by_feat.values() for a in pool}

    n_tested = 0
    screened: list[dict[str, Any]] = []
    max_random = int(os.environ.get("EDGE_V3_RANDOM_COMBOS", "150000"))
    if n_rows < 500:
        max_random = min(max_random, 8_000)
    elif n_rows < 5_000:
        max_random = min(max_random, 50_000)

    def consider(combo: tuple[Atom, ...]) -> dict[str, Any] | None:
        nonlocal n_tested
        feats_t = tuple(a.feature for a in combo)
        if len(set(feats_t)) != len(feats_t):
            return None
        n_tested += 1
        if len(combo) == 1:
            m = combo[0].mask
        else:
            m = np.logical_and.reduce([a.mask for a in combo])
        scr = cheap_screen(pnls, m)
        if scr is None or int(scr["n"]) < min_n:
            return None
        pf = float(scr["pf"]) if scr.get("pf") is not None else 10.0
        ev = float(scr["expectancy"] or 0.0)
        if pf < baseline_pf or ev <= baseline_ev:
            return None
        row = {
            "id": f"{len(combo)}|" + "|".join(a.id for a in combo),
            "rule": " AND ".join(f"({a.label})" for a in combo),
            "features": list(feats_t),
            "k": len(combo),
            "atom_ids": [a.id for a in combo],
            "mask": m,
            **scr,
            "pf_num": pf,
            "baseline_pf": baseline_pf,
            "baseline_ev": baseline_ev,
        }
        screened.append(row)
        return row

    # --- Singles (for interaction baseline) ---
    single_ev: dict[str, float] = {}
    for a in atom_index.values():
        n_tested += 1
        scr = cheap_screen(pnls, a.mask)
        if scr and int(scr["n"]) >= max(3, min_n // 2):
            single_ev[a.id] = float(scr["expectancy"] or 0.0)

    # --- 2-way exhaustive ---
    for fa, fb in itertools.combinations(feats, 2):
        for ra in by_feat[fa]:
            for rb in by_feat[fb]:
                consider((ra, rb))

    # --- 3-way pruned ---
    for fa, fb, fc in itertools.combinations(feats, 3):
        for ra in by_feat[fa][:4]:
            for rb in by_feat[fb][:4]:
                for rc in by_feat[fc][:4]:
                    consider((ra, rb, rc))

    # --- Dense random higher-order samples (toward million-scale search) ---
    rng = np.random.default_rng(42)
    all_atoms = [a for pool in by_feat.values() for a in pool]
    if len(feats) >= 4 and len(all_atoms) >= 8:
        for _ in range(max_random):
            k = int(rng.integers(4, max_k + 1))
            chosen_feats = list(rng.choice(feats, size=min(k, len(feats)), replace=False))
            combo_atoms: list[Atom] = []
            ok = True
            for f in chosen_feats:
                pool = by_feat[f]
                if not pool:
                    ok = False
                    break
                combo_atoms.append(pool[int(rng.integers(0, len(pool)))])
            if ok and len(combo_atoms) >= 2:
                consider(tuple(combo_atoms))

    # Sort screened pairs/triples for beam
    screened.sort(key=lambda c: (-float(c.get("expectancy") or 0), -int(c.get("n") or 0)))
    beam_seeds = screened[:beam]

    # --- Beam expand to k=4..max_k ---
    frontier = beam_seeds
    for _k in range(4, max_k + 1):
        nxt: list[dict[str, Any]] = []
        for seed in frontier[:beam]:
            used = set(seed["features"])
            seed_atoms = [atom_index[i] for i in seed["atom_ids"] if i in atom_index]
            if len(seed_atoms) != len(seed["atom_ids"]):
                continue
            for f in feats:
                if f in used:
                    continue
                for a in by_feat[f][:2]:
                    row = consider(tuple(seed_atoms + [a]))
                    if row is not None:
                        nxt.append(row)
        nxt.sort(key=lambda c: (-float(c.get("expectancy") or 0), -int(c.get("n") or 0)))
        frontier = nxt[:beam]
        if not frontier:
            break

    # Deduplicate by rule id
    uniq: dict[str, dict[str, Any]] = {}
    for c in screened:
        uniq[c["id"]] = c
    screened = list(uniq.values())
    screened.sort(key=lambda c: (-float(c.get("expectancy") or 0), -float(c.get("pf_num") or 0)))

    # Interaction lift vs singles
    interactions: list[dict[str, Any]] = []
    for c in screened[:500]:
        ids = c.get("atom_ids") or []
        if len(ids) < 2:
            continue
        alone = [single_ev.get(i) for i in ids]
        if any(x is None for x in alone):
            continue
        alone_max = max(float(x) for x in alone)  # type: ignore[arg-type]
        alone_sum = sum(float(x) for x in alone)  # type: ignore[arg-type]
        joint = float(c.get("expectancy") or 0.0)
        lift = joint - alone_max
        if lift >= 0.5 or (joint > 0 and alone_max <= 0 and lift > 0.2):
            interactions.append({
                "rule": c["rule"],
                "k": c["k"],
                "joint_ev": round(joint, 4),
                "alone_evs": [round(float(x), 4) for x in alone],  # type: ignore[arg-type]
                "alone_max": round(alone_max, 4),
                "alone_sum": round(alone_sum, 4),
                "lift_vs_best_alone": round(lift, 4),
                "n": c["n"],
                "pf": c.get("pf"),
            })
    interactions.sort(key=lambda x: -float(x.get("lift_vs_best_alone") or 0))

    # Full validation on top screened
    top = screened[:max_full]
    validated: list[dict[str, Any]] = []
    for i, c in enumerate(top):
        mask = c.pop("mask")
        metrics = full_validate(pnls, mask, closed, numeric, seed=i % 10_000)
        # Hard rejects
        reject = None
        if int(metrics.get("n") or 0) < min_n:
            reject = "sample_too_small"
        elif not metrics.get("stable"):
            reject = "unstable"
        elif metrics.get("p_value") is not None and float(metrics["p_value"]) > 0.10:
            reject = "overfit_p"
        elif not metrics.get("wf_ok") and not metrics.get("rolling_ok"):
            reject = "inconsistent_windows"
        elif metrics.get("prob_edge_gt_0") is not None and float(metrics["prob_edge_gt_0"]) < 0.55:
            reject = "weak_posterior"
        row = {
            **{k: v for k, v in c.items() if k != "mask"},
            **metrics,
            "reject_reason": reject,
        }
        validated.append(row)

    apply_fdr(validated, alpha=0.05)

    survivors: list[dict[str, Any]] = []
    for c in validated:
        n = int(c.get("n") or 0)
        p_ok = c.get("p_value") is None or float(c["p_value"]) <= 0.05
        fdr_ok = bool(c.get("fdr_ok"))
        survive_hard = (
            c.get("reject_reason") is None
            and bool(c.get("stable"))
            and bool(c.get("oos_ok") or c.get("wf_ok"))
            and bool(c.get("regime_ok", True))
            and bool(c.get("rolling_ok") or c.get("expanding_ok"))
            and p_ok
            and float(c.get("prob_edge_gt_0") or 0) >= 0.55
        )
        research_n_ok = n >= research_min_n
        if survive_hard and research_n_ok and fdr_ok:
            c["status"] = "READY"
            survivors.append(c)
        elif c.get("reject_reason") is None and p_ok and float(c.get("expectancy") or 0) > baseline_ev:
            c["status"] = "TEST"
            survivors.append(c)
        else:
            c["status"] = "REJECT"

    survivors.sort(key=lambda c: (-float(c.get("quality_score") or 0), -int(c.get("n") or 0)))
    # Drop internal
    for c in survivors:
        c.pop("atom_ids", None)
        c.pop("mask", None)

    return {
        "n_rows": n_rows,
        "min_n_applied": min_n,
        "research_min_n": research_min_n,
        "n_atoms": len(atoms),
        "n_features": len(feats),
        "n_combos_tested": n_tested,
        "n_edges_tested": n_tested,
        "n_search_space_estimate": int(
            sum(
                __import__("math").comb(max(len(feats), 1), k) * (8 ** k)
                for k in range(2, min(max_k, len(feats)) + 1)
            )
            if len(feats) >= 2
            else 0
        ),
        "n_screened": len(screened),
        "n_full_validated": len(validated),
        "baseline": {
            "n": base.get("n"),
            "pf": base.get("pf"),
            "expectancy": round(baseline_ev, 4),
            "winrate": base.get("winrate"),
        },
        "candidates": survivors[:100],
        "interactions": interactions[:50],
        "n_ready": sum(1 for c in survivors if c.get("status") == "READY"),
        "n_test": sum(1 for c in survivors if c.get("status") == "TEST"),
        "n_surviving": len(survivors),
        "top20": survivors[:20],
    }


__all__ = ["Atom", "generate_atoms", "mine_edges_v3"]
