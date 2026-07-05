"""Read-only source adapters for futures research (PostgreSQL primary, SQLite dev)."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    MessageColumnMap,
    RawMessage,
    build_messages_query,
    channel_stats_sqlite,
    iter_sqlite_rows,
    resolve_message_columns,
    row_to_raw_message,
    table_columns,
    table_exists,
    table_row_count,
    table_ts_range_sqlite,
    _normalize_ts,
)

READ_ONLY_GUARANTEE = (
    "Source adapter is read-only: no INSERT/UPDATE/DELETE on telegram/source tables."
)

# When auditing all channels without --source, cap parse-quality scan
DEFAULT_PARSE_SAMPLE_LIMIT = 5000
# Channels with fewer rows get a full parse scan in audit
FULL_PARSE_SCAN_MAX_ROWS = 20_000


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
class MessageStats:
    count: int
    first_timestamp: int | None
    last_timestamp: int | None
    duration_seconds: int
    source_filter: str | None = None
    column_map: dict[str, str] | None = None


class SourceReader(ABC):
    info: SourceConnectionInfo

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def list_source_tables(self) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    def resolve_message_table(self) -> tuple[str, MessageColumnMap] | None: ...

    @abstractmethod
    def message_stats(self, *, source: str | None = None) -> MessageStats: ...

    @abstractmethod
    def iter_raw_rows(
        self,
        *,
        limit: int | None = None,
        source: str | None = None,
    ) -> Iterator[dict[str, Any]]: ...

    @abstractmethod
    def channel_stats(self) -> list[dict[str, Any]]: ...

    def iter_telegram_messages(
        self,
        *,
        limit: int | None = None,
        source: str | None = None,
    ) -> Iterator[RawMessage]:
        resolved = self.resolve_message_table()
        if resolved is None:
            return
        _, mapping = resolved
        for row in self.iter_raw_rows(limit=limit, source=source):
            msg = row_to_raw_message(row, mapping)
            if msg:
                yield msg

    def map_row(self, row: dict[str, Any]) -> RawMessage | None:
        resolved = self.resolve_message_table()
        if resolved is None:
            return None
        return row_to_raw_message(row, resolved[1])


def _parse_dsn_meta(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    return parsed.hostname, (parsed.path.lstrip("/") if parsed.path else None)


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
        except SourceConfigError:
            raise
        except Exception as exc:
            raise SourceConfigError(
                f"PostgreSQL source connection failed: {exc}",
                reason_code=REASON_POSTGRES_CONNECT_FAILED,
            ) from exc

        self.info = SourceConnectionInfo(
            backend="postgres",
            status="connected",
            dsn_host=host,
            dsn_database=db,
            read_only=True,
        )
        self._mapping_cache: dict[str, MessageColumnMap] = {}

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

    def _pg_count(self, name: str, *, source_col: str | None = None, source: str | None = None) -> int:
        if not self._pg_table_exists(name):
            return 0
        cur = self._conn.cursor()
        if source and source_col:
            cur.execute(f"SELECT COUNT(*) AS n FROM {name} WHERE {source_col} = %s", (source,))
        else:
            cur.execute(f"SELECT COUNT(*) AS n FROM {name}")
        row = cur.fetchone()
        return int(row["n"]) if row else 0

    def _pg_ts_range(
        self,
        name: str,
        ts_col: str,
        *,
        source_col: str | None = None,
        source: str | None = None,
    ) -> tuple[int | None, int | None]:
        if not self._pg_table_exists(name):
            return None, None
        cur = self._conn.cursor()
        if source and source_col:
            cur.execute(
                f"SELECT MIN({ts_col}) AS lo, MAX({ts_col}) AS hi FROM {name} "
                f"WHERE {source_col} = %s AND {ts_col} IS NOT NULL",
                (source,),
            )
        else:
            cur.execute(
                f"SELECT MIN({ts_col}) AS lo, MAX({ts_col}) AS hi FROM {name} WHERE {ts_col} IS NOT NULL",
            )
        row = cur.fetchone()
        if not row or row["lo"] is None:
            return None, None
        return _normalize_ts(row["lo"]), _normalize_ts(row["hi"])

    def _mapping_for(self, table: str) -> MessageColumnMap | None:
        if table not in self._mapping_cache:
            mapping = resolve_message_columns(table, self._pg_columns(table))
            if mapping:
                self._mapping_cache[table] = mapping
        return self._mapping_cache.get(table)

    def resolve_message_table(self) -> tuple[str, MessageColumnMap] | None:
        for table in ("telegram_messages", "telegram_signals"):
            if self._pg_table_exists(table):
                mapping = self._mapping_for(table)
                if mapping:
                    return table, mapping
        return None

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
                mapping = resolve_message_columns(name, info["columns"])
                if mapping:
                    info["column_map"] = {
                        "text": mapping.text_col,
                        "timestamp": mapping.ts_col,
                        "message_id": mapping.msg_id_col,
                        "source": mapping.source_col,
                    }
                    info["ts_range"] = self._pg_ts_range(name, mapping.ts_col)
            tables[name] = info
        return tables

    def message_stats(self, *, source: str | None = None) -> MessageStats:
        resolved = self.resolve_message_table()
        if resolved is None:
            return MessageStats(count=0, first_timestamp=None, last_timestamp=None, duration_seconds=0)
        table, mapping = resolved
        count = self._pg_count(table, source_col=mapping.source_col, source=source)
        lo, hi = self._pg_ts_range(
            table, mapping.ts_col, source_col=mapping.source_col, source=source,
        )
        duration = (hi - lo) if lo is not None and hi is not None else 0
        return MessageStats(
            count=count,
            first_timestamp=lo,
            last_timestamp=hi,
            duration_seconds=duration,
            source_filter=source,
            column_map={
                "text": mapping.text_col,
                "timestamp": mapping.ts_col,
                "message_id": mapping.msg_id_col,
                "source": mapping.source_col or "",
            },
        )

    def iter_raw_rows(
        self,
        *,
        limit: int | None = None,
        source: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        resolved = self.resolve_message_table()
        if resolved is None:
            return
        _, mapping = resolved
        q, params = build_messages_query(mapping, source=source, limit=limit, param_style="pg")
        cur = self._conn.cursor()
        cur.execute(q, params)
        for row in cur:
            yield dict(row)

    def channel_stats(self) -> list[dict[str, Any]]:
        resolved = self.resolve_message_table()
        if resolved is None:
            return []
        table, mapping = resolved
        if not mapping.source_col:
            return [{"source": "all", "count": self._pg_count(table)}]
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT {mapping.source_col} AS source, COUNT(*) AS n FROM {table} "
            f"GROUP BY {mapping.source_col} ORDER BY n DESC",
        )
        return [{"source": r["source"], "count": r["n"]} for r in cur.fetchall()]


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

    def resolve_message_table(self) -> tuple[str, MessageColumnMap] | None:
        for table in ("telegram_messages", "telegram_signals"):
            if table_exists(self._conn, table):
                mapping = resolve_message_columns(table, table_columns(self._conn, table))
                if mapping:
                    return table, mapping
        return None

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
                mapping = resolve_message_columns(name, info["columns"])
                if mapping:
                    info["column_map"] = {
                        "text": mapping.text_col,
                        "timestamp": mapping.ts_col,
                        "message_id": mapping.msg_id_col,
                        "source": mapping.source_col,
                    }
                    info["ts_range"] = table_ts_range_sqlite(self._conn, name, mapping.ts_col)
            tables[name] = info
        return tables

    def message_stats(self, *, source: str | None = None) -> MessageStats:
        resolved = self.resolve_message_table()
        if resolved is None:
            return MessageStats(count=0, first_timestamp=None, last_timestamp=None, duration_seconds=0)
        table, mapping = resolved
        count = table_row_count(
            self._conn, table, source_col=mapping.source_col, source=source,
        )
        lo, hi = table_ts_range_sqlite(
            self._conn, table, mapping.ts_col,
            source_col=mapping.source_col, source=source,
        )
        duration = (hi - lo) if lo is not None and hi is not None else 0
        return MessageStats(
            count=count,
            first_timestamp=lo,
            last_timestamp=hi,
            duration_seconds=duration,
            source_filter=source,
            column_map={
                "text": mapping.text_col,
                "timestamp": mapping.ts_col,
                "message_id": mapping.msg_id_col,
                "source": mapping.source_col or "",
            },
        )

    def iter_raw_rows(
        self,
        *,
        limit: int | None = None,
        source: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        resolved = self.resolve_message_table()
        if resolved is None:
            return
        _, mapping = resolved
        yield from iter_sqlite_rows(self._conn, mapping, source=source, limit=limit)

    def channel_stats(self) -> list[dict[str, Any]]:
        resolved = self.resolve_message_table()
        if resolved is None:
            return []
        return channel_stats_sqlite(self._conn, resolved[1])


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


def sqlite_has_telegram_source(conn: sqlite3.Connection) -> bool:
    """True when conn contains a readable local telegram_messages/telegram_signals table."""
    for table in ("telegram_messages", "telegram_signals"):
        if not table_exists(conn, table):
            continue
        mapping = resolve_message_columns(table, table_columns(conn, table))
        if mapping is None:
            continue
        if table_row_count(conn, table) > 0:
            return True
    return False


def open_configured_source_reader() -> SourceReader:
    """Open telegram source from environment configuration only (production PostgreSQL)."""
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

    raise SourceConfigError(
        "No PostgreSQL source configured; pass an explicit source reader or sqlite source connection",
        reason_code=REASON_SQLITE_SOURCE_EMPTY,
    )


def resolve_research_source(
    research_conn: sqlite3.Connection,
    *,
    source: SourceReader | None = None,
    source_conn: sqlite3.Connection | None = None,
) -> tuple[SourceReader, bool]:
    """Resolve telegram source with precedence: reader > source_conn > local conn > env.

    Returns (reader, should_close).
    """
    if source is not None:
        return source, False
    if source_conn is not None:
        return SqliteSourceReader(source_conn), False
    if sqlite_has_telegram_source(research_conn):
        return SqliteSourceReader(research_conn), False
    return open_configured_source_reader(), True


def open_source_reader(*, sqlite_conn: sqlite3.Connection | None = None) -> SourceReader:
    """CLI helper: env-configured source, or sqlite backend using the given connection."""
    backend = resolve_source_backend()
    if backend == "postgres":
        return open_configured_source_reader()

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
        url = get_futures_source_database_url()
        if url and url.startswith(("postgres://", "postgresql://")):
            raise SourceConfigError(
                "PostgreSQL URL is configured but sqlite fallback has no telegram messages",
                reason_code=REASON_POSTGRES_CONNECT_FAILED,
            )
    return reader
