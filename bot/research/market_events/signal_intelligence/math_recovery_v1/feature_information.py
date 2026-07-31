"""Parts 4–6 — Feature information, combinations, ML dataset QA."""

from __future__ import annotations

import itertools
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.feature_store import (
    PREDICTION_FEATURES,
    extract_sample,
    load_closed_trade_rows,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.feature_audit import (
    _mutual_info_binned,
    _pearson,
)

REPORT_DIR = BASE_DIR / "reports" / "research"


def _pf_ev_wr(pnls: list[float]) -> tuple[float | None, float | None, float | None, int]:
    if not pnls:
        return None, None, None, 0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw, gl = sum(wins), abs(sum(losses))
    if gl > 1e-12:
        pf: float | None = gw / gl
    elif gw > 0:
        pf = None  # inf marker → use large
    else:
        pf = 0.0
    ev = sum(pnls) / len(pnls)
    wr = 100.0 * len(wins) / len(pnls)
    return pf, ev, wr, len(pnls)


def _auc_mann_whitney(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if len(pos) < 2 or len(neg) < 2:
        return None
    # AUC = P(score_pos > score_neg) + 0.5 P(eq)
    gt = 0.0
    eq = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                gt += 1
            elif p == n:
                eq += 1
    return round((gt + 0.5 * eq) / (len(pos) * len(neg)), 4)


def _bootstrap_ci_ev(pnls: list[float], n_boot: int = 200, alpha: float = 0.05) -> tuple[float | None, float | None]:
    if len(pnls) < 5:
        return None, None
    rng = np.random.default_rng(42)
    arr = np.asarray(pnls, dtype=float)
    means = []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means.append(float(sample.mean()))
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return round(lo, 4), round(hi, 4)


def _perm_pvalue(group_a: list[float], group_b: list[float], n_perm: int = 200) -> float | None:
    if len(group_a) < 3 or len(group_b) < 3:
        return None
    obs = abs(float(np.mean(group_a)) - float(np.mean(group_b)))
    pooled = np.asarray(group_a + group_b, dtype=float)
    rng = np.random.default_rng(0)
    n_a = len(group_a)
    extreme = 0
    for _ in range(n_perm):
        rng.shuffle(pooled)
        d = abs(float(pooled[:n_a].mean()) - float(pooled[n_a:].mean()))
        if d >= obs - 1e-12:
            extreme += 1
    return round((extreme + 1) / (n_perm + 1), 4)


def _information_gain_binary(xs: list[float], labels: list[int]) -> float | None:
    """IG of median split on x vs binary label entropy."""
    n = len(xs)
    if n < 8 or n != len(labels):
        return None
    def H(labs: list[int]) -> float:
        if not labs:
            return 0.0
        p = sum(labs) / len(labs)
        if p <= 0 or p >= 1:
            return 0.0
        return float(-(p * math.log2(p) + (1 - p) * math.log2(1 - p)))
    med = float(np.median(xs))
    left = [y for x, y in zip(xs, labels) if x <= med]
    right = [y for x, y in zip(xs, labels) if x > med]
    if not left or not right:
        return 0.0
    parent = H(labels)
    child = (len(left) / n) * H(left) + (len(right) / n) * H(right)
    return round(parent - child, 4)


def _single_feature_edge(xs: list[float], pnls: list[float]) -> dict[str, Any]:
    med = float(np.median(xs))
    hi = [p for x, p in zip(xs, pnls) if x > med]
    lo = [p for x, p in zip(xs, pnls) if x <= med]
    # pick better expectancy side
    _, ev_hi, _, n_hi = _pf_ev_wr(hi)
    _, ev_lo, _, n_lo = _pf_ev_wr(lo)
    if (ev_hi or -1e9) >= (ev_lo or -1e9):
        side, kept = "high", hi
    else:
        side, kept = "low", lo
    pf, ev, wr, n = _pf_ev_wr(kept)
    ci = _bootstrap_ci_ev(kept)
    other = lo if side == "high" else hi
    pval = _perm_pvalue(kept, other)
    return {
        "side": side,
        "threshold_median": med,
        "n": n,
        "ev": None if ev is None else round(ev, 4),
        "pf": None if pf is None else round(pf, 4),
        "wr": None if wr is None else round(wr, 2),
        "ci_ev": ci,
        "p_value": pval,
    }


def _matrix_from_samples(samples: list[dict[str, Any]], features: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rows = []
    ys = []
    keep_feat = []
    # first pass: which features have enough non-null variance
    for f in features:
        vals = [_safe_float(s.get(f)) for s in samples]
        filled = [v for v in vals if v is not None]
        if len(filled) < max(8, int(0.3 * len(samples))):
            continue
        if len(set(round(v, 8) for v in filled)) <= 1:
            continue
        keep_feat.append(f)
    for s in samples:
        pnl = _safe_float(s.get("pnl"))
        if pnl is None:
            continue
        vec = []
        ok = True
        for f in keep_feat:
            v = _safe_float(s.get(f))
            if v is None:
                ok = False
                break
            vec.append(v)
        if not ok:
            continue
        rows.append(vec)
        ys.append(pnl)
    if not rows:
        return np.zeros((0, 0)), np.zeros(0), keep_feat
    return np.asarray(rows, dtype=float), np.asarray(ys, dtype=float), keep_feat


def _rank_bucket(score: float, *, useful_cut: float = 0.02, weak_cut: float = 0.005) -> str:
    if score >= useful_cut:
        return "USEFUL"
    if score >= weak_cut:
        return "WEAK"
    if score > 0:
        return "NOISE"
    return "REMOVE"


def run_feature_information(
    conn: Any,
    *,
    write_reports: bool = True,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    closed = load_closed_trade_rows(conn)
    samples = [extract_sample(r) for r in closed]
    pnls_all = [_safe_float(s.get("pnl")) for s in samples]
    labels = [1 if (p or 0) > 0 else 0 for p in pnls_all]

    feature_rows: list[dict[str, Any]] = []
    categorical = {
        "symbol", "direction", "pattern", "gate_decision", "news_category", "market_regime",
    }
    for name in PREDICTION_FEATURES:
        xs: list[float] = []
        ys: list[float] = []
        labs: list[int] = []
        # categorical → frequency-encoded codes for IG/MI/AUC
        if name in categorical:
            raw_pairs = []
            for s, lab in zip(samples, labels):
                raw = s.get(name)
                p = _safe_float(s.get("pnl"))
                if raw is None or str(raw).upper() == "NULL" or p is None:
                    continue
                raw_pairs.append((str(raw), float(p), lab))
            if len(raw_pairs) < 5:
                feature_rows.append({
                    "feature": name,
                    "n": len(raw_pairs),
                    "information_gain": None,
                    "mutual_information": None,
                    "auc": None,
                    "single_feature_ev": None,
                    "single_feature_pf": None,
                    "shap_importance": None,
                    "permutation_importance": None,
                    "rank_score": -1.0,
                    "bucket": "REMOVE",
                    "reason": "insufficient_filled",
                })
                continue
            codes = {v: float(i) for i, v in enumerate(sorted({r[0] for r in raw_pairs}))}
            for raw, p, lab in raw_pairs:
                xs.append(codes[raw])
                ys.append(p)
                labs.append(lab)
        else:
            for s, lab in zip(samples, labels):
                v = _safe_float(s.get(name))
                p = _safe_float(s.get("pnl"))
                if v is None or p is None:
                    continue
                xs.append(float(v))
                ys.append(float(p))
                labs.append(lab)
        if len(xs) < 5:
            feature_rows.append({
                "feature": name,
                "n": len(xs),
                "information_gain": None,
                "mutual_information": None,
                "auc": None,
                "single_feature_ev": None,
                "single_feature_pf": None,
                "shap_importance": None,
                "permutation_importance": None,
                "rank_score": -1.0,
                "bucket": "REMOVE",
                "reason": "insufficient_filled",
            })
            continue
        ig = _information_gain_binary(xs, labs)
        mi = _mutual_info_binned(xs, ys)
        auc = _auc_mann_whitney(xs, labs)
        # AUC distance from 0.5
        auc_edge = abs((auc or 0.5) - 0.5) if auc is not None else 0.0
        edge = _single_feature_edge(xs, ys)
        corr = abs(_pearson(xs, ys) or 0.0)
        rank_score = round(
            0.35 * (mi or 0.0)
            + 0.25 * (ig or 0.0)
            + 0.25 * auc_edge
            + 0.15 * min(1.0, corr),
            4,
        )
        # Constant numerics → REMOVE
        if len(set(round(x, 10) for x in xs)) <= 1:
            rank_score = 0.0
            bucket = "REMOVE"
        else:
            bucket = _rank_bucket(rank_score)
        feature_rows.append({
            "feature": name,
            "n": len(xs),
            "information_gain": ig,
            "mutual_information": mi,
            "auc": auc,
            "single_feature_ev": edge.get("ev"),
            "single_feature_pf": edge.get("pf"),
            "single_feature_wr": edge.get("wr"),
            "single_feature_side": edge.get("side"),
            "ci_ev": edge.get("ci_ev"),
            "p_value": edge.get("p_value"),
            "correlation": _pearson(xs, ys),
            "shap_importance": None,  # filled below if model fits
            "permutation_importance": None,
            "rank_score": rank_score,
            "bucket": bucket,
        })

    # sklearn permutation / linear proxy for "SHAP"
    X, y_pnl, keep_feat = _matrix_from_samples(samples, list(PREDICTION_FEATURES))
    shap_map: dict[str, float] = {}
    perm_map: dict[str, float] = {}
    if len(X) >= 12 and X.shape[1] >= 1:
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.inspection import permutation_importance

            y_bin = (y_pnl > 0).astype(int)
            if len(set(y_bin.tolist())) >= 2:
                clf = RandomForestClassifier(
                    n_estimators=80, max_depth=3, random_state=42, n_jobs=1,
                )
                clf.fit(X, y_bin)
                # impurity importance as SHAP proxy when shap unavailable
                for f, imp in zip(keep_feat, clf.feature_importances_):
                    shap_map[f] = round(float(imp), 4)
                perm = permutation_importance(
                    clf, X, y_bin, n_repeats=15, random_state=42, n_jobs=1,
                )
                for f, imp in zip(keep_feat, perm.importances_mean):
                    perm_map[f] = round(float(imp), 4)
        except Exception as exc:
            shap_map = {"_error": str(exc)}  # type: ignore[assignment]

    for row in feature_rows:
        f = row["feature"]
        if f in shap_map and f != "_error":
            row["shap_importance"] = shap_map[f]
            row["shap_note"] = "sklearn RF impurity importance (SHAP lib not installed)"
        if f in perm_map:
            row["permutation_importance"] = perm_map[f]
            # boost rank slightly with perm
            if row.get("permutation_importance") is not None:
                row["rank_score"] = round(
                    float(row["rank_score"]) + 0.2 * max(0.0, float(row["permutation_importance"])),
                    4,
                )
                row["bucket"] = _rank_bucket(float(row["rank_score"]))

    feature_rows.sort(key=lambda r: float(r.get("rank_score") or -1), reverse=True)
    top20 = feature_rows[:20]

    # Combinations on non-REMOVE features with fill
    combo_pool = [
        r["feature"] for r in feature_rows
        if r.get("bucket") in ("USEFUL", "WEAK", "NOISE") and (r.get("n") or 0) >= 8
    ][:12]

    cat_codes: dict[str, dict[str, float]] = {}
    for f in combo_pool:
        if f in categorical:
            vals = sorted({
                str(s.get(f)) for s in samples
                if s.get(f) is not None and str(s.get(f)).upper() != "NULL"
            })
            cat_codes[f] = {v: float(i) for i, v in enumerate(vals)}

    def _feat_val(s: dict[str, Any], f: str) -> float | None:
        if f in categorical:
            raw = s.get(f)
            if raw is None or str(raw).upper() == "NULL":
                return None
            return cat_codes.get(f, {}).get(str(raw))
        return _safe_float(s.get(f))

    combos: list[dict[str, Any]] = []
    for k in (2, 3, 4):
        if len(combo_pool) < k:
            continue
        for feat_set in itertools.combinations(combo_pool, k):
            rules = []
            for f in feat_set:
                xs = []
                for s in samples:
                    v = _feat_val(s, f)
                    p = _safe_float(s.get("pnl"))
                    if v is not None and p is not None:
                        xs.append((float(v), float(p)))
                if len(xs) < 8:
                    rules = []
                    break
                med = float(np.median([a[0] for a in xs]))
                hi = [p for v, p in xs if v > med]
                lo = [p for v, p in xs if v <= med]
                side = "high" if (np.mean(hi) if hi else -1e9) >= (np.mean(lo) if lo else -1e9) else "low"
                rules.append((f, side, med))
            if len(rules) != k:
                continue
            kept_pnls = []
            for s in samples:
                p = _safe_float(s.get("pnl"))
                if p is None:
                    continue
                ok = True
                for f, side, med in rules:
                    v = _feat_val(s, f)
                    if v is None:
                        ok = False
                        break
                    if side == "high" and not (v > med):
                        ok = False
                        break
                    if side == "low" and not (v <= med):
                        ok = False
                        break
                if ok:
                    kept_pnls.append(float(p))
            if len(kept_pnls) < 5:
                continue
            pf, ev, wr, n = _pf_ev_wr(kept_pnls)
            base_pnls = [float(p) for p in pnls_all if p is not None]
            pval = _perm_pvalue(kept_pnls, base_pnls) if base_pnls else None
            ci = _bootstrap_ci_ev(kept_pnls)
            combos.append({
                "features": list(feat_set),
                "k": k,
                "n": n,
                "ev": None if ev is None else round(ev, 4),
                "pf": None if pf is None else round(pf, 4),
                "wr": None if wr is None else round(wr, 2),
                "ci_ev": ci,
                "p_value": pval,
                "rules": [{"feature": f, "side": s, "median": m} for f, s, m in rules],
            })
    combos.sort(key=lambda c: (c.get("ev") is not None, c.get("ev") or -1e9), reverse=True)
    top100 = combos[:100]

    ml_qa = audit_ml_dataset(samples)

    result = {
        "ok": True,
        "generated_at": now,
        "n_samples": len(samples),
        "features": feature_rows,
        "top20": top20,
        "buckets": {
            "USEFUL": [r["feature"] for r in feature_rows if r["bucket"] == "USEFUL"],
            "WEAK": [r["feature"] for r in feature_rows if r["bucket"] == "WEAK"],
            "NOISE": [r["feature"] for r in feature_rows if r["bucket"] == "NOISE"],
            "REMOVE": [r["feature"] for r in feature_rows if r["bucket"] == "REMOVE"],
        },
        "combinations_top100": top100,
        "ml_dataset_qa": ml_qa,
        "shap_backend": "sklearn_rf_impurity_proxy",
        "read_only": True,
    }
    md = format_feature_information_md(result)
    result["report_markdown"] = md
    if write_reports:
        out = Path(os.environ.get("FEATURE_INFO_DIR", str(report_dir or REPORT_DIR)))
        out.mkdir(parents=True, exist_ok=True)
        (out / "FEATURE_INFORMATION.md").write_text(md, encoding="utf-8")
        (out / "feature_information.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8",
        )
        (out / "feature_combinations_top100.json").write_text(
            json.dumps(top100, indent=2, default=str), encoding="utf-8",
        )
        result["report_paths"] = {
            "md": str(out / "FEATURE_INFORMATION.md"),
            "json": str(out / "feature_information.json"),
            "combos": str(out / "feature_combinations_top100.json"),
        }
    return result


def audit_ml_dataset(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Part 6 — label/leakage/constant/duplicate/imbalance/drift checks."""
    critical: list[str] = []
    warnings: list[str] = []
    n = len(samples)
    if n == 0:
        return {"critical": ["CRITICAL: no ML samples"], "warnings": [], "ok": False}

    # missing targets
    missing_tgt = sum(
        1 for s in samples
        if _safe_float(s.get("pnl")) is None and s.get("profitable") is None
    )
    if missing_tgt:
        critical.append(f"CRITICAL: missing targets on {missing_tgt}/{n} samples")

    categorical = {
        "symbol", "direction", "pattern", "gate_decision", "news_category", "market_regime",
    }

    # constant / empty columns
    const_cols = []
    for f in PREDICTION_FEATURES:
        if f in categorical:
            vals = [str(s.get(f)).strip() for s in samples if s.get(f) is not None and str(s.get(f)).strip() and str(s.get(f)).upper() != "NULL"]
            if not vals:
                const_cols.append(f"{f}(all_null)")
            elif len(set(vals)) <= 1:
                const_cols.append(f)
            continue
        vals = [_safe_float(s.get(f)) for s in samples]
        filled = [v for v in vals if v is not None]
        if filled and len(set(round(v, 10) for v in filled)) <= 1:
            const_cols.append(f)
        if not filled:
            const_cols.append(f"{f}(all_null)")
    if const_cols:
        critical.append(f"CRITICAL: constant/empty columns: {', '.join(const_cols[:20])}")

    # duplicate columns (corr ~ 1) — numerics only
    dup_pairs = []
    feats = [f for f in PREDICTION_FEATURES if f not in categorical]
    series: dict[str, list[float]] = {}
    for f in feats:
        col = []
        for s in samples:
            v = _safe_float(s.get(f))
            col.append(float(v) if v is not None else float("nan"))
        if sum(1 for x in col if not math.isnan(x)) >= 5:
            series[f] = col
    keys = list(series.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a = np.asarray(series[keys[i]], dtype=float)
            b = np.asarray(series[keys[j]], dtype=float)
            mask = ~np.isnan(a) & ~np.isnan(b)
            if mask.sum() < 5:
                continue
            if abs(_pearson(a[mask].tolist(), b[mask].tolist()) or 0) > 0.999:
                dup_pairs.append((keys[i], keys[j]))
    if dup_pairs:
        critical.append(f"CRITICAL: duplicate columns: {dup_pairs[:10]}")

    # label leakage: features that are outcome aliases
    leak_names = {"pnl", "pnl_pct", "result", "profitable", "mfe", "mae", "holding_time"}
    for s in samples[:1]:
        for k in s:
            if k in leak_names and k in PREDICTION_FEATURES:
                critical.append(f"CRITICAL: label leakage feature in predictors: {k}")

    # future / time leakage heuristics
    if any(_safe_float(s.get("mfe")) is not None for s in samples):
        warnings.append("mfe/mae present on samples (labels/aux — ensure excluded from predictors)")
    # class imbalance
    wins = sum(1 for s in samples if int(s.get("profitable") or 0) == 1 or (_safe_float(s.get("pnl")) or 0) > 0)
    ratio = wins / n if n else 0
    if ratio < 0.2 or ratio > 0.8:
        critical.append(f"CRITICAL: class imbalance win_rate={ratio:.2%} on n={n}")

    # feature drift: compare first vs last half means for filled numerics
    drift = []
    mid = n // 2
    if mid >= 5:
        for f in feats:
            a = [_safe_float(s.get(f)) for s in samples[:mid]]
            b = [_safe_float(s.get(f)) for s in samples[mid:]]
            aa = [x for x in a if x is not None]
            bb = [x for x in b if x is not None]
            if len(aa) < 5 or len(bb) < 5:
                continue
            ma, mb = float(np.mean(aa)), float(np.mean(bb))
            denom = max(abs(ma), abs(mb), 1e-6)
            if abs(ma - mb) / denom > 0.5 and abs(ma - mb) > 1e-6:
                drift.append({"feature": f, "early_mean": ma, "late_mean": mb})
    if drift:
        warnings.append(f"feature drift candidates: {[d['feature'] for d in drift[:10]]}")

    # time leakage: created_at not sorted / identical timestamps
    ts = [int(s.get("created_at") or 0) for s in samples]
    if ts and len(set(ts)) == 1:
        critical.append("CRITICAL: time leakage/risk — all samples share identical timestamp")

    return {
        "ok": len(critical) == 0,
        "critical": critical,
        "warnings": warnings,
        "n": n,
        "win_rate": round(100.0 * wins / n, 2) if n else None,
        "constant_columns": const_cols,
        "duplicate_pairs": dup_pairs,
        "drift": drift[:20],
    }


def format_feature_information_md(result: dict[str, Any]) -> str:
    lines = [
        "# Feature Information V1",
        "",
        f"_samples={result.get('n_samples')} | shap_backend={result.get('shap_backend')}_",
        "",
        "## Ranking buckets",
        "",
    ]
    for b, feats in (result.get("buckets") or {}).items():
        lines.append(f"- **{b}** ({len(feats)}): `{', '.join(feats[:40])}`")
    lines.extend(["", "## TOP 20", ""])
    lines.append("| rank | feature | score | IG | MI | AUC | EV | PF | perm | bucket |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for i, r in enumerate(result.get("top20") or [], 1):
        lines.append(
            f"| {i} | {r['feature']} | {r.get('rank_score')} | {r.get('information_gain')} | "
            f"{r.get('mutual_information')} | {r.get('auc')} | {r.get('single_feature_ev')} | "
            f"{r.get('single_feature_pf')} | {r.get('permutation_importance')} | {r.get('bucket')} |"
        )
    lines.extend(["", "## TOP combinations (by EV, max 20 shown)", ""])
    for c in (result.get("combinations_top100") or [])[:20]:
        lines.append(
            f"- `{'+'.join(c['features'])}` n={c['n']} EV={c.get('ev')} PF={c.get('pf')} "
            f"WR={c.get('wr')} CI={c.get('ci_ev')} p={c.get('p_value')}"
        )
    qa = result.get("ml_dataset_qa") or {}
    lines.extend(["", "## ML dataset QA", ""])
    if qa.get("critical"):
        for c in qa["critical"]:
            lines.append(f"- **{c}**")
    for w in qa.get("warnings") or []:
        lines.append(f"- warning: {w}")
    if qa.get("ok"):
        lines.append("- QA ok (no CRITICAL)")
    lines.append("")
    return "\n".join(lines)


__all__ = ["run_feature_information", "audit_ml_dataset"]
