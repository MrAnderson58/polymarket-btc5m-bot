"""Machine learning models (Block 3) and feature importance (Block 4)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from bot.optimizer.constants import FEATURE_COLUMNS

try:
    from xgboost import XGBClassifier

    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False


def _rows_to_matrix(rows: list) -> tuple[np.ndarray, list[str]]:
    labels = list(FEATURE_COLUMNS)
    matrix: list[list[float]] = []
    for row in rows:
        values: list[float] = []
        ok = True
        for col in labels:
            val = row[col]
            if val is None or (isinstance(val, float) and math.isnan(val)):
                ok = False
                break
            values.append(float(val))
        if ok:
            matrix.append(values)
    return np.array(matrix, dtype=float), labels


def _filter_complete(rows: list, target_col: str) -> tuple[list, np.ndarray]:
    complete_rows = []
    y = []
    for row in rows:
        ok = True
        for col in FEATURE_COLUMNS:
            if row[col] is None:
                ok = False
                break
        if ok:
            complete_rows.append(row)
            y.append(int(row[target_col]))
    return complete_rows, np.array(y, dtype=int)


def train_models(rows: list) -> dict[str, Any]:
    if len(rows) < 50:
        return {"status": "insufficient_data", "min_rows": 50, "rows": len(rows)}

    results: dict[str, Any] = {"targets": {}, "feature_importance": {}}

    for target, label in (("is_stop", "stop_probability"), ("is_win", "win_probability")):
        complete, y = _filter_complete(rows, target)
        if len(complete) < 50 or len(set(y.tolist())) < 2:
            results["targets"][label] = {"status": "insufficient_data"}
            continue
        x, _ = _rows_to_matrix(complete)
        scaler = StandardScaler()
        x_scaled = scaler.fit_transform(x)

        models: dict[str, Any] = {}
        candidates = {
            "random_forest": RandomForestClassifier(
                n_estimators=100, max_depth=6, random_state=42
            ),
            "logistic_regression": LogisticRegression(max_iter=500, random_state=42),
            "decision_tree": DecisionTreeClassifier(max_depth=5, random_state=42),
        }
        if HAS_XGBOOST:
            candidates["xgboost"] = XGBClassifier(
                n_estimators=80,
                max_depth=4,
                eval_metric="logloss",
                random_state=42,
            )

        best_name = ""
        best_auc = -1.0
        for name, model in candidates.items():
            try:
                cv = cross_val_score(
                    model, x_scaled, y, cv=min(5, len(complete) // 20 or 2), scoring="roc_auc"
                )
                auc = float(np.mean(cv))
            except ValueError:
                model.fit(x_scaled, y)
                pred = model.predict(x_scaled)
                auc = float(accuracy_score(y, pred))
            models[name] = {"roc_auc_cv": auc}
            if auc > best_auc:
                best_auc = auc
                best_name = name

        model = candidates[best_name]
        model.fit(x_scaled, y)
        if hasattr(model, "feature_importances_"):
            imp = model.feature_importances_
        else:
            imp = np.abs(model.coef_[0]) if hasattr(model, "coef_") else np.zeros(len(FEATURE_COLUMNS))
        total = float(imp.sum()) or 1.0
        importance = [
            {"feature": FEATURE_COLUMNS[i], "importance_pct": round(100 * imp[i] / total, 1)}
            for i in np.argsort(imp)[::-1]
        ]
        results["targets"][label] = {
            "best_model": best_name,
            "models": models,
            "best_auc_cv": best_auc,
        }
        if label == "win_probability":
            results["feature_importance"] = importance

    if not results["feature_importance"]:
        results["feature_importance"] = build_correlation_importance(rows)
    return results


def build_correlation_importance(rows: list) -> list[dict[str, Any]]:
    raw: list[tuple[str, float]] = []
    for col in FEATURE_COLUMNS:
        pairs = [
            (float(r[col]), float(r["pnl"]))
            for r in rows
            if r[col] is not None and r["pnl"] is not None
        ]
        if len(pairs) < 10:
            continue
        xs, ys = zip(*pairs)
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
        raw.append((col, abs(num / den) if den else 0.0))
    total = sum(v for _, v in raw) or 1.0
    return [
        {"feature": name, "importance_pct": round(100 * val / total, 1)}
        for name, val in sorted(raw, key=lambda x: x[1], reverse=True)
    ]
