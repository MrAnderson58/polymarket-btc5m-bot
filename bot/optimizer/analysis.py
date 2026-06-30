"""Cluster analysis and rule discovery (Blocks 5-6)."""

from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

from bot.optimizer.constants import FEATURE_COLUMNS


def _matrix(rows: list) -> tuple[np.ndarray, list]:
    complete = []
    matrix = []
    for row in rows:
        vals = []
        ok = True
        for col in FEATURE_COLUMNS:
            if row[col] is None:
                ok = False
                break
            vals.append(float(row[col]))
        if ok:
            complete.append(row)
            matrix.append(vals)
    return np.array(matrix, dtype=float), complete


def run_cluster_analysis(rows: list, *, k: int = 3) -> dict[str, Any]:
    x, complete = _matrix(rows)
    if len(complete) < k * 10:
        return {"status": "insufficient_data", "clusters": []}

    scaled = StandardScaler().fit_transform(x)
    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(scaled)

    clusters: list[dict[str, Any]] = []
    for cluster_id in range(k):
        members = [complete[i] for i, lab in enumerate(labels) if lab == cluster_id]
        pnls = [float(m["pnl"]) for m in members]
        wins = sum(1 for p in pnls if p > 0)
        gross_w = sum(p for p in pnls if p > 0)
        gross_l = abs(sum(p for p in pnls if p <= 0))
        pf = gross_w / gross_l if gross_l else float("inf")
        clusters.append(
            {
                "cluster_id": cluster_id + 1,
                "trades": len(members),
                "win_rate": wins / len(members),
                "profit_factor": pf,
                "avg_pnl": statistics.mean(pnls),
                "label": _cluster_label(wins / len(members), pf),
            }
        )

    diffs = _cluster_feature_diffs(x, labels, complete, k)
    return {"clusters": clusters, "feature_differences": diffs}


def _cluster_label(wr: float, pf: float) -> str:
    if wr >= 0.65 and pf >= 2.0:
        return "ideal trades"
    if wr <= 0.35 or pf < 0.8:
        return "poor trades"
    return "mixed trades"


def _cluster_feature_diffs(
    x: np.ndarray,
    labels: np.ndarray,
    complete: list,
    k: int,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    means = []
    for cid in range(k):
        mask = labels == cid
        if not mask.any():
            means.append(np.zeros(x.shape[1]))
        else:
            means.append(x[mask].mean(axis=0))
    best = max(
        range(k),
        key=lambda c: statistics.mean(
            float(complete[i]["pnl"]) for i, lab in enumerate(labels) if lab == c
        )
        if any(labels == c)
        else -999,
    )
    for i, col in enumerate(FEATURE_COLUMNS):
        spread = max(m[i] for m in means) - min(m[i] for m in means)
        if spread > 0:
            diffs.append(
                {
                    "feature": col,
                    "spread": round(float(spread), 4),
                    "best_cluster": best + 1,
                }
            )
    return sorted(diffs, key=lambda d: d["spread"], reverse=True)[:8]


def discover_rules(rows: list, *, target: str = "is_stop") -> list[dict[str, Any]]:
    x, complete = _matrix(rows)
    if len(complete) < 80:
        return []
    y = np.array([int(r[target]) for r in complete])
    if len(set(y.tolist())) < 2:
        return []

    tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=max(10, len(complete) // 30))
    tree.fit(x, y)
    text = export_text(tree, feature_names=list(FEATURE_COLUMNS))
    rules: list[dict[str, Any]] = []
    for line in text.splitlines():
        if "class:" in line and ("|---" in line or "<=" in line or ">" in line):
            continue
        if "class:" in line:
            parts = line.strip().split("class:")
            if len(parts) < 2:
                continue
            rate = _parse_class_rate(parts[1])
            rules.append(
                {
                    "rule_text": line.strip(),
                    "target": target,
                    "rate": rate,
                }
            )

    # Simpler single-feature rules
    for col in FEATURE_COLUMNS[:6]:
        vals = [float(r[col]) for r in complete]
        median = statistics.median(vals)
        high = [r for r in complete if float(r[col]) >= median]
        low = [r for r in complete if float(r[col]) < median]
        for subset, op in ((high, ">="), (low, "<")):
            if len(subset) < 15:
                continue
            rate = sum(int(r[target]) for r in subset) / len(subset)
            if rate >= 0.7 or rate <= 0.3:
                rules.append(
                    {
                        "rule_text": f"if {col} {op} {median:.3f} → {target} rate {rate:.0%}",
                        "target": target,
                        "rate": rate,
                        "support": len(subset),
                    }
                )
    rules.sort(key=lambda r: abs(r.get("rate", 0.5) - 0.5), reverse=True)
    return rules[:10]


def _parse_class_rate(class_text: str) -> float:
    try:
        if "1" in class_text:
            return float(class_text.split()[-1].replace("]", ""))
    except (ValueError, IndexError):
        pass
    return 0.5
