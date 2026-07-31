"""Feature binning + combination mining for Alpha Engine V1."""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.dataset import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
)
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    bootstrap_expectancy_ci,
    effective_pf,
    permutation_pvalue,
    pnl_list,
    trade_metrics,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

Predicate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class AlphaRule:
    id: str
    feature: str
    op: str
    threshold: float | str | None
    label: str
    features: tuple[str, ...]
    pred: Predicate


def _fixed_rsi_bins() -> list[tuple[str, float, float]]:
    return [
        ("rsi_os", 0.0, 30.0),
        ("rsi_low", 30.0, 45.0),
        ("rsi_mid", 45.0, 55.0),
        ("rsi_high", 55.0, 70.0),
        ("rsi_ob", 70.0, 100.0),
    ]


def generate_atomic_rules(rows: list[dict[str, Any]]) -> list[AlphaRule]:
    """Build single-feature bin / threshold rules."""
    rules: list[AlphaRule] = []

    # RSI fixed bins
    for name, lo, hi in _fixed_rsi_bins():
        rules.append(AlphaRule(
            id=name,
            feature="rsi",
            op="between",
            threshold=lo,
            label=f"rsi in [{lo},{hi})",
            features=("rsi",),
            pred=lambda r, lo=lo, hi=hi: (
                (_safe_float(r.get("rsi")) is not None)
                and lo <= float(r["rsi"]) < hi
            ),
        ))

    # Numeric quantile thresholds (low / high halves and quartiles)
    for feat in NUMERIC_FEATURES:
        if feat == "rsi":
            continue
        vals = [_safe_float(r.get(feat)) for r in rows]
        filled = [float(v) for v in vals if v is not None]
        if len(filled) < 20:
            continue
        # skip near-constant
        if len(set(round(v, 8) for v in filled)) < 4:
            continue
        med = float(np.median(filled))
        q25, q75 = float(np.quantile(filled, 0.25)), float(np.quantile(filled, 0.75))
        for label, op, thr, pred in (
            (f"{feat}<=median({med:.4g})", "le", med,
             lambda r, f=feat, t=med: (_safe_float(r.get(f)) is not None and float(r[f]) <= t)),
            (f"{feat}>median({med:.4g})", "gt", med,
             lambda r, f=feat, t=med: (_safe_float(r.get(f)) is not None and float(r[f]) > t)),
            (f"{feat}<=q25({q25:.4g})", "le", q25,
             lambda r, f=feat, t=q25: (_safe_float(r.get(f)) is not None and float(r[f]) <= t)),
            (f"{feat}>=q75({q75:.4g})", "ge", q75,
             lambda r, f=feat, t=q75: (_safe_float(r.get(f)) is not None and float(r[f]) >= t)),
        ):
            rules.append(AlphaRule(
                id=f"{feat}_{op}_{thr}",
                feature=feat,
                op=op,
                threshold=thr,
                label=label,
                features=(feat,),
                pred=pred,
            ))

    # Categorical equals (top symbols / gates)
    for feat in CATEGORICAL_FEATURES:
        counts: dict[str, int] = {}
        for r in rows:
            v = str(r.get(feat) or "").strip()
            if not v or v.upper() == "NULL":
                continue
            counts[v] = counts.get(v, 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])[:12]
        for val, n in top:
            if n < 5:
                continue
            rules.append(AlphaRule(
                id=f"{feat}_eq_{val}",
                feature=feat,
                op="eq",
                threshold=val,
                label=f"{feat}=={val}",
                features=(feat,),
                pred=lambda r, f=feat, v=val: str(r.get(f) or "") == v,
            ))
    return rules


def combine_rules(atoms: list[AlphaRule], *, max_combo: int = 3, max_total: int = 8000) -> list[AlphaRule]:
    """AND-combine atomic rules into 2- and 3-feature alphas."""
    out = list(atoms)
    # Prefer diverse features for combos
    by_feat: dict[str, list[AlphaRule]] = {}
    for a in atoms:
        by_feat.setdefault(a.feature, []).append(a)
    feats = list(by_feat.keys())
    # 2-feature
    for fa, fb in itertools.combinations(feats, 2):
        for ra in by_feat[fa][:4]:
            for rb in by_feat[fb][:4]:
                label = f"({ra.label}) AND ({rb.label})"
                out.append(AlphaRule(
                    id=f"2|{ra.id}|{rb.id}",
                    feature=f"{fa}+{fb}",
                    op="and",
                    threshold=None,
                    label=label,
                    features=(fa, fb),
                    pred=lambda r, p1=ra.pred, p2=rb.pred: bool(p1(r) and p2(r)),
                ))
                if len(out) >= max_total:
                    return out
    if max_combo >= 3:
        for fa, fb, fc in itertools.combinations(feats, 3):
            for ra in by_feat[fa][:2]:
                for rb in by_feat[fb][:2]:
                    for rc in by_feat[fc][:2]:
                        label = f"({ra.label}) AND ({rb.label}) AND ({rc.label})"
                        out.append(AlphaRule(
                            id=f"3|{ra.id}|{rb.id}|{rc.id}",
                            feature=f"{fa}+{fb}+{fc}",
                            op="and",
                            threshold=None,
                            label=label,
                            features=(fa, fb, fc),
                            pred=lambda r, p1=ra.pred, p2=rb.pred, p3=rc.pred: bool(
                                p1(r) and p2(r) and p3(r)
                            ),
                        ))
                        if len(out) >= max_total:
                            return out
    return out


def _holdout_split(rows: list[dict[str, Any]], train_frac: float = 0.7) -> tuple[list, list]:
    ordered = sorted(rows, key=lambda r: int(r.get("closed_at") or r.get("created_at") or 0))
    n = len(ordered)
    cut = max(1, int(n * train_frac))
    return ordered[:cut], ordered[cut:]


def evaluate_rule(
    rows: list[dict[str, Any]],
    rule: AlphaRule,
    *,
    baseline_pnls: list[float],
    min_n: int,
    seed: int = 0,
) -> dict[str, Any] | None:
    matched = [r for r in rows if rule.pred(r)]
    if len(matched) < min_n:
        return None
    pnls = pnl_list(matched)
    metrics = trade_metrics(pnls)
    base = trade_metrics(baseline_pnls)
    n_boot = int(os.environ.get("ALPHA_ENGINE_N_BOOT", "200"))
    n_perm = int(os.environ.get("ALPHA_ENGINE_N_PERM", "150"))
    ci = bootstrap_expectancy_ci(pnls, n_boot=n_boot, seed=seed)
    pval = permutation_pvalue(pnls, baseline_pnls, n_perm=n_perm, seed=seed)

    # Overfit check: train vs holdout expectancy sign agreement
    train, test = _holdout_split(rows, 0.7)
    tr = [r for r in train if rule.pred(r)]
    te = [r for r in test if rule.pred(r)]
    overfit = False
    train_ev = test_ev = None
    if len(tr) >= max(5, min_n // 2) and len(te) >= max(3, min_n // 3):
        train_ev = trade_metrics(pnl_list(tr))["expectancy"]
        test_ev = trade_metrics(pnl_list(te))["expectancy"]
        if train_ev is not None and test_ev is not None:
            # reject if train strongly positive but test negative (or vice versa with large gap)
            if train_ev > 0 and test_ev < 0 and (train_ev - test_ev) > abs(train_ev) * 0.5:
                overfit = True
            if train_ev > 0.5 and test_ev is not None and test_ev < train_ev * 0.2:
                overfit = True

    exp_d = None
    if metrics["expectancy"] is not None and base["expectancy"] is not None:
        exp_d = round(float(metrics["expectancy"]) - float(base["expectancy"]), 4)
    pf_d = round(effective_pf(metrics) - effective_pf(base), 4)

    return {
        "id": rule.id,
        "label": rule.label,
        "features": list(rule.features),
        "n": metrics["n"],
        "winrate": metrics["winrate"],
        "expectancy": metrics["expectancy"],
        "pf": metrics["pf"] if not metrics.get("pf_inf") else "inf",
        "avg_pnl": metrics["avg_pnl"],
        "sharpe": metrics["sharpe"],
        "ci_ev": ci,
        "p_value": pval,
        "expectancy_delta": exp_d,
        "pf_delta": pf_d,
        "overfit": overfit,
        "train_ev": train_ev,
        "test_ev": test_ev,
        "coverage": round(100.0 * metrics["n"] / max(1, len(rows)), 2),
    }


def mine_alphas(
    rows: list[dict[str, Any]],
    *,
    min_n: int | None = None,
    max_rules: int | None = None,
) -> dict[str, Any]:
    min_n = int(min_n or os.environ.get("ALPHA_ENGINE_MIN_N", "8"))
    # For tiny books allow smaller n but flag
    if len(rows) < 100:
        min_n = min(min_n, max(5, len(rows) // 5))
    max_rules = int(max_rules or os.environ.get("ALPHA_ENGINE_MAX_RULES", "8000"))

    atoms = generate_atomic_rules(rows)
    rules = combine_rules(atoms, max_combo=3, max_total=max_rules)
    baseline_pnls = pnl_list(rows)
    baseline = trade_metrics(baseline_pnls)

    candidates: list[dict[str, Any]] = []
    for i, rule in enumerate(rules):
        ev = evaluate_rule(
            rows, rule, baseline_pnls=baseline_pnls, min_n=min_n, seed=i % 10_000,
        )
        if ev is None:
            continue
        if ev.get("overfit"):
            continue
        candidates.append(ev)

    return {
        "n_rows": len(rows),
        "n_atoms": len(atoms),
        "n_rules_tested": len(rules),
        "n_candidates_raw": len(candidates),
        "baseline": baseline,
        "candidates": candidates,
        "min_n": min_n,
    }


__all__ = ["AlphaRule", "combine_rules", "evaluate_rule", "generate_atomic_rules", "mine_alphas"]
