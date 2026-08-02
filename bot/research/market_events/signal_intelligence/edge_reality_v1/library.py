"""SQLite store for Edge Reality Audit V1 module attribution."""

from __future__ import annotations

import json
import time
from typing import Any

LIBRARY_TABLE = "market_module_attribution_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  module TEXT NOT NULL,
  n INTEGER,
  ev REAL,
  mean_ev REAL,
  pf REAL,
  wr REAL,
  delta_ev REAL,
  delta_pf REAL,
  delta_wr REAL,
  mutual_information REAL,
  information_gain REAL,
  precision REAL,
  recall REAL,
  f1 REAL,
  grade TEXT,
  score REAL,
  keep INTEGER,
  metrics_json TEXT,
  updated_at INTEGER,
  UNIQUE(run_id, module)
);
CREATE INDEX IF NOT EXISTS idx_mod_attr_v1_run ON {LIBRARY_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_mod_attr_v1_module ON {LIBRARY_TABLE}(module);
"""


def ensure_attribution_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def upsert_attribution(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    now: int | None = None,
) -> int:
    ensure_attribution_schema(conn)
    now = int(now or time.time())
    n = 0
    for r in rows:
        mod = str(r.get("module") or "")
        if not mod:
            continue
        vs = r.get("vs_production") or {}
        params = (
            run_id,
            mod,
            r.get("n"),
            r.get("ev"),
            r.get("mean_ev"),
            r.get("pf"),
            r.get("wr"),
            r.get("delta_ev") if r.get("delta_ev") is not None else vs.get("delta_ev"),
            r.get("delta_pf") if r.get("delta_pf") is not None else vs.get("delta_pf"),
            r.get("delta_wr") if r.get("delta_wr") is not None else vs.get("delta_wr"),
            r.get("mutual_information"),
            r.get("information_gain"),
            r.get("precision"),
            r.get("recall"),
            r.get("f1"),
            r.get("grade"),
            r.get("score"),
            1 if r.get("keep") else 0,
            json.dumps(r, default=str),
            now,
        )
        sql = f"""
            INSERT INTO {LIBRARY_TABLE} (
              run_id, module, n, ev, mean_ev, pf, wr,
              delta_ev, delta_pf, delta_wr,
              mutual_information, information_gain,
              precision, recall, f1, grade, score, keep,
              metrics_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id, module) DO UPDATE SET
              n=excluded.n,
              ev=excluded.ev,
              mean_ev=excluded.mean_ev,
              pf=excluded.pf,
              wr=excluded.wr,
              delta_ev=excluded.delta_ev,
              delta_pf=excluded.delta_pf,
              delta_wr=excluded.delta_wr,
              mutual_information=excluded.mutual_information,
              information_gain=excluded.information_gain,
              precision=excluded.precision,
              recall=excluded.recall,
              f1=excluded.f1,
              grade=excluded.grade,
              score=excluded.score,
              keep=excluded.keep,
              metrics_json=excluded.metrics_json,
              updated_at=excluded.updated_at
            """
        last_err: Exception | None = None
        for attempt in range(8):
            try:
                conn.execute(sql, params)
                n += 1
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                msg = str(exc).lower()
                if "locked" in msg or "busy" in msg:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
        if last_err is not None:
            raise last_err
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_attribution(conn: Any, *, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    ensure_attribution_schema(conn)
    try:
        if run_id:
            cur = conn.execute(
                f"""
                SELECT * FROM {LIBRARY_TABLE}
                WHERE run_id = ?
                ORDER BY delta_ev DESC
                LIMIT ?
                """,
                (run_id, int(limit)),
            )
        else:
            cur = conn.execute(
                f"""
                SELECT * FROM {LIBRARY_TABLE}
                ORDER BY updated_at DESC, delta_ev DESC
                LIMIT ?
                """,
                (int(limit),),
            )
    except Exception:
        return []
    cols = [d[0] for d in (cur.description or [])]
    out = []
    for r in cur.fetchall():
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(zip(cols, r)))
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_attribution_schema",
    "load_attribution",
    "upsert_attribution",
]
