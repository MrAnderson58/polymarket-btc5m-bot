"""SQLite store for Signal Evolution V1 rankings."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_signal_evolution_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal TEXT NOT NULL UNIQUE,
  score REAL,
  age REAL,
  half_life REAL,
  drift REAL,
  confidence REAL,
  status TEXT,
  metrics_json TEXT,
  regimes_json TEXT,
  decay_json TEXT,
  survival_json TEXT,
  drift_json TEXT,
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sig_evo_v1_score ON {LIBRARY_TABLE}(score DESC);
CREATE INDEX IF NOT EXISTS idx_sig_evo_v1_status ON {LIBRARY_TABLE}(status);
"""


def ensure_evolution_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_evolution(conn: Any, rows: list[dict[str, Any]], *, now: int | None = None) -> int:
    ensure_evolution_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in rows:
        sig = str(r.get("signal") or "")
        if not sig:
            continue
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              signal, score, age, half_life, drift, confidence, status,
              metrics_json, regimes_json, decay_json, survival_json, drift_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(signal) DO UPDATE SET
              score=excluded.score,
              age=excluded.age,
              half_life=excluded.half_life,
              drift=excluded.drift,
              confidence=excluded.confidence,
              status=excluded.status,
              metrics_json=excluded.metrics_json,
              regimes_json=excluded.regimes_json,
              decay_json=excluded.decay_json,
              survival_json=excluded.survival_json,
              drift_json=excluded.drift_json,
              updated_at=excluded.updated_at
            """,
            (
                sig,
                r.get("score"),
                r.get("age"),
                r.get("half_life"),
                r.get("drift"),
                r.get("confidence"),
                str(r.get("status") or ""),
                json.dumps(r.get("rolling") or r.get("metrics") or {}, default=str),
                json.dumps(r.get("regimes") or {}, default=str),
                json.dumps(r.get("decay") or {}, default=str),
                json.dumps(r.get("survival") or {}, default=str),
                json.dumps(r.get("drift_detail") or {}, default=str),
                now,
            ),
        )
        n += 1
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_evolution(conn: Any, *, limit: int = 500) -> list[dict[str, Any]]:
    ensure_evolution_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT signal, score, age, half_life, drift, confidence, status, updated_at
            FROM {LIBRARY_TABLE}
            ORDER BY score DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    except Exception:
        return []
    out = []
    for r in cur.fetchall():
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                "signal": r[0], "score": r[1], "age": r[2], "half_life": r[3],
                "drift": r[4], "confidence": r[5], "status": r[6], "updated_at": r[7],
            })
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_evolution_schema",
    "load_evolution",
    "upsert_evolution",
]
