"""Feature importance consensus for Edge Discovery V3."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v3.dataset import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
)


def _target_binary(pnls: np.ndarray) -> np.ndarray:
    return (pnls > 0).astype(int)


def mutual_information_scores(
    numeric: dict[str, np.ndarray],
    cats: dict[str, np.ndarray],
    pnls: np.ndarray,
) -> dict[str, float]:
    y = _target_binary(pnls)
    scores: dict[str, float] = {}
    try:
        from sklearn.feature_selection import mutual_info_classif
    except Exception:
        return scores

    X_cols: list[np.ndarray] = []
    names: list[str] = []
    for f in NUMERIC_FEATURES:
        arr = numeric.get(f)
        if arr is None or not np.isfinite(arr).any():
            continue
        med = float(np.nanmedian(arr))
        X_cols.append(np.where(np.isfinite(arr), arr, med))
        names.append(f)
    if X_cols:
        X = np.column_stack(X_cols)
        try:
            mi = mutual_info_classif(X, y, discrete_features=False, random_state=0)
            for n, v in zip(names, mi):
                scores[n] = float(v)
        except Exception:
            pass

    # Categorical: simple MI via contingency
    for f in CATEGORICAL_FEATURES:
        vals = cats.get(f)
        if vals is None:
            continue
        # encode
        uniq = {v: i for i, v in enumerate(sorted(set(str(x) for x in vals)))}
        if len(uniq) < 2:
            continue
        enc = np.asarray([uniq[str(v)] for v in vals], dtype=float).reshape(-1, 1)
        try:
            mi = mutual_info_classif(enc, y, discrete_features=True, random_state=0)
            scores[f] = float(mi[0])
        except Exception:
            pass
    return scores


def information_gain_from_edges(candidates: list[dict[str, Any]]) -> dict[str, float]:
    """Proxy IG: count quality-weighted feature appearances in surviving edges."""
    scores: dict[str, float] = {}
    for c in candidates:
        w = float(c.get("quality_score") or c.get("edge_score") or 1.0)
        for f in c.get("features") or []:
            scores[f] = scores.get(str(f), 0.0) + w
    # normalize
    if scores:
        mx = max(scores.values()) or 1.0
        scores = {k: round(v / mx, 4) for k, v in scores.items()}
    return scores


def permutation_importance_scores(
    numeric: dict[str, np.ndarray],
    pnls: np.ndarray,
    *,
    n_repeats: int = 5,
    seed: int = 0,
) -> dict[str, float]:
    """Corr(|feature|, pnl) drop under permutation as lightweight importance."""
    rng = np.random.default_rng(seed)
    scores: dict[str, float] = {}
    y = pnls
    for f in NUMERIC_FEATURES:
        arr = numeric.get(f)
        if arr is None:
            continue
        valid = np.isfinite(arr)
        if int(valid.sum()) < 20:
            continue
        x = arr[valid]
        yy = y[valid]
        base = abs(float(np.corrcoef(x, yy)[0, 1])) if len(x) > 2 else 0.0
        if not np.isfinite(base):
            base = 0.0
        drops = []
        for _ in range(n_repeats):
            xp = rng.permutation(x)
            c = abs(float(np.corrcoef(xp, yy)[0, 1])) if len(x) > 2 else 0.0
            if not np.isfinite(c):
                c = 0.0
            drops.append(base - c)
        scores[f] = round(float(np.mean(drops)), 6)
    return scores


def shap_scores_optional(
    numeric: dict[str, np.ndarray],
    pnls: np.ndarray,
) -> dict[str, float]:
    """SHAP via sklearn tree if shap installed; else empty."""
    try:
        import shap  # type: ignore
        from sklearn.ensemble import GradientBoostingRegressor
    except Exception:
        return {}
    cols = []
    mats = []
    for f in NUMERIC_FEATURES:
        arr = numeric.get(f)
        if arr is None or not np.isfinite(arr).any():
            continue
        med = float(np.nanmedian(arr))
        cols.append(f)
        mats.append(np.where(np.isfinite(arr), arr, med))
    if len(cols) < 2 or len(pnls) < 30:
        return {}
    X = np.column_stack(mats)
    try:
        model = GradientBoostingRegressor(random_state=0, max_depth=2, n_estimators=40)
        model.fit(X, pnls)
        explainer = shap.Explainer(model.predict, X[: min(200, len(X))])
        sv = explainer(X[: min(200, len(X))])
        mean_abs = np.abs(sv.values).mean(axis=0)
        return {c: float(v) for c, v in zip(cols, mean_abs)}
    except Exception:
        # Fallback to impurity importance
        try:
            model = GradientBoostingRegressor(random_state=0, max_depth=2, n_estimators=40)
            model.fit(X, pnls)
            return {c: float(v) for c, v in zip(cols, model.feature_importances_)}
        except Exception:
            return {}


def consensus_ranking(
    *,
    mi: dict[str, float],
    ig: dict[str, float],
    perm: dict[str, float],
    shap: dict[str, float],
) -> list[dict[str, Any]]:
    keys = set(mi) | set(ig) | set(perm) | set(shap)

    def norm(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return {}
        mx = max(abs(v) for v in d.values()) or 1.0
        return {k: abs(v) / mx for k, v in d.items()}

    mi_n, ig_n, perm_n, shap_n = norm(mi), norm(ig), norm(perm), norm(shap)
    ranked = []
    for k in keys:
        parts = [mi_n.get(k), ig_n.get(k), perm_n.get(k), shap_n.get(k)]
        present = [p for p in parts if p is not None]
        if not present:
            continue
        score = float(np.mean(present))
        ranked.append({
            "feature": k,
            "consensus": round(score, 4),
            "mutual_information": round(mi.get(k, 0.0), 6) if k in mi else None,
            "information_gain": round(ig.get(k, 0.0), 4) if k in ig else None,
            "permutation_importance": round(perm.get(k, 0.0), 6) if k in perm else None,
            "shap": round(shap.get(k, 0.0), 6) if k in shap else None,
        })
    ranked.sort(key=lambda x: -float(x["consensus"]))
    return ranked


def compute_importance(
    numeric: dict[str, np.ndarray],
    cats: dict[str, np.ndarray],
    pnls: np.ndarray,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    mi = mutual_information_scores(numeric, cats, pnls)
    ig = information_gain_from_edges(candidates)
    perm = permutation_importance_scores(numeric, pnls)
    shap = shap_scores_optional(numeric, pnls)
    ranking = consensus_ranking(mi=mi, ig=ig, perm=perm, shap=shap)
    return {
        "ranking": ranking,
        "mutual_information": mi,
        "information_gain": ig,
        "permutation_importance": perm,
        "shap": shap,
        "shap_available": bool(shap),
    }


__all__ = ["compute_importance", "consensus_ranking"]
