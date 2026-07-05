"""Walk-forward MODEL A/B/C comparison — chronological, no random split."""

from __future__ import annotations

import json
import random
import sqlite3
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from bot.research.futures.config import BOOTSTRAP_SAMPLES, MIN_WALK_FORWARD_TRADES, WF_FOLDS, WF_TRAIN_RATIO
from bot.research.futures.schema import OUTCOMES_TABLE, SIGNALS_TABLE, SNAPSHOTS_TABLE


def _load_dataset(conn: sqlite3.Connection, *, horizon: str = "1h") -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.id, s.source, s.side, s.symbol, s.parser_confidence,
               s.entry_min, s.entry_max, s.stop_loss, s.leverage,
               o.direction_correct, o.pnl_standardized,
               snap.features_json
        FROM {SIGNALS_TABLE} s
        JOIN {OUTCOMES_TABLE} o ON o.signal_id = s.id AND o.horizon = ?
        LEFT JOIN {SNAPSHOTS_TABLE} snap ON snap.signal_id = s.id
        WHERE o.direction_correct IS NOT NULL
        ORDER BY s.timestamp ASC
        """,
        (horizon,),
    ).fetchall()

    data = []
    for r in rows:
        feats = json.loads(r["features_json"]) if r["features_json"] else {}
        data.append({
            "id": r["id"],
            "y": int(r["direction_correct"]),
            "pnl": float(r["pnl_standardized"] or 0),
            "side_long": 1 if r["side"] == "LONG" else 0,
            "parser_confidence": float(r["parser_confidence"] or 0),
            "has_entry": 1 if r["entry_min"] is not None else 0,
            "has_sl": 1 if r["stop_loss"] is not None else 0,
            "leverage": float(r["leverage"] or 0),
            "return_5m": _f(feats.get("return_5m")),
            "return_1h": _f(feats.get("return_1h")),
            "return_4h": _f(feats.get("return_4h")),
            "atr_pct": _f(feats.get("atr_pct")),
            "ema9_slope": _f(feats.get("ema9_slope")),
            "btc_return_1h": _f(feats.get("btc_return_1h")),
            "relative_strength_1h": _f(feats.get("relative_strength_1h")),
            "funding_rate": _f(feats.get("funding_rate")),
            "source_hash": hash(r["source"] or "") % 1000 / 1000.0,
        })
    return data


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


FEATURES_A = ["side_long", "parser_confidence", "has_entry", "has_sl", "leverage", "source_hash"]
FEATURES_B = [
    "return_5m", "return_1h", "return_4h", "atr_pct", "ema9_slope",
    "btc_return_1h", "relative_strength_1h", "funding_rate",
]
FEATURES_C = FEATURES_A + FEATURES_B


def _matrix(data: list[dict], cols: list[str]) -> np.ndarray:
    return np.array([[d[c] for c in cols] for d in data], dtype=float)


def _pf_from_preds(y_true: np.ndarray, pnl: np.ndarray, preds: np.ndarray) -> float:
    selected = pnl[preds == 1]
    if len(selected) == 0:
        return 0.0
    wins = selected[selected > 0].sum()
    losses = abs(selected[selected < 0].sum())
    return float(wins / losses) if losses > 0 else float(wins)


def walk_forward_abc(conn: sqlite3.Connection, *, horizon: str = "1h") -> dict[str, Any]:
    data = _load_dataset(conn, horizon=horizon)
    if len(data) < MIN_WALK_FORWARD_TRADES:
        return {
            "status": "insufficient_data",
            "n": len(data),
            "min_required": MIN_WALK_FORWARD_TRADES,
        }

    y_all = np.array([d["y"] for d in data])
    pnl_all = np.array([d["pnl"] for d in data])
    fold_size = max(1, len(data) // WF_FOLDS)
    models = {
        "MODEL_A_SIGNAL_ONLY": FEATURES_A,
        "MODEL_B_MARKET_ONLY": FEATURES_B,
        "MODEL_C_SIGNAL_PLUS_MARKET": FEATURES_C,
    }
    results: dict[str, Any] = {"horizon": horizon, "n": len(data), "models": {}}

    for name, cols in models.items():
        fold_metrics = []
        for fold in range(1, WF_FOLDS):
            test_start = fold * fold_size
            test_end = min((fold + 1) * fold_size, len(data))
            if test_start >= len(data):
                break
            train = data[:test_start] if test_start > 0 else data[:max(1, test_end // 2)]
            test = data[test_start:test_end]
            if len(train) < 20 or len(test) < 5:
                continue
            X_train = _matrix(train, cols)
            X_test = _matrix(test, cols)
            y_train = np.array([d["y"] for d in train])
            y_test = np.array([d["y"] for d in test])
            pnl_test = np.array([d["pnl"] for d in test])

            scaler = StandardScaler()
            X_train_s = scaler.fit_transform(X_train)
            X_test_s = scaler.transform(X_test)
            clf = LogisticRegression(max_iter=500, class_weight="balanced")
            clf.fit(X_train_s, y_train)
            prob = clf.predict_proba(X_test_s)[:, 1]
            pred = (prob >= 0.5).astype(int)
            auc = roc_auc_score(y_test, prob) if len(set(y_test)) > 1 else None
            fold_metrics.append({
                "precision": float((pred[y_test == 1] == 1).mean()) if (y_test == 1).any() else 0.0,
                "recall": float((pred[y_test == 1] == 1).sum() / max(1, (y_test == 1).sum())),
                "roc_auc": auc,
                "pf": _pf_from_preds(y_test, pnl_test, pred),
                "avg_pnl": float(pnl_test[pred == 1].mean()) if (pred == 1).any() else 0.0,
            })

        if not fold_metrics:
            continue
        avg = {k: sum(f[k] or 0 for f in fold_metrics) / len(fold_metrics) for k in fold_metrics[0]}
        avg["bootstrap_pf_ci"] = _bootstrap_pf(data, cols)
        results["models"][name] = avg

    if "MODEL_B_MARKET_ONLY" in results["models"] and "MODEL_C_SIGNAL_PLUS_MARKET" in results["models"]:
        b_pf = results["models"]["MODEL_B_MARKET_ONLY"]["pf"]
        c_pf = results["models"]["MODEL_C_SIGNAL_PLUS_MARKET"]["pf"]
        results["c_beats_b"] = c_pf > b_pf
        results["primary_question"] = "MODEL_C adds OOS value beyond MODEL_B" if c_pf > b_pf else "MODEL_C does not beat MODEL_B"
    return results


def _bootstrap_pf(data: list[dict], cols: list[str], *, samples: int = BOOTSTRAP_SAMPLES) -> tuple[float, float]:
    pnls = [d["pnl"] for d in data]
    if not pnls:
        return 0.0, 0.0
    rng = random.Random(42)
    pfs = []
    for _ in range(min(samples, 500)):
        sample = [pnls[rng.randrange(len(pnls))] for _ in range(len(pnls))]
        wins = sum(x for x in sample if x > 0)
        losses = abs(sum(x for x in sample if x < 0))
        pfs.append(wins / losses if losses else wins)
    pfs.sort()
    lo = pfs[int(len(pfs) * 0.05)]
    hi = pfs[int(len(pfs) * 0.95)]
    return float(lo), float(hi)
