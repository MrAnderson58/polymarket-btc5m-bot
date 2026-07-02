"""Counterfactual Engine — evaluate ALLOW/SKIP vs actual outcomes."""

from __future__ import annotations

import sqlite3
from typing import Any


def classify_counterfactual(decision: str, pnl: float) -> str:
    if decision == "ALLOW":
        return "true_allow" if pnl > 0 else "false_allow"
    if decision == "SKIP":
        return "true_skip" if pnl <= 0 else "false_skip"
    return "shadow"


def build_counterfactual_matrix(rows: list[sqlite3.Row]) -> dict[str, Any]:
    counts = {
        "true_allow": 0,
        "false_allow": 0,
        "true_skip": 0,
        "false_skip": 0,
        "shadow": 0,
    }
    tp = fp = tn = fn = 0

    for row in rows:
        decision = row["decision"]
        pnl = float(row["pnl"] or 0)
        kind = classify_counterfactual(decision, pnl)
        counts[kind] += 1

        if decision == "ALLOW":
            if pnl > 0:
                tp += 1
            else:
                fp += 1
        elif decision == "SKIP":
            if pnl <= 0:
                tn += 1
            else:
                fn += 1

    total_labeled = tp + fp + tn + fn
    false_allow_pct = counts["false_allow"] / max(1, counts["true_allow"] + counts["false_allow"])
    false_skip_pct = counts["false_skip"] / max(1, counts["true_skip"] + counts["false_skip"])

    return {
        "counts": counts,
        "matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "false_allow_pct": round(false_allow_pct, 3),
        "false_skip_pct": round(false_skip_pct, 3),
        "accuracy": round((tp + tn) / total_labeled, 3) if total_labeled else 0.0,
    }
