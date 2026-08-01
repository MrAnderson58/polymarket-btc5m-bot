"""SQLite journal for Shadow Live Evaluation V1."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_shadow_decisions_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER,
  trade_id INTEGER,
  ts INTEGER,
  symbol TEXT,
  production_action TEXT,
  brain_action TEXT,
  brain_probability REAL,
  brain_confidence REAL,
  expected_ev REAL,
  expected_pf REAL,
  explanation TEXT,
  conflict TEXT,
  conflict_score REAL,
  pnl REAL,
  evaluated INTEGER DEFAULT 0,
  production_hit INTEGER,
  brain_hit INTEGER,
  winner TEXT,
  created_at INTEGER,
  updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shadow_live_v1_ts ON {LIBRARY_TABLE}(ts DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_live_v1_trade ON {LIBRARY_TABLE}(trade_id);
CREATE INDEX IF NOT EXISTS idx_shadow_live_v1_eval ON {LIBRARY_TABLE}(evaluated, ts DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_live_v1_symbol ON {LIBRARY_TABLE}(symbol);
"""


def ensure_shadow_library_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_shadow_decisions(conn: Any, rows: list[dict[str, Any]], *, now: int | None = None) -> int:
    """Upsert shadow journal rows keyed by trade_id (fallback candidate_id)."""
    ensure_shadow_library_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in rows:
        tid = r.get("trade_id")
        cid = r.get("candidate_id")
        if tid is not None:
            conn.execute(f"DELETE FROM {LIBRARY_TABLE} WHERE trade_id = ?", (int(tid),))
        elif cid is not None:
            conn.execute(f"DELETE FROM {LIBRARY_TABLE} WHERE candidate_id = ? AND trade_id IS NULL", (int(cid),))
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              candidate_id, trade_id, ts, symbol, production_action, brain_action,
              brain_probability, brain_confidence, expected_ev, expected_pf,
              explanation, conflict, conflict_score, pnl, evaluated,
              production_hit, brain_hit, winner, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(cid) if cid is not None else None,
                int(tid) if tid is not None else None,
                int(r.get("ts") or now),
                str(r.get("symbol") or ""),
                str(r.get("production_action") or ""),
                str(r.get("brain_action") or ""),
                r.get("brain_probability"),
                r.get("brain_confidence"),
                r.get("expected_ev"),
                r.get("expected_pf"),
                str(r.get("explanation") or ""),
                str(r.get("conflict") or ""),
                r.get("conflict_score"),
                r.get("pnl"),
                1 if r.get("evaluated") else 0,
                r.get("production_hit"),
                r.get("brain_hit"),
                r.get("winner"),
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


def load_shadow_decisions(conn: Any, *, limit: int = 5000) -> list[dict[str, Any]]:
    ensure_shadow_library_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT candidate_id, trade_id, ts, symbol, production_action, brain_action,
                   brain_probability, brain_confidence, expected_ev, expected_pf,
                   explanation, conflict, conflict_score, pnl, evaluated,
                   production_hit, brain_hit, winner, created_at, updated_at
            FROM {LIBRARY_TABLE}
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in cur.fetchall():
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                "candidate_id": r[0], "trade_id": r[1], "ts": r[2], "symbol": r[3],
                "production_action": r[4], "brain_action": r[5],
                "brain_probability": r[6], "brain_confidence": r[7],
                "expected_ev": r[8], "expected_pf": r[9],
                "explanation": r[10], "conflict": r[11], "conflict_score": r[12],
                "pnl": r[13], "evaluated": r[14],
                "production_hit": r[15], "brain_hit": r[16], "winner": r[17],
                "created_at": r[18], "updated_at": r[19],
            })
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_shadow_library_schema",
    "load_shadow_decisions",
    "upsert_shadow_decisions",
]
