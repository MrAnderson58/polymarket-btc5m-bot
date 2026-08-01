"""SQLite Replay Library V1."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_trade_replays_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  trade_id INTEGER PRIMARY KEY,
  timeline_json TEXT,
  market_frames TEXT,
  events TEXT,
  features TEXT,
  future_path TEXT,
  regime TEXT,
  similar_ids TEXT,
  quality REAL,
  liquidity_json TEXT,
  missing_json TEXT,
  created_at INTEGER,
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_replay_v1_quality
  ON {LIBRARY_TABLE}(quality DESC);
CREATE INDEX IF NOT EXISTS idx_replay_v1_regime
  ON {LIBRARY_TABLE}(regime);
"""


def ensure_replay_library_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_replays(conn: Any, replays: list[dict[str, Any]], *, now: int | None = None) -> int:
    ensure_replay_library_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in replays:
        tid = int(r.get("trade_id") or 0)
        if not tid:
            continue
        params = (
            tid,
            json.dumps(r.get("timeline") or {}, default=str),
            json.dumps(r.get("market_frames") or [], default=str),
            json.dumps(r.get("events") or [], default=str),
            json.dumps(r.get("features") or {}, default=str),
            json.dumps(r.get("future_path") or [], default=str),
            str(r.get("regime") or ""),
            json.dumps(r.get("similar_ids") or [], default=str),
            float(r.get("quality") or 0.0),
            json.dumps(r.get("liquidity") or {}, default=str),
            json.dumps(r.get("missing_report") or {}, default=str),
            now,
            now,
        )
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              trade_id, timeline_json, market_frames, events, features,
              future_path, regime, similar_ids, quality, liquidity_json,
              missing_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_id) DO UPDATE SET
              timeline_json=excluded.timeline_json,
              market_frames=excluded.market_frames,
              events=excluded.events,
              features=excluded.features,
              future_path=excluded.future_path,
              regime=excluded.regime,
              similar_ids=excluded.similar_ids,
              quality=excluded.quality,
              liquidity_json=excluded.liquidity_json,
              missing_json=excluded.missing_json,
              updated_at=excluded.updated_at
            """,
            params,
        )
        n += 1
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_replays(conn: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    ensure_replay_library_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT trade_id, regime, quality, similar_ids, missing_json, updated_at
            FROM {LIBRARY_TABLE}
            ORDER BY quality DESC
            LIMIT ?
            """,
            (limit,),
        )
    except Exception:
        return []
    out = []
    for r in cur.fetchall():
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                "trade_id": r[0], "regime": r[1], "quality": r[2],
                "similar_ids": r[3], "missing_json": r[4], "updated_at": r[5],
            })
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_replay_library_schema",
    "load_replays",
    "upsert_replays",
]
