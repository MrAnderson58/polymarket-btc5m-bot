"""Interpretable conditional pattern research — train discover, OOS confirm."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bot.research.futures.config import MIN_SOURCE_N
from bot.research.futures.schema import OUTCOMES_TABLE, SIGNALS_TABLE, SNAPSHOTS_TABLE


PATTERN_DEFS = [
    {
        "id": "short_btc_down_oi_unavailable",
        "label": "SHORT + BTC 1h DOWN",
        "predicate": lambda r, f: r["side"] == "SHORT" and (f.get("btc_return_1h") or 0) < -0.1,
    },
    {
        "id": "long_after_vol_expansion",
        "label": "LONG + ATR pct elevated",
        "predicate": lambda r, f: r["side"] == "LONG" and (f.get("atr_pct") or 0) > 0.5,
    },
    {
        "id": "signal_aligned_15m",
        "label": "Side aligned with 15m momentum",
        "predicate": lambda r, f: (
            (r["side"] == "LONG" and (f.get("return_15m") or 0) > 0)
            or (r["side"] == "SHORT" and (f.get("return_15m") or 0) < 0)
        ),
    },
    {
        "id": "countertrend_long_bear",
        "label": "LONG counter to bear regime",
        "predicate": lambda r, f: r["side"] == "LONG" and f.get("market_regime") == "bear",
    },
    {
        "id": "short_aligned_bear",
        "label": "SHORT aligned with bear regime",
        "predicate": lambda r, f: r["side"] == "SHORT" and f.get("market_regime") == "bear",
    },
]


def discover_patterns(conn: sqlite3.Connection, *, horizon: str = "1h", train_ratio: float = 0.7) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.side, s.source, o.direction_correct, o.pnl_standardized, snap.features_json
        FROM {SIGNALS_TABLE} s
        JOIN {OUTCOMES_TABLE} o ON o.signal_id = s.id AND o.horizon = ?
        LEFT JOIN {SNAPSHOTS_TABLE} snap ON snap.signal_id = s.id
        WHERE o.pnl_standardized IS NOT NULL
        ORDER BY s.timestamp ASC
        """,
        (horizon,),
    ).fetchall()
    if len(rows) < MIN_SOURCE_N:
        return []

    split = int(len(rows) * train_ratio)
    train, test = rows[:split], rows[split:]
    confirmed = []

    for pat in PATTERN_DEFS:
        train_hits = [_eval_row(r, pat["predicate"]) for r in train]
        train_rows = [r for r, hit in zip(train, train_hits) if hit]
        if len(train_rows) < 10:
            continue
        train_wr = sum(int(r["direction_correct"]) for r in train_rows) / len(train_rows)
        train_avg = sum(float(r["pnl_standardized"]) for r in train_rows) / len(train_rows)
        if train_wr < 0.52 and train_avg <= 0:
            continue

        test_hits = [_eval_row(r, pat["predicate"]) for r in test]
        test_rows = [r for r, hit in zip(test, test_hits) if hit]
        if len(test_rows) < 5:
            continue
        test_wr = sum(int(r["direction_correct"]) for r in test_rows) / len(test_rows)
        test_avg = sum(float(r["pnl_standardized"]) for r in test_rows) / len(test_rows)
        confirmed.append({
            "id": pat["id"],
            "label": pat["label"],
            "train_n": len(train_rows),
            "train_wr": train_wr,
            "train_avg_pnl": train_avg,
            "oos_n": len(test_rows),
            "oos_wr": test_wr,
            "oos_avg_pnl": test_avg,
            "oos_confirmed": test_wr >= 0.5 and test_avg > 0,
        })
    return confirmed


def _eval_row(row: sqlite3.Row, predicate: Any) -> bool:
    feats = json.loads(row["features_json"]) if row["features_json"] else {}
    try:
        return bool(predicate(row, feats))
    except Exception:
        return False
