"""Phase G.0 — market_events database backends (PostgreSQL production, SQLite tests)."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse

from bot.research.market_events.db_config import (
    MarketEventsDbConfig,
    resolve_market_events_db_config,
)

T = TypeVar("T")

BUSY_TIMEOUT_MS = 10_000
_LOCK_RETRY_INITIAL_MS = 50
_LOCK_RETRY_MAX_TOTAL_MS = 10_000


class MarketEventsDbError(RuntimeError):
    pass


def connection_is_postgres(conn: Any) -> bool:
    return isinstance(conn, PostgresBackend)


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _adapt_sql_placeholders(sql: str, params: tuple | list | None) -> str:
    if not params:
        return sql
    return sql.replace("?", "%s")


def is_database_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "could not obtain lock" in msg or "deadlock detected" in msg:
        return True
    if isinstance(exc, sqlite3.OperationalError):
        return "database is locked" in msg or "database table is locked" in msg
    return False


def lock_retry_sleep_schedule() -> list[float]:
    schedule: list[float] = []
    ms = _LOCK_RETRY_INITIAL_MS
    total_ms = 0
    while total_ms < _LOCK_RETRY_MAX_TOTAL_MS:
        step = min(ms, _LOCK_RETRY_MAX_TOTAL_MS - total_ms)
        if step <= 0:
            break
        schedule.append(step / 1000.0)
        total_ms += step
        ms = min(ms * 2, 6400)
    return schedule


def retry_on_db_locked(fn: Callable[[], T]) -> T:
    schedule = lock_retry_sleep_schedule()
    last_exc: BaseException | None = None
    for attempt in range(len(schedule) + 1):
        try:
            return fn()
        except (sqlite3.OperationalError, Exception) as exc:
            if not is_database_locked(exc):
                raise
            last_exc = exc
            if attempt >= len(schedule):
                break
            time.sleep(schedule[attempt])
    assert last_exc is not None
    raise last_exc


class PostgresBackend:
    """PostgreSQL backend — duck-types sqlite3 connection API."""

    def __init__(self, conn) -> None:
        import psycopg2.extras
        self._conn = conn
        self._extras = psycopg2.extras

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        if params:
            cur.execute(_adapt_sql_placeholders(sql, params), params)
        else:
            cur.execute(sql)
        return _PgCursor(cur)

    def executescript(self, ddl: str) -> None:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                self.execute(s)

    def insert_returning_id(self, sql: str, params: tuple | list | None = None) -> int:
        pg_sql = _adapt_sql_placeholders(sql, params or ()).rstrip().rstrip(";")
        if "RETURNING" not in pg_sql.upper():
            pg_sql += " RETURNING id"
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.execute(pg_sql, params or ())
        row = cur.fetchone()
        if not row or row.get("id") is None:
            raise MarketEventsDbError("INSERT RETURNING id did not return a row")
        return int(row["id"])

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class _PgCursor:
    def __init__(self, cur) -> None:
        self._cur = cur
        self.lastrowid: int | None = None

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class SQLiteBackend:
    """Thin wrapper so both backends share executescript()."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple | list | None = None):
        return self._conn.execute(sql, params or ())

    def executescript(self, ddl: str) -> None:
        self._conn.executescript(ddl)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._conn.row_factory = value


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """SQLite-only pragmas — never called for PostgreSQL."""
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(journal).lower() == "wal":
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")


def ensure_wal_enabled(db_path: Path | None = None) -> str:
    """SQLite-only: switch to WAL once before workers."""
    path = db_path or ensure_db_dir()

    def _enable() -> str:
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
        try:
            current = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(current).lower() == "wal":
                return "wal"
            result = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            return str(result).lower()
        finally:
            conn.close()

    return retry_on_db_locked(_enable)


def ensure_db_initialized(cfg: MarketEventsDbConfig | None = None) -> str:
    """One-shot init before workers: WAL for SQLite, connect check for PostgreSQL."""
    cfg = cfg or resolve_market_events_db_config()
    if cfg.backend == "postgresql":
        with market_events_connection(url=cfg.url) as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            if not row:
                raise MarketEventsDbError("PostgreSQL connectivity check failed")
        return "postgresql"
    return ensure_wal_enabled(cfg.sqlite_path)


def ensure_db_dir() -> Path:
    cfg = resolve_market_events_db_config()
    cfg.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return cfg.sqlite_path


def _connect_postgres(dsn: str) -> PostgresBackend:
    try:
        import psycopg2
    except ImportError as exc:
        raise MarketEventsDbError("psycopg2 required for PostgreSQL market_events DB") from exc
    parsed = urlparse(dsn)
    try:
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            dbname=(parsed.path or "/").lstrip("/") or "market_events",
            connect_timeout=5,
        )
    except Exception as exc:
        raise MarketEventsDbError(f"PostgreSQL connection failed: {exc}") from exc
    conn.autocommit = False
    return PostgresBackend(conn)


def _connect_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
    return conn


@contextmanager
def market_events_connection(
    db_path: Path | None = None,
    *,
    url: str | None = None,
) -> Iterator[Any]:
    """Yield MarketEvents connection (PostgreSQL or SQLite).

    Precedence: explicit url > explicit db_path (sqlite) > env config.
    """
    cfg = resolve_market_events_db_config()

    if url is not None:
        dsn = url
    elif db_path is not None:
        dsn = f"sqlite:///{db_path}"
    else:
        dsn = cfg.url

    if cfg.postgres_url_configured and url is None and db_path is None:
        if not _is_postgres_url(dsn):
            raise MarketEventsDbError(
                "MARKET_EVENTS_DB_URL is PostgreSQL but resolved to non-PG URL",
            )

    if _is_postgres_url(dsn):
        wrapper = _connect_postgres(dsn)
        try:
            yield wrapper
            wrapper.commit()
        except Exception:
            wrapper.rollback()
            raise
        finally:
            wrapper._conn.close()
    else:
        path = Path(dsn.replace("sqlite:///", ""))
        conn = _connect_sqlite(path)
        try:
            yield conn
            retry_on_db_locked(conn.commit)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


@contextmanager
def with_retry_transaction(conn: Any) -> Iterator[Any]:
    if connection_is_postgres(conn):
        conn.execute("BEGIN")
    else:
        retry_on_db_locked(lambda: conn.execute("BEGIN IMMEDIATE"))
    try:
        yield conn
        retry_on_db_locked(conn.commit)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def execute_with_retry(conn: Any, sql: str, params: tuple | list = ()) -> Any:
    return retry_on_db_locked(lambda: conn.execute(sql, params))


def insert_returning_id(conn: Any, sql: str, params: tuple | list = ()) -> int:
    if isinstance(conn, PostgresBackend):
        return conn.insert_returning_id(sql, params)

    def _run() -> int:
        cur = conn.execute(sql, params)
        lid = cur.lastrowid
        if lid is None:
            raise RuntimeError("INSERT did not return lastrowid")
        return int(lid)

    return retry_on_db_locked(_run)


def format_db_info(db_path: Path | None = None) -> str:
    """Backward-compatible wrapper — delegates to db_tools."""
    from bot.research.market_events.db_tools import format_db_info as _fmt
    from bot.research.market_events.db_config import resolve_market_events_db_config
    cfg = resolve_market_events_db_config()
    if db_path is not None:
        from bot.research.market_events.db_config import MarketEventsDbConfig
        cfg = MarketEventsDbConfig(
            backend="sqlite",
            url=f"sqlite:///{db_path}",
            sqlite_path=db_path,
            config_source="explicit_path",
            postgres_url_configured=False,
        )
    return _fmt(cfg)
