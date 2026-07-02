"""Knowledge Engine — causal links between features and outcomes."""

from __future__ import annotations

import json
import sqlite3
import statistics
from typing import Any


def _metrics(pnls: list[float]) -> dict[str, float]:
    if not pnls:
        return {"n": 0, "avg_pnl": 0.0, "win_rate": 0.0}
    wins = [p for p in pnls if p > 0]
    return {
        "n": len(pnls),
        "avg_pnl": statistics.mean(pnls),
        "win_rate": len(wins) / len(pnls),
    }


def _confidence(n: int, effect: float) -> float:
    base = min(90.0, 40.0 + n * 2.5)
    if abs(effect) > 3:
        base += 10
    return min(99.0, base)


def discover_causal_knowledge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule-based causal discovery from feature rows with outcomes."""
    if len(rows) < 10:
        return []

    all_pnls = [float(r.get("pnl") or 0) for r in rows]
    baseline_avg = statistics.mean(all_pnls)
    links: list[dict[str, Any]] = []

    def _bucket_link(
        feature: str,
        condition: str,
        subset: list[dict[str, Any]],
    ) -> None:
        if len(subset) < 8:
            return
        pnls = [float(r.get("pnl") or 0) for r in subset]
        m = _metrics(pnls)
        effect = m["avg_pnl"] - baseline_avg
        direction = "positive" if effect > 0.5 else "negative" if effect < -0.5 else "neutral"
        if direction == "neutral":
            return
        links.append(
            {
                "feature": feature,
                "condition_text": condition,
                "outcome_metric": "avg_pnl",
                "effect_value": round(effect, 2),
                "causal_direction": direction,
                "confidence": _confidence(len(subset), effect),
                "sample_n": len(subset),
            }
        )

    for regime in {r.get("market_regime") or r.get("regime_label") for r in rows}:
        if not regime:
            continue
        subset = [r for r in rows if (r.get("market_regime") or r.get("regime_label")) == regime]
        _bucket_link("market_regime", f"regime={regime}", subset)

    for entry in sorted({round(float(r.get("entry_price", 0)), 2) for r in rows}):
        subset = [r for r in rows if abs(float(r.get("entry_price", 0)) - entry) < 0.006]
        _bucket_link("entry_price", f"entry={entry:.2f}", subset)

    for direction in ("up", "down", "flat"):
        subset = [r for r in rows if r.get("btc_direction") == direction]
        _bucket_link("btc_direction", f"btc_{direction}", subset)

    high_spread = [r for r in rows if (r.get("spread") or 0) > 0.025]
    _bucket_link("spread", "spread>0.025", high_spread)
    low_spread = [r for r in rows if r.get("spread") is not None and r["spread"] <= 0.015]
    _bucket_link("spread", "spread<=0.015", low_spread)

    for label, lo, hi in (("btc_high", 15, 999), ("btc_low", -999, -5)):
        subset = [
            r
            for r in rows
            if r.get("btc_move_30s") is not None and lo <= float(r["btc_move_30s"]) <= hi
        ]
        _bucket_link("btc_move_30s", label, subset)

    return sorted(links, key=lambda x: x["confidence"], reverse=True)


def persist_knowledge(conn: sqlite3.Connection, links: list[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM brain_knowledge")
    for link in links:
        conn.execute(
            """
            INSERT INTO brain_knowledge (
                feature, condition_text, outcome_metric, effect_value,
                causal_direction, confidence, sample_n
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link["feature"],
                link["condition_text"],
                link["outcome_metric"],
                link["effect_value"],
                link["causal_direction"],
                link["confidence"],
                link["sample_n"],
            ),
        )
    return len(links)


def load_knowledge(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM brain_knowledge
        ORDER BY confidence DESC, sample_n DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def match_knowledge_for_signal(
    conn: sqlite3.Connection,
    features: dict[str, Any],
    *,
    cached: list | None = None,
) -> list[dict[str, Any]]:
    """Return knowledge links relevant to a signal's features."""
    rows = cached if cached is not None else load_knowledge(conn, limit=100)
    matched: list[dict[str, Any]] = []
    regime = features.get("market_regime") or features.get("regime_label")
    entry = round(float(features.get("entry_price", 0)), 2)
    direction = features.get("btc_direction", "flat")
    spread = features.get("spread")

    for row in rows:
        cond = row["condition_text"]
        hit = False
        if cond == f"regime={regime}":
            hit = True
        elif cond == f"entry={entry:.2f}":
            hit = True
        elif cond == f"btc_{direction}":
            hit = True
        elif cond == "spread>0.025" and spread is not None and spread > 0.025:
            hit = True
        elif cond == "spread<=0.015" and spread is not None and spread <= 0.015:
            hit = True
        elif cond == "btc_high" and (features.get("btc_move_30s") or 0) > 15:
            hit = True
        elif cond == "btc_low" and (features.get("btc_move_30s") or 0) < -5:
            hit = True
        if hit:
            matched.append(
                {
                    "feature": row["feature"],
                    "condition": row["condition_text"],
                    "effect": row["effect_value"],
                    "direction": row["causal_direction"],
                    "confidence": row["confidence"],
                }
            )
    return matched[:8]
