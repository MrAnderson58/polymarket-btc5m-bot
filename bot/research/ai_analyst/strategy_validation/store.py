"""S48 — SQLite schema for signal history, outcomes, rankings."""

from __future__ import annotations

import json
from typing import Any

S48_VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS ai_signal_history_s48 (
    signal_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    market TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    score REAL,
    reasoning TEXT,
    reasons_json TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry_low REAL NOT NULL,
    entry_high REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    risk_pct REAL NOT NULL,
    status TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_ai_signal_history_s48_created
  ON ai_signal_history_s48(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_signal_history_s48_market
  ON ai_signal_history_s48(market, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_signal_outcomes_s48 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL UNIQUE,
    trade_id TEXT NOT NULL,
    closed_at INTEGER NOT NULL,
    result TEXT NOT NULL,
    r_multiple REAL NOT NULL,
    pnl_usd REAL NOT NULL,
    mfe_pct REAL NOT NULL,
    mae_pct REAL NOT NULL,
    hold_time_sec INTEGER NOT NULL,
    tp_level_reached TEXT,
    exit_reason TEXT,
    strategy TEXT NOT NULL,
    market TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL,
    score REAL,
    reasons_json TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(signal_id) REFERENCES ai_signal_history_s48(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_signal_outcomes_s48_closed
  ON ai_signal_outcomes_s48(closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_signal_outcomes_s48_strategy
  ON ai_signal_outcomes_s48(strategy, closed_at DESC);

CREATE TABLE IF NOT EXISTS ai_signal_rankings_s48 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    ranking_type TEXT NOT NULL,
    body_text TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def upsert_signal_history(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_signal_history_s48 (
          signal_id, created_at, market, direction, confidence, score,
          reasoning, reasons_json, strategy, entry_low, entry_high,
          stop_loss, tp1, tp2, tp3, risk_pct, status, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["signal_id"],
            int(row["created_at"]),
            str(row["market"]),
            str(row["direction"]),
            float(row["confidence"]),
            row.get("score"),
            row.get("reasoning"),
            json.dumps(row.get("reasons") or [], ensure_ascii=False),
            str(row.get("strategy") or "ai_default"),
            float(row["entry_low"]),
            float(row["entry_high"]),
            float(row["stop_loss"]),
            float(row["tp1"]),
            float(row["tp2"]),
            float(row["tp3"]),
            float(row.get("risk_pct") or 1.0),
            str(row.get("status") or "PENDING"),
            json.dumps(row.get("tags") or [], ensure_ascii=False),
        ),
    )


def upsert_outcome(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO ai_signal_outcomes_s48 (
          signal_id, trade_id, closed_at, result, r_multiple, pnl_usd,
          mfe_pct, mae_pct, hold_time_sec, tp_level_reached, exit_reason,
          strategy, market, direction, confidence, score, reasons_json, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id) DO UPDATE SET
          trade_id=excluded.trade_id,
          closed_at=excluded.closed_at,
          result=excluded.result,
          r_multiple=excluded.r_multiple,
          pnl_usd=excluded.pnl_usd,
          mfe_pct=excluded.mfe_pct,
          mae_pct=excluded.mae_pct,
          hold_time_sec=excluded.hold_time_sec,
          tp_level_reached=excluded.tp_level_reached,
          exit_reason=excluded.exit_reason,
          strategy=excluded.strategy,
          market=excluded.market,
          direction=excluded.direction,
          confidence=excluded.confidence,
          score=excluded.score,
          reasons_json=excluded.reasons_json,
          tags_json=excluded.tags_json
        """,
        (
            row["signal_id"],
            row["trade_id"],
            int(row["closed_at"]),
            str(row["result"]),
            float(row["r_multiple"]),
            float(row["pnl_usd"]),
            float(row["mfe_pct"]),
            float(row["mae_pct"]),
            int(row["hold_time_sec"]),
            row.get("tp_level_reached"),
            row.get("exit_reason"),
            str(row.get("strategy") or "ai_default"),
            str(row.get("market") or "BTC"),
            str(row.get("direction") or ""),
            row.get("confidence"),
            row.get("score"),
            json.dumps(row.get("reasons") or [], ensure_ascii=False),
            json.dumps(row.get("tags") or [], ensure_ascii=False),
        ),
    )


def save_ranking(conn: Any, *, ranking_type: str, body: str, payload: dict[str, Any], now: int) -> None:
    conn.execute(
        """
        INSERT INTO ai_signal_rankings_s48 (created_at, ranking_type, body_text, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (now, ranking_type, body, json.dumps(payload, ensure_ascii=False, default=str)),
    )


def load_signal_history(conn: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM ai_signal_history_s48
        ORDER BY created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_signal(dict(r)) for r in rows]


def load_outcomes(conn: Any, *, limit: int = 200, since_ts: int | None = None) -> list[dict[str, Any]]:
    if since_ts is not None:
        rows = conn.execute(
            """
            SELECT * FROM ai_signal_outcomes_s48
            WHERE closed_at >= ?
            ORDER BY closed_at DESC LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM ai_signal_outcomes_s48
            ORDER BY closed_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_outcome(dict(r)) for r in rows]


def _row_to_signal(d: dict[str, Any]) -> dict[str, Any]:
    d["reasons"] = json.loads(d.pop("reasons_json") or "[]")
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    return d


def _row_to_outcome(d: dict[str, Any]) -> dict[str, Any]:
    d["reasons"] = json.loads(d.pop("reasons_json") or "[]")
    d["tags"] = json.loads(d.pop("tags_json") or "[]")
    return d
