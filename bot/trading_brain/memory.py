"""Memory Engine — unified knowledge store for trades, experiments, reports."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR
from bot.trading_brain.constants import SOURCE_TABLE


def upsert_memory(
    conn: sqlite3.Connection,
    *,
    category: str,
    ref_key: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO brain_memory (category, ref_key, payload_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(category, ref_key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (category, ref_key, json.dumps(payload, ensure_ascii=False)),
    )


def load_memory(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
) -> list[sqlite3.Row]:
    if category:
        return conn.execute(
            """
            SELECT * FROM brain_memory
            WHERE category = ?
            ORDER BY updated_at DESC
            """,
            (category,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM brain_memory ORDER BY category, updated_at DESC"
    ).fetchall()


def get_learning_state(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT value_json FROM brain_learning_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return {}
    return json.loads(row["value_json"])


def set_learning_state(conn: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO brain_learning_state (key, value_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(value, ensure_ascii=False)),
    )


def sync_memory_engine(conn: sqlite3.Connection) -> dict[str, int]:
    """Ingest trades, experiments, reports, versions into brain_memory."""
    counts = {"trade": 0, "experiment": 0, "report": 0, "version": 0}

    trades = conn.execute(
        f"""
        SELECT id, strategy_name, entry_price, pnl_percent, exit_reason, closed_at
        FROM {SOURCE_TABLE}
        WHERE status = 'closed'
        ORDER BY entry_ts DESC
        LIMIT 500
        """
    ).fetchall()
    for t in trades:
        upsert_memory(
            conn,
            category="trade",
            ref_key=str(t["id"]),
            payload={
                "trade_id": t["id"],
                "strategy": t["strategy_name"],
                "entry": float(t["entry_price"]),
                "pnl": float(t["pnl_percent"] or 0),
                "exit_reason": t["exit_reason"],
                "closed_at": t["closed_at"],
            },
        )
        counts["trade"] += 1

    exp_path = BASE_DIR / "reports" / "experiments.json"
    if exp_path.exists():
        experiments = json.loads(exp_path.read_text(encoding="utf-8"))
        for exp in experiments[-20:]:
            upsert_memory(
                conn,
                category="experiment",
                ref_key=str(exp.get("id", exp.get("config_fingerprint", "?"))),
                payload=exp,
            )
            counts["experiment"] += 1

    idx_path = BASE_DIR / "reports" / "memory_index.json"
    if idx_path.exists():
        index = json.loads(idx_path.read_text(encoding="utf-8"))
        for entry in index[-14:]:
            upsert_memory(
                conn,
                category="report",
                ref_key=entry.get("generated_at", "?")[:19],
                payload=entry,
            )
            counts["report"] += 1

    from bot.performance import build_version_summaries, fetch_closed_trades

    summaries = build_version_summaries(fetch_closed_trades(conn))
    for s in summaries:
        upsert_memory(
            conn,
            category="version",
            ref_key=s.version,
            payload={
                "version": s.version,
                "trades": s.trades,
                "win_rate": s.win_rate,
                "profit_factor": s.profit_factor,
                "avg_pnl": s.average_pnl_percent,
            },
        )
        counts["version"] += 1

    set_learning_state(
        conn,
        "memory_sync",
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
        },
    )
    return counts
