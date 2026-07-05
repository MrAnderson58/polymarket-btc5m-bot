"""Telegram source column mapping and row normalization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

SOURCE_TABLES = (
    "telegram_channels",
    "telegram_messages",
    "telegram_signals",
    "market_prices",
    "news",
    "ai_signals",
    "source_ratings",
)

# Production trading_ai.telegram_messages schema (priority order)
TEXT_COLUMNS = ("message_text", "text", "content", "message", "body", "raw_text")
TS_COLUMNS = (
    "message_date",
    "timestamp",
    "date",
    "created_at",
    "published_at",
    "message_ts",
    "ts",
    "collected_at",
    "recorded_at",
)
MSG_ID_COLUMNS = ("telegram_message_id", "message_id", "external_id", "msg_id")
SOURCE_COLUMNS = ("channel_name", "source", "channel", "source_name", "channel_username")
CHANNEL_ID_COLUMNS = ("channel_id", "chat_id")
RAW_JSON_COLUMNS = ("raw_json", "metadata_json", "payload")
ROW_ID_COLUMNS = ("id",)


@dataclass(frozen=True)
class MessageColumnMap:
    table: str
    text_col: str
    ts_col: str
    msg_id_col: str
    source_col: str | None = None
    channel_id_col: str | None = None
    raw_json_col: str | None = None

    def select_sql(self) -> str:
        cols = [self.text_col, self.ts_col, self.msg_id_col]
        if self.source_col:
            cols.append(self.source_col)
        if self.channel_id_col:
            cols.append(self.channel_id_col)
        if self.raw_json_col:
            cols.append(self.raw_json_col)
        seen: set[str] = set()
        unique = []
        for c in cols:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return ", ".join(unique)


@dataclass
class RawMessage:
    source: str
    message_id: str
    timestamp: int
    text: str
    channel_id: str | None = None
    raw_json: str | None = None


def _pick_column(cols: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def resolve_message_columns(table: str, cols: list[str]) -> MessageColumnMap | None:
    text_col = _pick_column(cols, TEXT_COLUMNS)
    ts_col = _pick_column(cols, TS_COLUMNS)
    msg_id_col = _pick_column(cols, MSG_ID_COLUMNS) or _pick_column(cols, ROW_ID_COLUMNS)
    if not text_col or not ts_col or not msg_id_col:
        return None
    return MessageColumnMap(
        table=table,
        text_col=text_col,
        ts_col=ts_col,
        msg_id_col=msg_id_col,
        source_col=_pick_column(cols, SOURCE_COLUMNS),
        channel_id_col=_pick_column(cols, CHANNEL_ID_COLUMNS),
        raw_json_col=_pick_column(cols, RAW_JSON_COLUMNS),
    )


def _row_get(row: Any, col: str) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[col]
    if isinstance(row, dict):
        return row.get(col)
    return getattr(row, col, None)


def _normalize_ts(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for parser in (
            lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
            lambda s: datetime.fromisoformat(s.replace(" ", "T")),
            lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
        ):
            try:
                return int(parser(text).timestamp())
            except ValueError:
                continue
        return None
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return None
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def row_to_raw_message(row: Any, mapping: MessageColumnMap) -> RawMessage | None:
    text = _row_get(row, mapping.text_col)
    if not text or not str(text).strip():
        return None
    ts = _normalize_ts(_row_get(row, mapping.ts_col))
    if ts is None:
        return None
    msg_id_raw = _row_get(row, mapping.msg_id_col)
    if msg_id_raw is None:
        return None
    source = "unknown"
    if mapping.source_col:
        src_val = _row_get(row, mapping.source_col)
        if src_val is not None:
            source = str(src_val)
    channel_id = None
    if mapping.channel_id_col:
        ch = _row_get(row, mapping.channel_id_col)
        if ch is not None:
            channel_id = str(ch)
    raw_json = None
    if mapping.raw_json_col:
        raw = _row_get(row, mapping.raw_json_col)
        if raw is not None:
            raw_json = str(raw)
    return RawMessage(
        source=source,
        message_id=str(msg_id_raw),
        timestamp=ts,
        text=str(text),
        channel_id=channel_id,
        raw_json=raw_json,
    )


def build_messages_query(
    mapping: MessageColumnMap,
    *,
    source: str | None = None,
    limit: int | None = None,
    order: str = "ASC",
    param_style: str = "pg",
) -> tuple[str, list[Any]]:
    placeholder = "%s" if param_style == "pg" else "?"
    q = f"SELECT {mapping.select_sql()} FROM {mapping.table}"
    params: list[Any] = []
    if source and mapping.source_col:
        q += f" WHERE {mapping.source_col} = {placeholder}"
        params.append(source)
    q += f" ORDER BY {mapping.ts_col} {order}"
    if limit is not None:
        q += f" LIMIT {placeholder}"
        params.append(int(limit))
    return q, params


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


def table_row_count(
    conn: sqlite3.Connection,
    name: str,
    *,
    source_col: str | None = None,
    source: str | None = None,
) -> int:
    if not table_exists(conn, name):
        return 0
    if source and source_col:
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {name} WHERE {source_col} = ?",
            (source,),
        ).fetchone()[0])
    return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])


def table_ts_range_sqlite(
    conn: sqlite3.Connection,
    name: str,
    ts_col: str,
    *,
    source_col: str | None = None,
    source: str | None = None,
) -> tuple[int | None, int | None]:
    if not table_exists(conn, name):
        return None, None
    if source and source_col:
        row = conn.execute(
            f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {name} "
            f"WHERE {source_col} = ? AND {ts_col} IS NOT NULL",
            (source,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {name} WHERE {ts_col} IS NOT NULL",
        ).fetchone()
    if not row or row[0] is None:
        return None, None
    return _normalize_ts(row[0]), _normalize_ts(row[1])


def iter_sqlite_rows(
    conn: sqlite3.Connection,
    mapping: MessageColumnMap,
    *,
    source: str | None = None,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    q, params = build_messages_query(mapping, source=source, limit=limit, param_style="sqlite")
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(q, params):
            yield dict(row)
    finally:
        conn.row_factory = None


def channel_stats_sqlite(conn: sqlite3.Connection, mapping: MessageColumnMap) -> list[dict[str, Any]]:
    if not mapping.source_col:
        return [{"source": "all", "count": table_row_count(conn, mapping.table)}]
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"SELECT {mapping.source_col} AS source, COUNT(*) AS n FROM {mapping.table} "
            f"GROUP BY {mapping.source_col} ORDER BY n DESC",
        ).fetchall()
        return [{"source": r["source"], "count": r["n"]} for r in rows]
    finally:
        conn.row_factory = None


def iter_telegram_messages(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    source: str | None = None,
) -> list[RawMessage]:
    table = "telegram_messages" if table_exists(conn, "telegram_messages") else (
        "telegram_signals" if table_exists(conn, "telegram_signals") else None
    )
    if not table:
        return []
    mapping = resolve_message_columns(table, table_columns(conn, table))
    if mapping is None:
        return []
    out: list[RawMessage] = []
    for row in iter_sqlite_rows(conn, mapping, source=source, limit=limit):
        msg = row_to_raw_message(row, mapping)
        if msg:
            out.append(msg)
    return out


def channel_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    table = "telegram_messages" if table_exists(conn, "telegram_messages") else (
        "telegram_signals" if table_exists(conn, "telegram_signals") else None
    )
    if not table:
        return []
    mapping = resolve_message_columns(table, table_columns(conn, table))
    if mapping is None:
        return []
    return channel_stats_sqlite(conn, mapping)
