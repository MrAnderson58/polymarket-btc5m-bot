"""Training dataset export + Shadow ML Ranking V1 (observe-only scores).

Never places trades. Existing strategy remains authoritative.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import pickle
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence import feature_store as fs

logger = logging.getLogger(__name__)

DATASET_DIR = BASE_DIR / "research" / "ml" / "datasets"
MODEL_DIR = BASE_DIR / "research" / "ml" / "models"
ML_REPORT_PATH = BASE_DIR / "ML_REPORT.md"
SHADOW_MODE = True  # hard safety — never flip for execution


def dataset_dir(*, feature_version: str | None = None) -> Path:
    ver = feature_version or fs.FEATURE_VERSION
    root = Path(os.environ.get("ML_DATASET_DIR", str(DATASET_DIR)))
    return root / ver


def model_dir(*, feature_version: str | None = None) -> Path:
    ver = feature_version or fs.FEATURE_VERSION
    root = Path(os.environ.get("ML_MODEL_DIR", str(MODEL_DIR)))
    return root / ver


def _filter_samples(
    samples: list[dict[str, Any]],
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    symbols: list[str] | None = None,
    feature_version: str | None = None,
) -> list[dict[str, Any]]:
    out = samples
    if feature_version:
        out = [s for s in out if str(s.get("feature_version")) == feature_version]
    if start_ts is not None:
        out = [s for s in out if int(s.get("created_at") or 0) >= int(start_ts)]
    if end_ts is not None:
        out = [s for s in out if int(s.get("created_at") or 0) <= int(end_ts)]
    if symbols:
        want = {str(x).upper() for x in symbols}
        out = [s for s in out if str(s.get("symbol") or "").upper() in want]
    return out


def export_training_dataset(
    samples: list[dict[str, Any]] | None = None,
    *,
    feature_version: str = fs.FEATURE_VERSION,
    start_ts: int | None = None,
    end_ts: int | None = None,
    symbols: list[str] | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Export CSV + Parquet for offline ML with version / date / symbol filters."""
    if samples is None:
        samples = fs.load_samples(feature_version=feature_version)
    filtered = _filter_samples(
        samples,
        start_ts=start_ts,
        end_ts=end_ts,
        symbols=symbols,
        feature_version=feature_version,
    )
    directory = out_dir or dataset_dir(feature_version=feature_version)
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "training_dataset.csv"
    parquet_path = directory / "training_dataset.parquet"
    meta_path = directory / "dataset_meta.json"

    fieldnames = sorted({k for s in filtered for k in s.keys()}) if filtered else (
        ["sample_id", "feature_version", fs.LABEL_COL] + fs.PREDICTION_FEATURES
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for s in filtered:
            writer.writerow({k: s.get(k) for k in fieldnames})

    parquet_ok = False
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if filtered:
            cols = {k: [s.get(k) for s in filtered] for k in fieldnames}
            pq.write_table(pa.Table.from_pydict(cols), parquet_path)
            parquet_ok = True
    except Exception as exc:
        logger.info("training_dataset: parquet skipped: %s", exc)

    meta = {
        "feature_version": feature_version,
        "n_rows": len(filtered),
        "n_features_prediction": len(fs.PREDICTION_FEATURES),
        "label": fs.LABEL_COL,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "symbols": symbols,
        "shadow_mode": True,
        "ml_may_execute": False,
        "paths": {
            "csv": str(csv_path.resolve()),
            "parquet": str(parquet_path.resolve()) if parquet_ok else None,
        },
        "updated_at": int(time.time()),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "n_rows": len(filtered),
        "csv_path": str(csv_path.resolve()),
        "parquet_path": str(parquet_path.resolve()) if parquet_ok else None,
        "meta": meta,
        "samples": filtered,
    }


def _matrix(samples: list[dict[str, Any]]) -> tuple[list[list[float]], list[int], list[str]]:
    """Encode prediction features to a numeric matrix (categoricals → codes)."""
    cat_cols = [c for c in fs.PREDICTION_FEATURES if fs.FEATURE_SPEC[c]["type"] == "categorical"]
    num_cols = [c for c in fs.PREDICTION_FEATURES if fs.FEATURE_SPEC[c]["type"] == "numeric"]
    cat_maps: dict[str, dict[str, int]] = {}
    for c in cat_cols:
        values = sorted({str(s.get(c) or "NULL") for s in samples})
        cat_maps[c] = {v: i for i, v in enumerate(values)}

    feature_names = [f"cat__{c}" for c in cat_cols] + num_cols
    xs: list[list[float]] = []
    ys: list[int] = []
    for s in samples:
        vec: list[float] = []
        for c in cat_cols:
            vec.append(float(cat_maps[c].get(str(s.get(c) or "NULL"), 0)))
        for c in num_cols:
            v = s.get(c)
            try:
                vec.append(float(v) if v is not None else 0.0)
            except (TypeError, ValueError):
                vec.append(0.0)
        # Replace nan/inf
        clean = []
        for x in vec:
            if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
                clean.append(0.0)
            else:
                clean.append(float(x))
        xs.append(clean)
        ys.append(int(s.get(fs.LABEL_COL) or 0))
    return xs, ys, feature_names


def _select_backend() -> str:
    preferred = os.environ.get("ML_BACKEND", "auto").strip().lower()
    if preferred in ("lightgbm", "lgbm"):
        try:
            import lightgbm  # noqa: F401
            return "lightgbm"
        except ImportError:
            pass
    if preferred in ("xgboost", "xgb"):
        try:
            import xgboost  # noqa: F401
            return "xgboost"
        except ImportError:
            pass
    if preferred in ("sklearn", "gbm", "auto"):
        try:
            import lightgbm  # noqa: F401
            if preferred == "auto":
                return "lightgbm"
        except ImportError:
            pass
        try:
            import xgboost  # noqa: F401
            if preferred == "auto":
                return "xgboost"
        except ImportError:
            pass
        return "sklearn"
    return "sklearn"


def _fit_model(xs: list[list[float]], ys: list[int], backend: str):
    import numpy as np

    X = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=int)
    if backend == "lightgbm":
        import lightgbm as lgb

        model = lgb.LGBMClassifier(
            n_estimators=80,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=42,
            verbosity=-1,
        )
        model.fit(X, y)
        return model, "lightgbm"
    if backend == "xgboost":
        import xgboost as xgb

        model = xgb.XGBClassifier(
            n_estimators=80,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=42,
            eval_metric="logloss",
            verbosity=0,
        )
        model.fit(X, y)
        return model, "xgboost"
    from sklearn.ensemble import GradientBoostingClassifier

    model = GradientBoostingClassifier(
        n_estimators=80,
        learning_rate=0.05,
        max_depth=3,
        random_state=42,
    )
    model.fit(X, y)
    return model, "sklearn"


def _predict_proba(model: Any, xs: list[list[float]]) -> list[float]:
    import numpy as np

    X = np.asarray(xs, dtype=float)
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        # class 1 = profitable
        classes = list(getattr(model, "classes_", [0, 1]))
        if 1 in classes:
            idx = classes.index(1)
        else:
            idx = min(1, proba.shape[1] - 1)
        return [float(p[idx]) for p in proba]
    preds = model.predict(X)
    return [float(p) for p in preds]


def calibration_metrics(y_true: list[int], y_prob: list[float]) -> dict[str, Any]:
    from sklearn.metrics import (
        brier_score_loss,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    if len(set(y_true)) < 2 or len(y_true) < 5:
        return {
            "roc_auc": None,
            "precision": None,
            "recall": None,
            "brier": None,
            "calibration_curve": [],
            "note": "insufficient class diversity",
        }
    y_hat = [1 if p >= 0.5 else 0 for p in y_prob]
    # Calibration curve bins
    bins = 5
    curve = []
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        idxs = [j for j, p in enumerate(y_prob) if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if not idxs:
            continue
        mean_p = sum(y_prob[j] for j in idxs) / len(idxs)
        frac_pos = sum(y_true[j] for j in idxs) / len(idxs)
        curve.append({"bin": f"{lo:.2f}-{hi:.2f}", "mean_predicted": round(mean_p, 4), "frac_positive": round(frac_pos, 4), "n": len(idxs)})
    try:
        auc = float(roc_auc_score(y_true, y_prob))
    except Exception:
        auc = None
    return {
        "roc_auc": None if auc is None else round(auc, 4),
        "precision": round(float(precision_score(y_true, y_hat, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_hat, zero_division=0)), 4),
        "brier": round(float(brier_score_loss(y_true, y_prob)), 4),
        "calibration_curve": curve,
    }


def feature_importance(
    model: Any,
    feature_names: list[str],
    xs: list[list[float]],
    ys: list[int],
) -> dict[str, Any]:
    """Gain/impurity importance + optional SHAP; else permutation importance."""
    import numpy as np

    X = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=int)
    native: list[tuple[str, float]] = []
    if hasattr(model, "feature_importances_"):
        imp = list(model.feature_importances_)
        native = sorted(
            [(feature_names[i], float(imp[i])) for i in range(len(feature_names))],
            key=lambda t: -t[1],
        )

    shap_summary: list[dict[str, Any]] = []
    shap_available = False
    try:
        import shap  # type: ignore

        explainer = shap.TreeExplainer(model)
        # Limit rows for speed
        X_s = X[: min(200, len(X))]
        values = explainer.shap_values(X_s)
        if isinstance(values, list):
            values = values[1] if len(values) > 1 else values[0]
        mean_abs = np.mean(np.abs(values), axis=0)
        shap_summary = [
            {"feature": feature_names[i], "mean_abs_shap": round(float(mean_abs[i]), 6)}
            for i in range(len(feature_names))
        ]
        shap_summary.sort(key=lambda d: -d["mean_abs_shap"])
        shap_available = True
    except Exception:
        # Permutation importance fallback
        from sklearn.inspection import permutation_importance

        try:
            r = permutation_importance(model, X, y, n_repeats=5, random_state=42)
            shap_summary = [
                {
                    "feature": feature_names[i],
                    "mean_abs_shap": round(float(abs(r.importances_mean[i])), 6),
                    "method": "permutation_importance",
                }
                for i in range(len(feature_names))
            ]
            shap_summary.sort(key=lambda d: -d["mean_abs_shap"])
        except Exception as exc:
            logger.info("feature_importance fallback failed: %s", exc)

    top_pos = native[:10] if native else [(d["feature"], d["mean_abs_shap"]) for d in shap_summary[:10]]
    # "Negative" = least important / bottom of ranking for reporting
    top_neg = list(reversed(native[-10:])) if native else [
        (d["feature"], d["mean_abs_shap"]) for d in shap_summary[-10:]
    ]
    return {
        "native_importance": [{"feature": f, "importance": round(v, 6)} for f, v in native],
        "shap_summary": shap_summary,
        "shap_available": shap_available,
        "top_positive": [{"feature": f, "importance": round(float(v), 6)} for f, v in top_pos],
        "top_negative": [{"feature": f, "importance": round(float(v), 6)} for f, v in top_neg],
    }


def train_shadow_ml(
    samples: list[dict[str, Any]] | None = None,
    *,
    feature_version: str = fs.FEATURE_VERSION,
    test_frac: float = 0.3,
) -> dict[str, Any]:
    """Train shadow classifier predicting P(trade profitable). No execution side-effects."""
    assert SHADOW_MODE is True
    if samples is None:
        samples = fs.load_samples(feature_version=feature_version)
    samples = [s for s in samples if s.get(fs.LABEL_COL) is not None]
    if len(samples) < 10:
        return {
            "ok": False,
            "error": f"need >=10 labeled samples, got {len(samples)}",
            "shadow_mode": True,
            "ml_may_execute": False,
        }

    # Chronological split
    ordered = sorted(samples, key=lambda s: int(s.get("created_at") or 0))
    cut = max(1, int(len(ordered) * (1.0 - test_frac)))
    if cut >= len(ordered):
        cut = len(ordered) - 1
    train_s, test_s = ordered[:cut], ordered[cut:]
    # Fit encoding on train+test jointly for stable category codes
    xs_all, ys_all, feature_names = _matrix(ordered)
    xs_train, ys_train = xs_all[:cut], ys_all[:cut]
    xs_test, ys_test = xs_all[cut:], ys_all[cut:]

    backend = _select_backend()
    model, backend_used = _fit_model(xs_train, ys_train, backend)
    train_prob = _predict_proba(model, xs_train)
    test_prob = _predict_proba(model, xs_test) if xs_test else []

    train_metrics = calibration_metrics(ys_train, train_prob)
    test_metrics = calibration_metrics(ys_test, test_prob) if ys_test else {}
    importance = feature_importance(model, feature_names, xs_train, ys_train)

    # Shadow scores on all samples (never used for execution)
    all_prob = _predict_proba(model, xs_all)
    scored = []
    for s, p in zip(ordered, all_prob):
        scored.append({
            "sample_id": s.get("sample_id"),
            "symbol": s.get("symbol"),
            "ml_score": round(float(p), 6),
            "profitable": s.get("profitable"),
            "shadow_mode": True,
            "ml_may_execute": False,
        })

    mdir = model_dir(feature_version=feature_version)
    mdir.mkdir(parents=True, exist_ok=True)
    model_path = mdir / "shadow_model.pkl"
    meta_path = mdir / "model_meta.json"
    scores_path = mdir / "shadow_scores.jsonl"
    with model_path.open("wb") as fh:
        pickle.dump({"model": model, "feature_names": feature_names, "backend": backend_used}, fh)
    with scores_path.open("w", encoding="utf-8") as fh:
        for row in scored:
            fh.write(json.dumps(row) + "\n")

    n_pos = sum(ys_all)
    result = {
        "ok": True,
        "shadow_mode": True,
        "ml_may_execute": False,
        "backend": backend_used,
        "feature_version": feature_version,
        "dataset_size": len(ordered),
        "train_size": len(train_s),
        "test_size": len(test_s),
        "feature_count": len(feature_names),
        "class_balance": {
            "profitable": int(n_pos),
            "unprofitable": int(len(ys_all) - n_pos),
            "positive_rate": round(n_pos / len(ys_all), 4),
        },
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "feature_importance": importance,
        "model_path": str(model_path.resolve()),
        "scores_path": str(scores_path.resolve()),
        "recommended_future_features": [
            "realized_volatility_regime_interaction",
            "orderbook_depth_imbalance_at_entry",
            "funding_cross_exchange_dispersion",
            "news_embedding_similarity_to_past_winners",
            "time_of_day_session_flags",
            "btc_lead_lag_vs_alt_beta",
        ],
        "generated_at": int(time.time()),
    }
    meta_path.write_text(json.dumps({
        k: v for k, v in result.items()
        if k not in ("feature_importance",)
    }, indent=2, default=str), encoding="utf-8")
    # Store importance separately for report
    (mdir / "feature_importance.json").write_text(
        json.dumps(importance, indent=2), encoding="utf-8",
    )
    result["report_markdown"] = format_ml_report(result)
    return result


def predict_ml_score(features: dict[str, Any], *, feature_version: str = fs.FEATURE_VERSION) -> float | None:
    """Shadow score only — must never gate live opens."""
    assert SHADOW_MODE is True
    model_path = model_dir(feature_version=feature_version) / "shadow_model.pkl"
    if not model_path.exists():
        return None
    with model_path.open("rb") as fh:
        blob = pickle.load(fh)
    model = blob["model"]
    # Build one-row sample then matrix
    sample = dict(features)
    sample.setdefault("profitable", 0)
    sample.setdefault("feature_version", feature_version)
    xs, _, _ = _matrix([sample])
    if not xs:
        return None
    return float(_predict_proba(model, xs)[0])


def format_ml_report(result: dict[str, Any]) -> str:
    imp = result.get("feature_importance") or {}
    lines = [
        "# ML_REPORT — Feature Store & ML Ranking V1 (Shadow)",
        "",
        f"**Generated:** {result.get('generated_at')}",
        f"**Shadow mode:** {result.get('shadow_mode', True)}",
        f"**ML may execute:** {result.get('ml_may_execute', False)}",
        f"**Backend:** {result.get('backend')}",
        f"**Feature version:** {result.get('feature_version')}",
        "",
        "## Dataset",
        "",
        f"- size: **{result.get('dataset_size')}** (train={result.get('train_size')} test={result.get('test_size')})",
        f"- feature count: **{result.get('feature_count')}**",
        f"- class balance: `{json.dumps(result.get('class_balance') or {})}`",
        "",
        "## Metrics (test / holdout)",
        "",
        "```json",
        json.dumps(result.get("test_metrics") or {}, indent=2),
        "```",
        "",
        "## Metrics (train)",
        "",
        "```json",
        json.dumps(result.get("train_metrics") or {}, indent=2),
        "```",
        "",
        "## Feature importance (top positive)",
        "",
    ]
    for row in (imp.get("top_positive") or [])[:15]:
        lines.append(f"- **{row.get('feature')}**: {row.get('importance')}")
    lines.extend(["", "## Feature importance (lowest / negative end)", ""])
    for row in (imp.get("top_negative") or [])[:15]:
        lines.append(f"- **{row.get('feature')}**: {row.get('importance')}")
    lines.extend([
        "",
        "## SHAP summary",
        "",
        f"SHAP available: **{imp.get('shap_available')}**",
        "",
    ])
    for row in (imp.get("shap_summary") or [])[:15]:
        lines.append(
            f"- {row.get('feature')}: mean_abs={row.get('mean_abs_shap')}"
            + (f" ({row.get('method')})" if row.get("method") else ""),
        )
    lines.extend([
        "",
        "## Recommended future features",
        "",
    ])
    for f in result.get("recommended_future_features") or []:
        lines.append(f"- {f}")
    lines.extend([
        "",
        "## Safety",
        "",
        "- Existing strategy remains authoritative.",
        "- This model only emits shadow `ml_score` values.",
        "- It must never place or block trades in V1.",
        "",
    ])
    return "\n".join(lines)


def write_ml_report(result: dict[str, Any], *, path: Path | None = None) -> Path:
    p = Path(path) if path else Path(os.environ.get("ML_REPORT_PATH", str(ML_REPORT_PATH)))
    md = result.get("report_markdown") or format_ml_report(result)
    p.write_text(md, encoding="utf-8")
    return p


def read_ml_report(path: Path | None = None) -> str:
    p = Path(path) if path else Path(os.environ.get("ML_REPORT_PATH", str(ML_REPORT_PATH)))
    if p.exists():
        return p.read_text(encoding="utf-8")
    return (
        "No ML_REPORT.md yet. Run:\n"
        "  python -m bot.research.market_events build-dataset\n"
        "  python -m bot.research.market_events train-ml\n"
    )


def run_build_dataset(conn: Any, **kwargs: Any) -> dict[str, Any]:
    sync = fs.sync_feature_store(conn)
    exported = export_training_dataset(
        feature_version=kwargs.get("feature_version") or fs.FEATURE_VERSION,
        start_ts=kwargs.get("start_ts"),
        end_ts=kwargs.get("end_ts"),
        symbols=kwargs.get("symbols"),
    )
    return {"ok": True, "feature_store": sync, "dataset": exported, "shadow_mode": True}


def run_train_ml(conn: Any | None = None, **kwargs: Any) -> dict[str, Any]:
    # Ensure store exists
    if conn is not None:
        fs.sync_feature_store(conn)
        export_training_dataset()
    result = train_shadow_ml(feature_version=kwargs.get("feature_version") or fs.FEATURE_VERSION)
    if result.get("ok"):
        path = write_ml_report(result)
        result["report_path"] = str(path.resolve())
    return result


__all__ = [
    "DATASET_DIR",
    "ML_REPORT_PATH",
    "MODEL_DIR",
    "SHADOW_MODE",
    "calibration_metrics",
    "dataset_dir",
    "export_training_dataset",
    "feature_importance",
    "format_ml_report",
    "model_dir",
    "predict_ml_score",
    "read_ml_report",
    "run_build_dataset",
    "run_train_ml",
    "train_shadow_ml",
    "write_ml_report",
]
