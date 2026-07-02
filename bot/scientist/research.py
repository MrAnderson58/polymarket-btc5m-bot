"""Research Engine — discover and persist market patterns."""

from __future__ import annotations

import json
import sqlite3
import statistics
from typing import Any

from bot.report.analytics import _metrics
from bot.scientist.dataset import load_enriched_trades


def _persist_pattern(conn: sqlite3.Connection, pattern: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO scientist_patterns (
            pattern_type, description, feature, effect_json, sample_n, confidence
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(pattern_type, description) DO UPDATE SET
            effect_json = excluded.effect_json,
            sample_n = excluded.sample_n,
            confidence = excluded.confidence,
            created_at = datetime('now')
        """,
        (
            pattern["pattern_type"],
            pattern["description"],
            pattern.get("feature"),
            json.dumps(pattern.get("effect", {}), ensure_ascii=False),
            pattern["sample_n"],
            pattern["confidence"],
        ),
    )


def _compare_features(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []

    def _effect(feature: str, split_fn) -> float | None:
        a, b = [], []
        for r in rows:
            pnl = float(r.get("pnl") or 0)
            if split_fn(r):
                a.append(pnl)
            else:
                b.append(pnl)
        if len(a) < 8 or len(b) < 8:
            return None
        return statistics.mean(a) - statistics.mean(b)

    accel_effect = _effect(
        "btc_acceleration",
        lambda r: abs(float(r.get("btc_acceleration") or 0)) >= 8,
    )
    spread_effect = _effect(
        "spread",
        lambda r: (r.get("spread") or 0) > 0.02,
    )
    if accel_effect is not None and spread_effect is not None:
        winner = "btc_acceleration" if abs(accel_effect) > abs(spread_effect) else "spread"
        patterns.append(
            {
                "pattern_type": "feature_importance",
                "feature": winner,
                "description": f"{winner} explains PnL better than the other feature",
                "effect": {
                    "btc_acceleration_delta": round(accel_effect, 2),
                    "spread_delta": round(spread_effect, 2),
                },
                "sample_n": len(rows),
                "confidence": 70.0 if abs(accel_effect - spread_effect) > 1 else 55.0,
            }
        )

    for entry in sorted({round(float(r.get("entry_price", 0)), 2) for r in rows}):
        regime_rows = [
            r
            for r in rows
            if abs(float(r.get("entry_price", 0)) - entry) < 0.006
        ]
        low_vol = [r for r in regime_rows if (r.get("volatility") or 99) < 0.02]
        if len(low_vol) < 8:
            continue
        m = _metrics([float(r.get("pnl") or 0) for r in low_vol])
        all_m = _metrics([float(r.get("pnl") or 0) for r in regime_rows])
        if m["avg_pnl"] > all_m["avg_pnl"] + 1:
            patterns.append(
                {
                    "pattern_type": "regime_entry",
                    "feature": "entry_price",
                    "description": f"Entry {entry:.2f} performs better in Low Volatility",
                    "effect": {
                        "low_vol_avg": round(m["avg_pnl"], 2),
                        "overall_avg": round(all_m["avg_pnl"], 2),
                    },
                    "sample_n": len(low_vol),
                    "confidence": min(90.0, 50 + len(low_vol)),
                }
            )

    trailing = [r for r in rows if (r.get("exit_reason") or "").upper() == "TRAILING_STOP"]
    impulse = [
        r for r in trailing if abs(float(r.get("btc_move_30s") or 0)) >= 20
    ]
    if len(impulse) >= 8:
        m = _metrics([float(r.get("pnl") or 0) for r in impulse])
        if m["avg_pnl"] < 0:
            patterns.append(
                {
                    "pattern_type": "exit_quality",
                    "feature": "trailing_stop",
                    "description": "Trailing Stop underperforms after strong BTC impulse",
                    "effect": {"avg_pnl": round(m["avg_pnl"], 2), "trades": len(impulse)},
                    "sample_n": len(impulse),
                    "confidence": min(88.0, 45 + len(impulse)),
                }
            )

    return patterns


def discover_patterns(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = load_enriched_trades(conn)
    if len(rows) < 10:
        return []
    patterns = _compare_features(rows)
    for p in patterns:
        _persist_pattern(conn, p)
    return patterns


def load_patterns(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM scientist_patterns
        ORDER BY confidence DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["effect"] = json.loads(d.pop("effect_json") or "{}")
        out.append(d)
    return out
