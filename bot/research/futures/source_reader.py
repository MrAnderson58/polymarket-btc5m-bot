"""Read-only source adapters for futures research (PostgreSQL primary, SQLite dev)."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlparse

from bot.research.futures.db_config import (
    REASON_POSTGRES_CONNECT_FAILED,
    REASON_POSTGRES_DRIVER_MISSING,
    REASON_POSTGRES_URL_MISSING,
    REASON_SOURCE_BACKEND_MISMATCH,
    REASON_SQLITE_SOURCE_EMPTY,
    get_futures_require_postgres,
    get_futures_source_backend,
    get_futures_source_connect_timeout_sec,
    get_futures_source_database_url,
    get_futures_source_statement_timeout_ms,
)
from bot.research.futures.source_data import (
    SOURCE_TABLES,
    RawMessage,
    _iter_from_table,
    _normalize_ts,
    _pick_column,
    table_columns,
    table_exists,
    table_row_count,
    table_ts_range,
)

READ_ONLY_GUARANTEE = (
    "Source adapter is read-only: no INSERT/UPDATE/DELETE on telegram/source tables."
)


class SourceConfigError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass
class SourceConnectionInfo:
    backend: str
    status: str
    reason_code: str | None = None
    dsn_host: str | None = None
    dsn_database: str | None = None
    read_only: bool = True
    details: str = READ_ONLY_GUARANTEE


@dataclass
class SourceReader(ABC):
    info: SourceConnectionInfo

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def list_source_tables(self) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    def iter_telegram_messages(self, *, limit: int | None = None) -> Iterator[RawMessage]: ...

    @abstractmethod
    def channel_stats(self) -> list[dict[str, Any]]: ...


def _parse_dsn_meta(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    host = parsed.hostname
    db = parsed.path.lstrip("/") if parsed.path else None
    return host, db


def _postgres_connect(url: str):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise SourceConfigError(
            "psycopg2 required for PostgreSQL source (pip install psycopg2-binary)",
            reason_code=REASON_POSTGRES_DRIVER_MISSING,
        ) from exc

    conn = psycopg2.connect(
        url,
        connect_timeout=int(get_futures_source_connect_timeout_sec()),
        cursor_factory=RealDictCursor,
    )
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {int(get_futures_source_statement_timeout_ms())}")
    return conn


class PostgresSourceReader(SourceReader):
    def __init__(self, url: str) -> None:
        host, db = _parse_dsn_meta(url)
        try:
            self._conn = _postgres_connect(url)
            status = "connected"
            reason = None
        except SourceConfigError:
            raise
        except Exception as exc:
            raise SourceConfigError(
                f"PostgreSQL source connection failed: {exc}",
                reason_code=REASON_POSTGRES_CONNECT_FAILED,
            ) from exc

        self.info = SourceConnectionInfo(
            backend="postgres",
            status=status,
            reason_code=reason,
            dsn_host=host,
            dsn_database=db,
            read_only=True,
        )

    def close(self) -> None:
        self._conn.close()

    def _pg_table_exists(self, name: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (name,),
        )
        return cur.fetchone() is not None

    def _pg_columns(self, name: str) -> list[str]:
        if not self._pg_table_exists(name):
            return []
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (name,),
        )
        return [r["column_name"] for r in cur.fetchall()]

    def _pg_count(self, name: str) -> int:
        if not self._pg_table_exists(name):
            return 0
        cur = self._conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM {name}")
        row = cur.fetchone()
        return int(row["n"]) if row else 0

    def _pg_ts_range(self, name: str, ts_col: str) -> tuple[int | None, int | None]:
        if not self._pg_table_exists(name):
            return None, None
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT MIN({ts_col}) AS lo, MAX({ts_col}) AS hi FROM {name} WHERE {ts_col} IS NOT NULL"
        )
        row = cur.fetchone()
        if not row or row["lo"] is None:
            return None, None
        lo = _normalize_ts(row["lo"])
        hi = _normalize_ts(row["hi"])
        return lo, hi

    def list_source_tables(self) -> dict[str, dict[str, Any]]:
        tables: dict[str, dict[str, Any]] = {}
        for name in SOURCE_TABLES:
            exists = self._pg_table_exists(name)
            info: dict[str, Any] = {
                "exists": exists,
                "row_count": self._pg_count(name) if exists else 0,
                "columns": self._pg_columns(name) if exists else [],
            }
            if exists:
                for ts_col in ("timestamp", "message_ts", "ts", "created_at", "recorded_at", "date"):
                    if ts_col in info["columns"]:
                        info["ts_range"] = self._pg_ts_range(name, ts_col)
                        break
            tables[name] = info
        return tables

    def iter_telegram_messages(self, *, limit: int | None = None) -> Iterator[RawMessage]:
        tables = self.list_source_tables()
        if tables.get("telegram_messages", {}).get("exists"):
            yield from self._iter_pg_table("telegram_messages", limit=limit)
            return
        if tables.get("telegram_signals", {}).get("exists"):
            yield from self._iter_pg_table("telegram_signals", limit=limit)
            return

    def _iter_pg_table(self, table: str, *, limit: int | None) -> Iterator[RawMessage]:
        cols = self._pg_columns(table)
        text_col = _pick_column(cols, ("text", "message_text", "content", "body", "raw_text"))
        ts_col = _pick_column(cols, ("timestamp", "message_ts", "ts", "created_at", "date"))
        msg_col = _pick_column(cols, ("message_id", "msg_id", "id"))
        src_col = _pick_column(cols, ("source", "channel", "channel_username", "channel_name"))
        ch_col = _pick_column(cols, ("channel_id", "chat_id"))
        raw_col = _pick_column(cols, ("raw_json", "metadata_json", "payload"))

        if not text_col or not ts_col or not msg_col:
            return

        q = f"SELECT * FROM {table} ORDER BY {ts_col} ASC"
        if limit:
            q += f" LIMIT {int(limit)}"

        cur = self._conn.cursor()
        cur.execute(q)
        for row in cur:
            text = row.get(text_col)
            if not text:
                continue
            ts = _normalize_ts(row.get(ts_col))
            if ts is None:
                continue
            source = str(row[src_col]) if src_col and row.get(src_col) is not None else "unknown"
            yield RawMessage(
                source=source,
                message_id=str(row[msg_col]),
                timestamp=ts,
                text=str(text),
                channel_id=str(row[ch_col]) if ch_col and row.get(ch_col) is not None else None,
                raw_json=str(row[raw_col]) if raw_col and row.get(raw_col) is not None else None,
            )

    def channel_stats(self) -> list[dict[str, Any]]:
        tables = self.list_source_tables()
        if tables.get("telegram_messages", {}).get("exists"):
            table = "telegram_messages"
        elif tables.get("telegram_signals", {}).get("exists"):
            table = "telegram_signals"
        else:
            return []

        cols = self._pg_columns(table)
        src_col = _pick_column(cols, ("source", "channel", "channel_username", "channel_name"))
        if not src_col:
            return [{"source": "all", "count": self._pg_count(table)}]

        rows = self._conn.cursor()
        rows.execute(
            f"SELECT {src_col} AS source, COUNT(*) AS n FROM {table} GROUP BY {src_col} ORDER BY n DESC"
        )
        return [{"source": r["source"], "count": r["n"]} for r in rows.fetchall()]


class SqliteSourceReader(SourceReader):
    def __init__(self, conn: sqlite3.Connection, *, path: str | None = None) -> None:
        self._conn = conn
        self._owns_conn = False
        self.info = SourceConnectionInfo(
            backend="sqlite",
            status="connected",
            dsn_database=path,
            read_only=True,
        )

    def close(self) -> None:
        if self._owns_conn:
            self._conn.close()

    def list_source_tables(self) -> dict[str, dict[str, Any]]:
        tables: dict[str, dict[str, Any]] = {}
        for name in SOURCE_TABLES:
            exists = table_exists(self._conn, name)
            info: dict[str, Any] = {
                "exists": exists,
                "row_count": table_row_count(self._conn, name) if exists else 0,
                "columns": table_columns(self._conn, name) if exists else [],
            }
            if exists:
                for ts_col in ("timestamp", "message_ts", "ts", "created_at", "recorded_at"):
                    if ts_col in info["columns"]:
                        info["ts_range"] = table_ts_range(self._conn, name, ts_col)
                        break
            tables[name] = info
        return tables

    def iter_telegram_messages(self, *, limit: int | None = None) -> Iterator[RawMessage]:
        if table_exists(self._conn, "telegram_messages"):
            yield from iter(_iter_from_table(self._conn, "telegram_messages", limit=limit))
            return
        if table_exists(self._conn, "telegram_signals"):
            yield from iter(_iter_from_table(self._conn, "telegram_signals", limit=limit))

    def channel_stats(self) -> list[dict[str, Any]]:
        from bot.research.futures.source_data import channel_stats
        return channel_stats(self._conn)


def resolve_source_backend() -> str:
    backend = get_futures_source_backend()
    url = get_futures_source_database_url()
    if backend == "auto":
        if url and url.startswith(("postgres://", "postgresql://")):
            return "postgres"
        return "sqlite"
    if backend == "postgres" and not url:
        raise SourceConfigError(
            "FUTURES_SOURCE_BACKEND=postgres but FUTURES_SOURCE_DATABASE_URL is not set",
            reason_code=REASON_POSTGRES_URL_MISSING,
        )
    return backend


def open_source_reader(*, sqlite_conn: sqlite3.Connection | None = None) -> SourceReader:
    """Open read-only source reader. Never writes to source tables."""
    backend = resolve_source_backend()
    url = get_futures_source_database_url()

    if backend == "postgres":
        if not url:
            raise SourceConfigError(
                "PostgreSQL source expected but FUTURES_SOURCE_DATABASE_URL is empty",
                reason_code=REASON_POSTGRES_URL_MISSING,
            )
        if not url.startswith(("postgres://", "postgresql://")):
            raise SourceConfigError(
                f"FUTURES_SOURCE_DATABASE_URL must be postgres://… got scheme {urlparse(url).scheme}",
                reason_code=REASON_SOURCE_BACKEND_MISMATCH,
            )
        return PostgresSourceReader(url)

    if get_futures_require_postgres():
        raise SourceConfigError(
            "FUTURES_REQUIRE_POSTGRES=true but source backend resolved to sqlite",
            reason_code=REASON_SOURCE_BACKEND_MISMATCH,
        )

    if sqlite_conn is None:
        raise SourceConfigError(
            "SQLite source backend requires a database connection",
            reason_code=REASON_SQLITE_SOURCE_EMPTY,
        )

    reader = SqliteSourceReader(sqlite_conn)
    tables = reader.list_source_tables()
    if not any(tables[n]["exists"] and tables[n]["row_count"] > 0 for n in ("telegram_messages", "telegram_signals")):
        if url and url.startswith(("postgres://", "postgresql://")):
            raise SourceConfigError(
                "PostgreSQL URL is configured but sqlite fallback has no telegram messages",
                reason_code=REASON_POSTGRES_CONNECT_FAILED,
            )
    return reader
