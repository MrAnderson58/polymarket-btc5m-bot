"""Chronological train/validation/OOS split persistence."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.historical_replay.constants import (
    SPLIT_HOLDOUT_RATIO,
    SPLIT_TRAIN_RATIO,
    SPLIT_VAL_RATIO,
)


def compute_split_boundaries(event_timestamps: list[int]) -> dict[str, int | str]:
    if len(event_timestamps) < 10:
        return {"verdict": "INSUFFICIENT_DATA", "n": len(event_timestamps)}
    ts = sorted(event_timestamps)
    n = len(ts)
    train_end_idx = max(1, int(n * SPLIT_TRAIN_RATIO)) - 1
    val_end_idx = max(train_end_idx + 1, int(n * (SPLIT_TRAIN_RATIO + SPLIT_VAL_RATIO)) - 1)
    return {
        "verdict": "OK",
        "n": n,
        "train_end_ts": ts[train_end_idx],
        "validation_end_ts": ts[val_end_idx],
        "holdout_end_ts": ts[-1],
    }


def persist_replay_split(conn: Any, *, run_tag: str, event_timestamps: list[int]) -> dict[str, Any]:
    bounds = compute_split_boundaries(event_timestamps)
    if bounds.get("verdict") != "OK":
        return bounds
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_replay_splits (
          run_tag, train_end_ts, validation_end_ts, holdout_end_ts, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            run_tag,
            bounds["train_end_ts"],
            bounds["validation_end_ts"],
            bounds["holdout_end_ts"],
            now,
        ),
    )
    return bounds


def split_bucket_for_ts(conn: Any, *, run_tag: str, event_ts: int) -> str:
    row = conn.execute(
        "SELECT train_end_ts, validation_end_ts FROM market_events_replay_splits WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if not row:
        return "unsplit"
    if event_ts <= int(row["train_end_ts"]):
        return "train"
    if event_ts <= int(row["validation_end_ts"]):
        return "validation"
    return "holdout"


def split_report(conn: Any, *, run_tag: str) -> str:
    row = conn.execute(
        "SELECT * FROM market_events_replay_splits WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if not row:
        return f"No split persisted for run_tag={run_tag}"
    return json.dumps(dict(row), indent=2)
