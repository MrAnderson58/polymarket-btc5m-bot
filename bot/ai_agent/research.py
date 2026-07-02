"""Pattern discovery over ai_features history."""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any


def _metrics(pnls: list[float]) -> dict[str, Any]:
    if not pnls:
        return {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0, "profit_factor": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tp = sum(wins)
    tl = abs(sum(losses))
    return {
        "trades": len(pnls),
        "win_rate": len(wins) / len(pnls),
        "avg_pnl": statistics.mean(pnls),
        "profit_factor": tp / tl if tl else float("inf"),
    }


def discover_patterns(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    if len(rows) < 10:
        return patterns

    by_regime: dict[str, list[float]] = {}
    by_entry: dict[float, list[float]] = {}
    by_btc: dict[str, list[float]] = {}

    for row in rows:
        pnl = float(row["pnl"] or 0)
        regime = row["regime_label"] or "Unknown"
        by_regime.setdefault(regime, []).append(pnl)
        entry = round(float(row["entry_price"]), 2)
        by_entry.setdefault(entry, []).append(pnl)
        move = row["btc_move_30s"]
        bucket = "btc_up" if move is not None and move > 10 else "btc_down" if move is not None and move < -5 else "btc_flat"
        by_btc.setdefault(bucket, []).append(pnl)

    for regime, pnls in by_regime.items():
        m = _metrics(pnls)
        if m["trades"] >= 8 and m["avg_pnl"] < -1.0:
            patterns.append(
                {
                    "pattern": f"Regime {regime} underperforms",
                    "trades": m["trades"],
                    "win_rate": m["win_rate"],
                    "avg_pnl": round(m["avg_pnl"], 2),
                    "confidence_pct": min(95, 60 + m["trades"]),
                }
            )

    for entry, pnls in by_entry.items():
        m = _metrics(pnls)
        if m["trades"] >= 10 and m["profit_factor"] >= 2.0:
            patterns.append(
                {
                    "pattern": f"Entry {entry:.2f} strong edge",
                    "trades": m["trades"],
                    "win_rate": m["win_rate"],
                    "avg_pnl": round(m["avg_pnl"], 2),
                    "confidence_pct": min(94, 55 + m["trades"]),
                }
            )

    for bucket, pnls in by_btc.items():
        m = _metrics(pnls)
        if m["trades"] >= 10 and m["avg_pnl"] < -0.5:
            patterns.append(
                {
                    "pattern": f"BTC context {bucket} weak",
                    "trades": m["trades"],
                    "win_rate": m["win_rate"],
                    "avg_pnl": round(m["avg_pnl"], 2),
                    "confidence_pct": 75,
                }
            )

    skip_rows = [r for r in rows if r["decision"] == "SKIP"]
    allow_rows = [r for r in rows if r["decision"] == "ALLOW"]
    if skip_rows and allow_rows:
        skip_avg = statistics.mean(float(r["pnl"] or 0) for r in skip_rows)
        allow_avg = statistics.mean(float(r["pnl"] or 0) for r in allow_rows)
        if skip_avg < allow_avg - 1.5:
            patterns.append(
                {
                    "pattern": "Agent SKIP beats ALLOW avg PnL (counterfactual)",
                    "trades": len(skip_rows),
                    "win_rate": 0.0,
                    "avg_pnl": round(skip_avg - allow_avg, 2),
                    "confidence_pct": 80,
                }
            )

    return sorted(patterns, key=lambda p: p["confidence_pct"], reverse=True)[:8]


def shadow_experiment_recommendations(
    patterns: list[dict[str, Any]],
) -> list[str]:
    recs: list[str] = []
    for p in patterns[:5]:
        text = p["pattern"]
        if "Regime" in text and "underperforms" in text:
            regime = text.replace("Regime ", "").replace(" underperforms", "")
            recs.append(f"Shadow: block entries in {regime} regime and replay 200 trades")
        elif "Entry" in text and "strong" in text:
            recs.append(f"Shadow: tighten entry toward pattern — {text}")
        elif "BTC context" in text:
            recs.append(f"Shadow: BTC filter experiment for {text}")
        elif "SKIP beats" in text:
            recs.append("Shadow: simulate full SKIP gate offline; do not enable live")
        else:
            recs.append(f"Shadow: validate — {text}")
    if not recs:
        recs.append("Continue observe-only accumulation; no shadow experiment ready yet")
    return recs
