"""Read from existing telegram/source tables without modifying raw data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

SOURCE_TABLES = (
    "telegram_channels",
    "telegram_messages",
    "telegram_signals",
    "market_prices",
    "news",
    "ai_signals",
    "source_ratings",
)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    if not table_exists(conn, name):
        return []
    return [r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]


def table_row_count(conn: sqlite3.Connection, name: str) -> int:
    if not table_exists(conn, name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])


def table_ts_range(conn: sqlite3.Connection, name: str, ts_col: str) -> tuple[int | None, int | None]:
    if not table_exists(conn, name) or ts_col not in table_columns(conn, name):
        return None, None
    row = conn.execute(
        f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {name} WHERE {ts_col} IS NOT NULL"
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    return int(row[0]), int(row[1])


def _pick_column(cols: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


@dataclass
class RawMessage:
    source: str
    message_id: str
    timestamp: int
    text: str
    channel_id: str | None = None
    raw_json: str | None = None


def iter_telegram_messages(conn: sqlite3.Connection, *, limit: int | None = None) -> list[RawMessage]:
    """Load messages from telegram_messages or telegram_signals fallback."""
    if table_exists(conn, "telegram_messages"):
        return _iter_from_table(conn, "telegram_messages", limit=limit)
    if table_exists(conn, "telegram_signals"):
        return _iter_from_table(conn, "telegram_signals", limit=limit)
    return []


def _iter_from_table(conn: sqlite3.Connection, table: str, *, limit: int | None) -> list[RawMessage]:
    cols = table_columns(conn, table)
    text_col = _pick_column(cols, ("text", "message_text", "content", "body", "raw_text"))
    ts_col = _pick_column(cols, ("timestamp", "message_ts", "ts", "created_at", "date"))
    msg_col = _pick_column(cols, ("message_id", "msg_id", "id"))
    src_col = _pick_column(cols, ("source", "channel", "channel_username", "channel_name"))
    ch_col = _pick_column(cols, ("channel_id", "chat_id"))
    raw_col = _pick_column(cols, ("raw_json", "metadata_json", "payload"))

    if not text_col or not ts_col or not msg_col:
        return []

    q = f"SELECT * FROM {table} ORDER BY {ts_col} ASC"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()
    out: list[RawMessage] = []
    for row in rows:
        text = row[text_col]
        if not text:
            continue
        ts_raw = row[ts_col]
        ts = _normalize_ts(ts_raw)
        if ts is None:
            continue
        source = str(row[src_col]) if src_col and row[src_col] is not None else "unknown"
        out.append(RawMessage(
            source=source,
            message_id=str(row[msg_col]),
            timestamp=ts,
            text=str(text),
            channel_id=str(row[ch_col]) if ch_col and row[ch_col] is not None else None,
            raw_json=str(row[raw_col]) if raw_col and row[raw_col] is not None else None,
        ))
    return out


def _normalize_ts(value: Any) -> int | None:
    if value is None:
        return None
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return None
    # Heuristic: ms vs s
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def channel_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "telegram_messages"):
        if not table_exists(conn, "telegram_signals"):
            return []
        table = "telegram_signals"
    else:
        table = "telegram_messages"

    cols = table_columns(conn, table)
    src_col = _pick_column(cols, ("source", "channel", "channel_username", "channel_name"))
    if not src_col:
        return [{"source": "all", "count": table_row_count(conn, table)}]

    rows = conn.execute(
        f"SELECT {src_col} AS source, COUNT(*) AS n FROM {table} GROUP BY {src_col} ORDER BY n DESC"
    ).fetchall()
    return [{"source": r["source"], "count": r["n"]} for r in rows]
