"""Studies 1–10 for Market Mathematics Research V1."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
    CATEGORICAL_FEATURES,
    GATE_FILTER_FEATURES,
    NUMERIC_FEATURES,
)
from bot.research.market_events.signal_intelligence.market_math_v1.metrics import (
    effective_pf,
    pnl_array,
    rich_metrics,
    significance_ok,
)

# Fixed RSI-style bins where applicable; others use quantiles / fixed grids.
_RSI_EDGES = [0, 20, 30, 40, 50, 60, 70, 80, 100]
_FEAR_EDGES = [0, 20, 40, 60, 80, 100]
_ADX_EDGES = [0, 15, 20, 25, 30, 40, 100]
_STOCH_EDGES = [0, 20, 40, 60, 80, 100]


def _edges_for(feat: str, vals: np.ndarray) -> list[float]:
    if feat == "rsi":
        return list(_RSI_EDGES)
    if feat in ("fear_greed", "fear"):
        return list(_FEAR_EDGES)
    if feat == "adx":
        return list(_ADX_EDGES)
    if feat in ("stoch_k", "stoch_d"):
        return list(_STOCH_EDGES)
    if vals.size < 10:
        return []
    qs = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    edges = sorted({float(np.quantile(vals, q)) for q in qs})
    # ensure strictly increasing
    out = [edges[0]]
    for e in edges[1:]:
        if e > out[-1] + 1e-12:
            out.append(e)
    return out if len(out) >= 3 else []


def _bucket_label(lo: float, hi: float, *, last: bool = False) -> str:
    if last:
        return f"{lo:g}-{hi:g}"
    return f"{lo:g}-{hi:g}"


def _assign_numeric_buckets(
    values: np.ndarray,
    edges: list[float],
) -> list[tuple[str, np.ndarray]]:
    """Return list of (label, boolean mask)."""
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        if last:
            mask = (values >= lo) & (values <= hi)
        else:
            mask = (values >= lo) & (values < hi)
        out.append((_bucket_label(lo, hi, last=last), mask))
    return out


def study_feature_buckets(
    rows: list[dict[str, Any]],
    *,
    min_n: int = 8,
    n_boot: int = 120,
    n_perm: int = 80,
) -> dict[str, Any]:
    """Study 1 — per-feature bucket tables."""
    baseline = pnl_array(rows)
    tables: dict[str, list[dict[str, Any]]] = {}

    for feat in NUMERIC_FEATURES:
        vals = np.asarray([_safe_float(r.get(feat)) for r in rows], dtype=float)
        filled_mask = ~np.isnan(vals)
        filled = vals[filled_mask]
        edges = _edges_for(feat, filled)
        buckets: list[dict[str, Any]] = []
        if not edges:
            tables[feat] = buckets
            continue
        # work on full-length array with nan
        for label, mask in _assign_numeric_buckets(vals, edges):
            idx = np.where(mask & filled_mask)[0]
            if idx.size < min_n:
                continue
            pnls = baseline[idx]
            m = rich_metrics(pnls, baseline=baseline, n_boot=n_boot, n_perm=n_perm, seed=hash(feat + label) % 10_000)
            buckets.append({"feature": feat, "bucket": label, **m})
        tables[feat] = buckets

    for feat in CATEGORICAL_FEATURES:
        buckets = []
        counts: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            key = str(r.get(feat) or "NULL").upper()
            counts.setdefault(key, []).append(i)
        for key, idxs in sorted(counts.items(), key=lambda kv: -len(kv[1])):
            if len(idxs) < min_n:
                continue
            pnls = baseline[np.asarray(idxs)]
            m = rich_metrics(pnls, baseline=baseline, n_boot=n_boot, n_perm=n_perm, seed=hash(feat + key) % 10_000)
            buckets.append({"feature": feat, "bucket": key, **m})
        tables[feat] = buckets

    return {"baseline": rich_metrics(baseline, n_boot=n_boot, seed=1), "features": tables}


def _quantile_halves(vals: np.ndarray) -> list[tuple[str, float, str]]:
    """Return [(label, threshold, op), ...] for le/gt median and q25/q75."""
    if vals.size < 10:
        return []
    med = float(np.median(vals))
    q25 = float(np.quantile(vals, 0.25))
    q75 = float(np.quantile(vals, 0.75))
    return [
        (f"<={med:g}", med, "le"),
        (f">{med:g}", med, "gt"),
        (f"<={q25:g}", q25, "le"),
        (f">={q75:g}", q75, "ge"),
    ]


def _mask_op(vals: np.ndarray, thr: float, op: str) -> np.ndarray:
    if op == "le":
        return vals <= thr
    if op == "gt":
        return vals > thr
    if op == "ge":
        return vals >= thr
    return np.zeros(vals.shape, dtype=bool)


def study_feature_pairs(
    rows: list[dict[str, Any]],
    *,
    min_n: int = 15,
    n_boot: int = 80,
    n_perm: int = 60,
    max_results: int = 2000,
) -> dict[str, Any]:
    """Study 2 — all numeric feature pairs × threshold combos."""
    baseline = pnl_array(rows)
    # Pre-extract arrays
    arrays: dict[str, np.ndarray] = {}
    halves: dict[str, list[tuple[str, float, str]]] = {}
    for feat in NUMERIC_FEATURES:
        vals = np.asarray([_safe_float(r.get(feat)) for r in rows], dtype=float)
        arrays[feat] = vals
        filled = vals[~np.isnan(vals)]
        halves[feat] = _quantile_halves(filled)

    results: list[dict[str, Any]] = []
    for fa, fb in itertools.combinations(NUMERIC_FEATURES, 2):
        ha, hb = halves.get(fa) or [], halves.get(fb) or []
        if not ha or not hb:
            continue
        va, vb = arrays[fa], arrays[fb]
        for la, ta, oa in ha[:4]:
            for lb, tb, ob in hb[:4]:
                mask = (~np.isnan(va)) & (~np.isnan(vb)) & _mask_op(va, ta, oa) & _mask_op(vb, tb, ob)
                idx = np.where(mask)[0]
                if idx.size < min_n:
                    continue
                m = rich_metrics(
                    baseline[idx], baseline=baseline, n_boot=n_boot, n_perm=n_perm,
                    seed=(hash(fa + fb + la + lb) % 10_000),
                )
                results.append({
                    "features": [fa, fb],
                    "rule": f"{fa}{la} AND {fb}{lb}",
                    "confidence": _confidence(m),
                    **m,
                })
    results.sort(key=lambda r: (-(r.get("expectancy") or -1e9), -(effective_pf(r)), -(r.get("n") or 0)))
    return {"n_tested": len(results), "pairs": results[:max_results]}


def study_feature_triples(
    rows: list[dict[str, Any]],
    *,
    min_n: int = 12,
    n_boot: int = 60,
    n_perm: int = 50,
    max_keep: int = 500,
) -> dict[str, Any]:
    """Study 3 — triples; keep statistically significant only."""
    baseline = pnl_array(rows)
    # Use a reduced core set for tractability on large corpora
    core = [
        "rsi", "atr_pct", "ema20_distance", "vwap_distance", "funding",
        "oi_delta", "fear_greed", "adx", "macd_hist", "trend", "stoch_k",
    ]
    core = [f for f in core if f in NUMERIC_FEATURES]
    arrays = {
        f: np.asarray([_safe_float(r.get(f)) for r in rows], dtype=float) for f in core
    }
    halves = {f: _quantile_halves(a[~np.isnan(a)])[:2] for f, a in arrays.items()}

    kept: list[dict[str, Any]] = []
    n_tested = 0
    for fa, fb, fc in itertools.combinations(core, 3):
        for la, ta, oa in halves.get(fa) or []:
            for lb, tb, ob in halves.get(fb) or []:
                for lc, tc, oc in halves.get(fc) or []:
                    n_tested += 1
                    va, vb, vc = arrays[fa], arrays[fb], arrays[fc]
                    mask = (
                        (~np.isnan(va)) & (~np.isnan(vb)) & (~np.isnan(vc))
                        & _mask_op(va, ta, oa) & _mask_op(vb, tb, ob) & _mask_op(vc, tc, oc)
                    )
                    idx = np.where(mask)[0]
                    if idx.size < min_n:
                        continue
                    m = rich_metrics(
                        baseline[idx], baseline=baseline, n_boot=n_boot, n_perm=n_perm,
                        seed=n_tested % 10_000,
                    )
                    if not significance_ok(m, min_n=min_n, alpha=0.15):
                        # also keep strong PF/EV even if p weak on tiny books
                        if not (
                            (m.get("expectancy") or 0) > 0
                            and effective_pf(m) >= 1.2
                            and (m.get("n") or 0) >= min_n
                        ):
                            continue
                    kept.append({
                        "features": [fa, fb, fc],
                        "rule": f"{fa}{la} AND {fb}{lb} AND {fc}{lc}",
                        "confidence": _confidence(m),
                        **m,
                    })
    kept.sort(key=lambda r: (-(r.get("expectancy") or -1e9), -(effective_pf(r))))
    return {"n_tested": n_tested, "n_significant": len(kept), "triples": kept[:max_keep]}


def _confidence(m: dict[str, Any]) -> float:
    """0..1 confidence from n, CI, p-value, PF."""
    n = int(m.get("n") or 0)
    n_term = min(1.0, math.log1p(n) / math.log1p(500))
    ci = m.get("ci95") or (None, None)
    ci_term = 0.0
    if ci[0] is not None and ci[1] is not None and ci[1] > ci[0]:
        width = ci[1] - ci[0]
        ev = abs(float(m.get("expectancy") or 0))
        ci_term = max(0.0, 1.0 - width / max(ev * 4, 1e-6))
        if ci[0] > 0:
            ci_term = min(1.0, ci_term + 0.2)
    p = m.get("p_value")
    p_term = max(0.0, 1.0 - float(p)) if p is not None else 0.35
    pf_term = min(1.0, effective_pf(m) / 3.0)
    return round(0.35 * n_term + 0.25 * ci_term + 0.25 * p_term + 0.15 * pf_term, 4)


def study_explainable_tree(
    rows: list[dict[str, Any]],
    *,
    min_n: int = 20,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Study 4 — explainable greedy EV tree (not sklearn)."""
    baseline = pnl_array(rows)
    base_ev = float(baseline.mean()) if baseline.size else 0.0
    feats = [f for f in (
        "rsi", "funding", "oi_delta", "atr_pct", "ema20_distance", "fear_greed",
        "adx", "trend", "macd_hist", "stoch_k", "vwap_distance",
    ) if f in NUMERIC_FEATURES]
    arrays = {f: np.asarray([_safe_float(r.get(f)) for r in rows], dtype=float) for f in feats}

    def best_split(mask: np.ndarray, depth: int) -> dict[str, Any] | None:
        idx = np.where(mask)[0]
        if idx.size < min_n * 2 or depth >= max_depth:
            return None
        best = None
        best_score = -1e18
        for feat in feats:
            vals = arrays[feat][idx]
            filled = vals[~np.isnan(vals)]
            if filled.size < min_n:
                continue
            for q in (0.25, 0.5, 0.75):
                thr = float(np.quantile(filled, q))
                left = mask & (~np.isnan(arrays[feat])) & (arrays[feat] <= thr)
                right = mask & (~np.isnan(arrays[feat])) & (arrays[feat] > thr)
                for side_name, side in (("le", left), ("gt", right)):
                    sidx = np.where(side)[0]
                    if sidx.size < min_n:
                        continue
                    ev = float(baseline[sidx].mean())
                    # prefer lifts over baseline with decent n
                    score = (ev - base_ev) * math.log1p(sidx.size)
                    if score > best_score and ev > base_ev:
                        best_score = score
                        m = rich_metrics(baseline[sidx], baseline=baseline, n_boot=60, n_perm=40, seed=depth * 17)
                        best = {
                            "feature": feat,
                            "op": "<=" if side_name == "le" else ">",
                            "threshold": thr,
                            "n": int(sidx.size),
                            "expectancy": m.get("expectancy"),
                            "profit_factor": m.get("profit_factor"),
                            "winrate": m.get("winrate"),
                            "mask": side,
                        }
        return best

    def build(mask: np.ndarray, depth: int, path: list[str]) -> dict[str, Any]:
        node_idx = np.where(mask)[0]
        node_m = rich_metrics(baseline[node_idx], baseline=baseline, n_boot=80, n_perm=50, seed=depth + 3)
        node = {
            "path": list(path),
            "n": int(node_idx.size),
            "expectancy": node_m.get("expectancy"),
            "profit_factor": node_m.get("profit_factor"),
            "winrate": node_m.get("winrate"),
            "split": None,
            "child": None,
        }
        split = best_split(mask, depth)
        if split is None:
            return node
        rule = f"{split['feature']}{split['op']}{split['threshold']:g}"
        node["split"] = {
            "feature": split["feature"],
            "op": split["op"],
            "threshold": split["threshold"],
            "rule": rule,
            "n": split["n"],
            "expectancy": split["expectancy"],
            "profit_factor": split["profit_factor"],
            "winrate": split["winrate"],
        }
        node["child"] = build(split["mask"], depth + 1, path + [rule])
        return node

    root_mask = np.ones(len(rows), dtype=bool)
    tree = build(root_mask, 0, [])
    # Flatten leaf paths with positive EV
    leaves: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("child") is None and node.get("path"):
            leaves.append({
                "path": node["path"],
                "n": node["n"],
                "expectancy": node["expectancy"],
                "profit_factor": node["profit_factor"],
                "winrate": node["winrate"],
            })
        elif node.get("child") is not None:
            walk(node["child"])
        if node.get("split") and (node["split"].get("expectancy") or 0) > 0:
            leaves.append({
                "path": (node.get("path") or []) + [node["split"]["rule"]],
                "n": node["split"]["n"],
                "expectancy": node["split"]["expectancy"],
                "profit_factor": node["split"]["profit_factor"],
                "winrate": node["split"]["winrate"],
            })

    walk(tree)
    # dedupe by path string
    uniq = {}
    for leaf in leaves:
        key = " AND ".join(leaf["path"])
        uniq[key] = {**leaf, "rule": key}
    ranked = sorted(uniq.values(), key=lambda x: (-(x.get("expectancy") or -1e9), -(x.get("n") or 0)))
    return {"tree": _strip_masks(tree), "leaves": ranked[:50]}


def _strip_masks(node: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in node.items() if k != "mask"}
    if out.get("child"):
        out["child"] = _strip_masks(out["child"])
    return out


def study_gate_contribution(
    rows: list[dict[str, Any]],
    *,
    min_n: int = 10,
) -> dict[str, Any]:
    """Study 5 — contribution of gate-like filters (useful / useless / harmful)."""
    baseline = pnl_array(rows)
    base = rich_metrics(baseline, n_boot=100, seed=2)
    contributions = []
    for feat in GATE_FILTER_FEATURES:
        vals = np.asarray([_safe_float(r.get(feat)) for r in rows], dtype=float)
        filled = vals[~np.isnan(vals)]
        if filled.size < min_n * 2:
            contributions.append({
                "filter": feat,
                "status": "USELESS",
                "reason": "insufficient_coverage",
                "baseline_pf": base.get("profit_factor"),
                "baseline_ev": base.get("expectancy"),
            })
            continue
        med = float(np.median(filled))
        # two sides
        low = baseline[np.where((~np.isnan(vals)) & (vals <= med))[0]]
        high = baseline[np.where((~np.isnan(vals)) & (vals > med))[0]]
        m_low = rich_metrics(low, baseline=baseline, n_boot=80, n_perm=50, seed=3)
        m_high = rich_metrics(high, baseline=baseline, n_boot=80, n_perm=50, seed=4)
        # best side = "with filter"; complement-ish = weaker side
        if (m_low.get("expectancy") or -1e9) >= (m_high.get("expectancy") or -1e9):
            good, bad, good_rule = m_low, m_high, f"{feat}<={med:g}"
        else:
            good, bad, good_rule = m_high, m_low, f"{feat}>{med:g}"
        delta_pf = effective_pf(good) - effective_pf(base)
        delta_ev = float(good.get("expectancy") or 0) - float(base.get("expectancy") or 0)
        if delta_ev > 0.05 and delta_pf > 0.05 and (good.get("n") or 0) >= min_n:
            status = "USEFUL"
        elif delta_ev < -0.05 or (bad.get("expectancy") or 0) > (good.get("expectancy") or 0) + 0.1:
            # if "filter" side is worse than opposite — harmful to keep as-is
            status = "HARMFUL" if delta_ev < 0 else "USELESS"
        else:
            status = "USELESS"
        # without filter ≈ baseline (all trades already passed gate)
        contributions.append({
            "filter": feat,
            "status": status,
            "good_rule": good_rule,
            "with_filter": {
                "n": good.get("n"),
                "pf": good.get("profit_factor"),
                "ev": good.get("expectancy"),
                "wr": good.get("winrate"),
            },
            "without_filter": {
                "n": base.get("n"),
                "pf": base.get("profit_factor"),
                "ev": base.get("expectancy"),
                "wr": base.get("winrate"),
            },
            "opposite_side": {
                "n": bad.get("n"),
                "pf": bad.get("profit_factor"),
                "ev": bad.get("expectancy"),
                "wr": bad.get("winrate"),
            },
            "delta_ev": round(delta_ev, 4),
            "delta_pf": round(delta_pf, 4),
        })
    return {"baseline": base, "filters": contributions}


def study_top_rules(
    pairs: dict[str, Any],
    triples: dict[str, Any],
    feature_tables: dict[str, Any],
    tree: dict[str, Any],
    *,
    top_n: int = 100,
) -> list[dict[str, Any]]:
    """Study 6 — top-100 rules ranked by PF, EV, CI, sample size."""
    candidates: list[dict[str, Any]] = []
    for feat, buckets in (feature_tables.get("features") or {}).items():
        for b in buckets:
            if (b.get("expectancy") or 0) <= 0:
                continue
            candidates.append({
                "rule": f"{feat} in [{b.get('bucket')}]",
                "source": "feature_bucket",
                "n": b.get("n"),
                "profit_factor": b.get("profit_factor"),
                "expectancy": b.get("expectancy"),
                "winrate": b.get("winrate"),
                "ci95": b.get("ci95"),
                "p_value": b.get("p_value"),
                "confidence": _confidence(b),
            })
    for p in pairs.get("pairs") or []:
        if (p.get("expectancy") or 0) <= 0:
            continue
        candidates.append({
            "rule": p.get("rule"),
            "source": "pair",
            "n": p.get("n"),
            "profit_factor": p.get("profit_factor"),
            "expectancy": p.get("expectancy"),
            "winrate": p.get("winrate"),
            "ci95": p.get("ci95"),
            "p_value": p.get("p_value"),
            "confidence": p.get("confidence"),
        })
    for t in triples.get("triples") or []:
        candidates.append({
            "rule": t.get("rule"),
            "source": "triple",
            "n": t.get("n"),
            "profit_factor": t.get("profit_factor"),
            "expectancy": t.get("expectancy"),
            "winrate": t.get("winrate"),
            "ci95": t.get("ci95"),
            "p_value": t.get("p_value"),
            "confidence": t.get("confidence"),
        })
    for leaf in tree.get("leaves") or []:
        if (leaf.get("expectancy") or 0) <= 0:
            continue
        candidates.append({
            "rule": leaf.get("rule"),
            "source": "tree",
            "n": leaf.get("n"),
            "profit_factor": leaf.get("profit_factor"),
            "expectancy": leaf.get("expectancy"),
            "winrate": leaf.get("winrate"),
            "ci95": None,
            "p_value": None,
            "confidence": _confidence(leaf),
        })

    def sort_key(r: dict[str, Any]) -> tuple:
        ci = r.get("ci95") or (None, None)
        ci_lo = ci[0] if ci and ci[0] is not None else -1e9
        return (
            -effective_pf(r),
            -(r.get("expectancy") or -1e9),
            -ci_lo,
            -(r.get("n") or 0),
        )

    candidates.sort(key=sort_key)
    # dedupe by rule text
    seen = set()
    out = []
    for c in candidates:
        key = str(c.get("rule"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= top_n:
            break
    return out


def study_symbol_rankings(rows: list[dict[str, Any]], *, min_n: int = 5) -> list[dict[str, Any]]:
    """Study 7 — per-symbol PF/EV/WR/Sharpe/Kelly/MFE/MAE + grade."""
    baseline = pnl_array(rows)
    by_sym: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_sym.setdefault(str(r.get("symbol") or "NULL").upper(), []).append(i)
    out = []
    for sym, idxs in sorted(by_sym.items(), key=lambda kv: -len(kv[1])):
        arr = baseline[np.asarray(idxs)]
        m = rich_metrics(arr, baseline=baseline, n_boot=80, n_perm=50, seed=hash(sym) % 10_000)
        mfes = [_safe_float(rows[i].get("mfe_pct")) for i in idxs]
        maes = [_safe_float(rows[i].get("mae_pct")) for i in idxs]
        mfes_f = [x for x in mfes if x is not None]
        maes_f = [x for x in maes if x is not None]
        pf = effective_pf(m)
        ev = float(m.get("expectancy") or 0)
        n = int(m.get("n") or 0)
        if n < min_n:
            grade = "REMOVE"
        elif pf >= 1.5 and ev > 0 and n >= max(min_n, 15):
            grade = "BEST"
        elif pf >= 1.15 and ev > 0:
            grade = "GOOD"
        elif pf < 0.85 or ev < -0.1:
            grade = "BAD"
        else:
            grade = "NEUTRAL"
        out.append({
            "symbol": sym,
            "n": n,
            "profit_factor": m.get("profit_factor"),
            "expectancy": m.get("expectancy"),
            "winrate": m.get("winrate"),
            "sharpe": m.get("sharpe"),
            "kelly": m.get("kelly"),
            "mean_mfe": round(float(np.mean(mfes_f)), 4) if mfes_f else None,
            "mean_mae": round(float(np.mean(maes_f)), 4) if maes_f else None,
            "grade": grade,
        })
    out.sort(key=lambda r: (
        {"BEST": 0, "GOOD": 1, "NEUTRAL": 2, "BAD": 3, "REMOVE": 4}.get(r["grade"], 9),
        -(r.get("expectancy") or -1e9),
    ))
    return out


def study_harmful_regimes(
    feature_tables: dict[str, Any],
    pairs: dict[str, Any],
    *,
    min_n: int = 15,
) -> dict[str, Any]:
    """Study 8 — states with PF<1 or EV<0 to block."""
    harmful = []
    for feat, buckets in (feature_tables.get("features") or {}).items():
        for b in buckets:
            if (b.get("n") or 0) < min_n:
                continue
            pf = effective_pf(b)
            ev = float(b.get("expectancy") or 0)
            if pf < 1.0 or ev < 0:
                harmful.append({
                    "rule": f"{feat} in [{b.get('bucket')}]",
                    "source": "feature_bucket",
                    "n": b.get("n"),
                    "profit_factor": b.get("profit_factor"),
                    "expectancy": ev,
                    "winrate": b.get("winrate"),
                    "action": "BLOCK",
                })
    for p in pairs.get("pairs") or []:
        if (p.get("n") or 0) < min_n:
            continue
        pf = effective_pf(p)
        ev = float(p.get("expectancy") or 0)
        if pf < 1.0 or ev < 0:
            harmful.append({
                "rule": p.get("rule"),
                "source": "pair",
                "n": p.get("n"),
                "profit_factor": p.get("profit_factor"),
                "expectancy": ev,
                "winrate": p.get("winrate"),
                "action": "BLOCK",
            })
    harmful.sort(key=lambda r: ((r.get("expectancy") or 0), effective_pf(r), -(r.get("n") or 0)))
    return {"n_harmful": len(harmful), "regimes": harmful[:200]}


def study_market_map(
    feature_tables: dict[str, Any],
    pairs: dict[str, Any],
    triples: dict[str, Any],
    top_rules: list[dict[str, Any]],
    harmful: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Study 9 — mathematical market map summary."""
    best_buckets = []
    worst_buckets = []
    for feat, buckets in (feature_tables.get("features") or {}).items():
        for b in buckets:
            item = {"feature": feat, "bucket": b.get("bucket"), "n": b.get("n"),
                    "ev": b.get("expectancy"), "pf": b.get("profit_factor")}
            if (b.get("expectancy") or 0) > 0 and effective_pf(b) >= 1.0:
                best_buckets.append(item)
            elif (b.get("expectancy") or 0) < 0 or effective_pf(b) < 1.0:
                worst_buckets.append(item)
    best_buckets.sort(key=lambda x: (-(x.get("ev") or -1e9), -effective_pf({"profit_factor": x.get("pf")})))
    worst_buckets.sort(key=lambda x: ((x.get("ev") or 0), effective_pf({"profit_factor": x.get("pf")})))

    useful = [f["filter"] for f in gate.get("filters") or [] if f.get("status") == "USEFUL"]
    useless = [f["filter"] for f in gate.get("filters") or [] if f.get("status") == "USELESS"]
    harmful_f = [f["filter"] for f in gate.get("filters") or [] if f.get("status") == "HARMFUL"]

    return {
        "best_regimes": best_buckets[:25],
        "worst_regimes": worst_buckets[:25],
        "best_combinations": (pairs.get("pairs") or [])[:25],
        "worst_combinations": (harmful.get("regimes") or [])[:25],
        "most_stable_features": useful,
        "most_useless_features": useless + harmful_f,
        "top_rules_preview": top_rules[:10],
        "significant_triples": len(triples.get("triples") or []),
    }


def study_recommendations(gate: dict[str, Any], harmful: dict[str, Any], top_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Study 10 — actionable recommendations (no auto Gate changes)."""
    can_remove = []
    can_strengthen = []
    can_weaken = []
    need_check = []
    for f in gate.get("filters") or []:
        name = f.get("filter")
        st = f.get("status")
        if st == "USELESS":
            can_remove.append({
                "filter": name,
                "reason": "no meaningful EV/PF lift vs baseline",
                "evidence": f,
            })
        elif st == "HARMFUL":
            can_weaken.append({
                "filter": name,
                "reason": "current side underperforms; consider relaxing or flipping",
                "evidence": f,
            })
        elif st == "USEFUL":
            can_strengthen.append({
                "filter": name,
                "reason": f"good_rule={f.get('good_rule')} delta_ev={f.get('delta_ev')}",
                "evidence": f,
            })
        else:
            need_check.append({"filter": name, "reason": "unclear", "evidence": f})
    # harmful regimes → strengthen blocks
    for h in (harmful.get("regimes") or [])[:30]:
        can_strengthen.append({
            "filter": "block_regime",
            "reason": f"BLOCK {h.get('rule')} (EV={h.get('expectancy')}, PF={h.get('profit_factor')})",
            "evidence": h,
        })
    for rule in top_rules[:15]:
        need_check.append({
            "filter": "candidate_rule",
            "reason": f"validate OOS: {rule.get('rule')}",
            "evidence": rule,
        })
    return {
        "can_remove": can_remove,
        "can_strengthen": can_strengthen,
        "can_weaken": can_weaken,
        "need_check": need_check,
        "note": "Recommendations only — Gate / Strategy / Paper / Optimizer unchanged.",
    }


__all__ = [
    "study_explainable_tree",
    "study_feature_buckets",
    "study_feature_pairs",
    "study_feature_triples",
    "study_gate_contribution",
    "study_harmful_regimes",
    "study_market_map",
    "study_recommendations",
    "study_symbol_rankings",
    "study_top_rules",
]
