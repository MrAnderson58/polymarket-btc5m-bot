"""SQLite Edge Library V1 — durable store for surviving edges."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

LIBRARY_TABLE = "market_edge_library_v1"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LIBRARY_TABLE} (
  id TEXT PRIMARY KEY,
  rule TEXT NOT NULL,
  regime TEXT,
  sample INTEGER,
  expectancy REAL,
  pf REAL,
  wr REAL,
  drawdown REAL,
  confidence REAL,
  posterior_probability REAL,
  stability REAL,
  quality_score REAL,
  status TEXT,
  features_json TEXT,
  metrics_json TEXT,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edge_lib_quality
  ON {LIBRARY_TABLE}(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_edge_lib_status
  ON {LIBRARY_TABLE}(status);
"""


def ensure_edge_library_schema(conn: Any) -> None:
    conn.executescript(_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def _edge_id(rule: str, features: list[str] | None) -> str:
    blob = (rule + "|" + ",".join(features or [])).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def upsert_edges(conn: Any, edges: list[dict[str, Any]], *, now: int | None = None) -> int:
    """Insert or update surviving edges. Returns rows touched."""
    ensure_edge_library_schema(conn)
    now = int(now or time.time())
    n = 0
    for e in edges:
        eid = e.get("library_id") or _edge_id(str(e.get("rule") or ""), list(e.get("features") or []))
        e["library_id"] = eid
        conf = None
        if e.get("ci95") and e["ci95"][0] is not None:
            # confidence proxy: distance of CI low above 0
            conf = max(0.0, min(1.0, float(e.get("prob_edge_gt_0") or 0.5)))
        else:
            conf = float(e.get("prob_edge_gt_0") or 0.0)
        params = (
            eid,
            str(e.get("rule") or ""),
            str(e.get("regime") or e.get("cluster") or ""),
            int(e.get("n") or 0),
            e.get("expectancy"),
            e.get("pf"),
            e.get("winrate"),
            e.get("max_dd"),
            conf,
            e.get("prob_edge_gt_0"),
            e.get("stability"),
            e.get("quality_score") or e.get("edge_score"),
            str(e.get("status") or ""),
            json.dumps(e.get("features") or [], default=str),
            json.dumps({
                "p_value": e.get("p_value"),
                "q_value": e.get("q_value"),
                "sortino": e.get("sortino"),
                "sharpe": e.get("sharpe"),
                "wf_ok": e.get("wf_ok"),
                "oos_ok": e.get("oos_ok"),
                "rolling_ok": e.get("rolling_ok"),
                "expanding_ok": e.get("expanding_ok"),
                "regime_ok": e.get("regime_ok"),
            }, default=str),
            now,
            now,
        )
        conn.execute(
            f"""
            INSERT INTO {LIBRARY_TABLE} (
              id, rule, regime, sample, expectancy, pf, wr, drawdown,
              confidence, posterior_probability, stability, quality_score,
              status, features_json, metrics_json, first_seen, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              rule=excluded.rule,
              regime=excluded.regime,
              sample=excluded.sample,
              expectancy=excluded.expectancy,
              pf=excluded.pf,
              wr=excluded.wr,
              drawdown=excluded.drawdown,
              confidence=excluded.confidence,
              posterior_probability=excluded.posterior_probability,
              stability=excluded.stability,
              quality_score=excluded.quality_score,
              status=excluded.status,
              features_json=excluded.features_json,
              metrics_json=excluded.metrics_json,
              last_seen=excluded.last_seen
            """,
            params,
        )
        n += 1
    try:
        conn.commit()
    except Exception:
        pass
    return n


def load_library(conn: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    ensure_edge_library_schema(conn)
    try:
        cur = conn.execute(
            f"""
            SELECT id, rule, regime, sample, expectancy, pf, wr, drawdown,
                   confidence, posterior_probability, stability, quality_score,
                   status, features_json, first_seen, last_seen
            FROM {LIBRARY_TABLE}
            ORDER BY quality_score DESC NULLS LAST
            LIMIT ?
            """,
            (limit,),
        )
    except Exception:
        # SQLite older may not like NULLS LAST
        cur = conn.execute(
            f"""
            SELECT id, rule, regime, sample, expectancy, pf, wr, drawdown,
                   confidence, posterior_probability, stability, quality_score,
                   status, features_json, first_seen, last_seen
            FROM {LIBRARY_TABLE}
            ORDER BY quality_score DESC
            LIMIT ?
            """,
            (limit,),
        )
    out = []
    for r in cur.fetchall():
        if hasattr(r, "keys"):
            d = {k: r[k] for k in r.keys()}
        else:
            d = {
                "id": r[0], "rule": r[1], "regime": r[2], "sample": r[3],
                "expectancy": r[4], "pf": r[5], "wr": r[6], "drawdown": r[7],
                "confidence": r[8], "posterior_probability": r[9],
                "stability": r[10], "quality_score": r[11], "status": r[12],
                "features_json": r[13], "first_seen": r[14], "last_seen": r[15],
            }
        out.append(d)
    return out


__all__ = [
    "LIBRARY_TABLE",
    "ensure_edge_library_schema",
    "load_library",
    "upsert_edges",
]
