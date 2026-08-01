"""SQLite Causal Library V1."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_trade_causality_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  trade_id INTEGER PRIMARY KEY,
  primary_cause TEXT,
  secondary_cause TEXT,
  contributions_json TEXT,
  counterfactual_json TEXT,
  causal_cluster TEXT,
  confidence REAL,
  quality REAL,
  created_at INTEGER,
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_causality_v1_cluster
  ON {LIBRARY_TABLE}(causal_cluster);
CREATE INDEX IF NOT EXISTS idx_causality_v1_primary
  ON {LIBRARY_TABLE}(primary_cause);
"""


def ensure_causality_library_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_causality(conn: Any, rows: list[dict[str, Any]], *, now: int | None = None) -> int:
    ensure_causality_library_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        if not tid:
            continue
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              trade_id, primary_cause, secondary_cause, contributions_json,
              counterfactual_json, causal_cluster, confidence, quality,
              created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_id) DO UPDATE SET
              primary_cause=excluded.primary_cause,
              secondary_cause=excluded.secondary_cause,
              contributions_json=excluded.contributions_json,
              counterfactual_json=excluded.counterfactual_json,
              causal_cluster=excluded.causal_cluster,
              confidence=excluded.confidence,
              quality=excluded.quality,
              updated_at=excluded.updated_at
            """,
            (
                tid,
                str(r.get("primary_cause") or ""),
                str(r.get("secondary_cause") or ""),
                json.dumps(r.get("contributions") or {}, default=str),
                json.dumps(r.get("counterfactual") or {}, default=str),
                str(r.get("causal_cluster") or ""),
                float(r.get("confidence") or 0.0),
                float(r.get("quality") or 0.0),
                now,
                now,
            ),
        )
        n += 1
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_causality(conn: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    ensure_causality_library_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT trade_id, primary_cause, secondary_cause, causal_cluster,
                   confidence, quality, updated_at
            FROM {LIBRARY_TABLE}
            ORDER BY confidence DESC
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
                "trade_id": r[0], "primary_cause": r[1], "secondary_cause": r[2],
                "causal_cluster": r[3], "confidence": r[4], "quality": r[5],
                "updated_at": r[6],
            })
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_causality_library_schema",
    "load_causality",
    "upsert_causality",
]
