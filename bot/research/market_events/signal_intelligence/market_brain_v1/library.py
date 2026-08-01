"""SQLite Brain decisions library."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_brain_decisions_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id INTEGER,
  decision TEXT,
  direction TEXT,
  probability_buy REAL,
  expected_ev REAL,
  expected_pf REAL,
  risk TEXT,
  confidence_raw REAL,
  confidence_calibrated REAL,
  conflict_level TEXT,
  conflict_score REAL,
  votes_json TEXT,
  opinions_json TEXT,
  explanation_json TEXT,
  module_weights_json TEXT,
  created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_brain_v1_trade ON {LIBRARY_TABLE}(trade_id);
CREATE INDEX IF NOT EXISTS idx_brain_v1_decision ON {LIBRARY_TABLE}(decision);
"""


def ensure_brain_library_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_decisions(conn: Any, rows: list[dict[str, Any]], *, now: int | None = None) -> int:
    """Insert decision rows (append-friendly; deletes prior trade_id then insert)."""
    ensure_brain_library_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in rows:
        tid = r.get("trade_id")
        if tid is not None:
            try:
                conn.execute(f"DELETE FROM {LIBRARY_TABLE} WHERE trade_id = ?", (int(tid),))
            except Exception:
                pass
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              trade_id, decision, direction, probability_buy, expected_ev, expected_pf,
              risk, confidence_raw, confidence_calibrated, conflict_level, conflict_score,
              votes_json, opinions_json, explanation_json, module_weights_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(tid) if tid is not None else None,
                str(r.get("decision") or ""),
                str(r.get("direction") or ""),
                r.get("probability_buy"),
                r.get("expected_ev"),
                r.get("expected_pf"),
                str(r.get("risk") or ""),
                r.get("confidence_raw"),
                r.get("confidence_calibrated"),
                str(r.get("conflict_level") or ""),
                r.get("conflict_score"),
                json.dumps(r.get("votes") or {}, default=str),
                json.dumps(r.get("opinions_slim") or [], default=str),
                json.dumps(r.get("explanation") or {}, default=str),
                json.dumps(r.get("module_weights") or {}, default=str),
                now,
            ),
        )
        n += 1
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_decisions(conn: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    ensure_brain_library_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT trade_id, decision, direction, probability_buy, expected_ev,
                   confidence_calibrated, conflict_level, risk, created_at
            FROM {LIBRARY_TABLE}
            ORDER BY id DESC
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
                "trade_id": r[0], "decision": r[1], "direction": r[2],
                "probability_buy": r[3], "expected_ev": r[4],
                "confidence_calibrated": r[5], "conflict_level": r[6],
                "risk": r[7], "created_at": r[8],
            })
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_brain_library_schema",
    "load_decisions",
    "upsert_decisions",
]
