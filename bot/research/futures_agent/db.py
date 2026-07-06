"""Futures agent database — PostgreSQL primary, SQLite for tests/dev."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from bot.research.futures_agent.config import AGENT_TABLE_ALLOWLIST, get_agent_database_url, get_agent_sqlite_fallback_path


class AgentDbError(RuntimeError):
    pass


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:")


def resolve_agent_url() -> str:
    url = get_agent_database_url()
    if url:
        return url
    fallback = get_agent_sqlite_fallback_path()
    if fallback:
        return f"sqlite:///{fallback}"
    return "sqlite:///data/futures_agent.db"


@contextmanager
def agent_connection(url: str | None = None) -> Iterator[Any]:
    """Yield a DB connection (psycopg2 or sqlite3)."""
    dsn = url or resolve_agent_url()
    if _is_postgres_url(dsn):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise AgentDbError("psycopg2 required for PostgreSQL agent DB") from exc
        parsed = urlparse(dsn)
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            dbname=(parsed.path or "/").lstrip("/") or "trading_ai",
            connect_timeout=5,
        )
        conn.autocommit = False
        try:
            yield _PgConnWrapper(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        path = dsn.replace("sqlite:///", "")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class _PgConnWrapper:
    """Normalize psycopg2 to sqlite-like execute(fetchone) interface."""

    def __init__(self, conn) -> None:
        import psycopg2.extras
        self._conn = conn
        self._extras = psycopg2.extras

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        pg_sql = sql.replace("?", "%s")
        upper = sql.strip().upper()
        if upper.startswith("INSERT INTO") and "RETURNING" not in upper:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
            cur.execute(pg_sql, params or ())
            row = cur.fetchone()
            return _PgCursor(cur, lastrowid=int(row["id"]) if row else None)
        cur.execute(pg_sql, params or ())
        return _PgCursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class _PgCursor:
    def __init__(self, cur, lastrowid: int | None = None) -> None:
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


def validate_write_table(table_name: str) -> None:
    if table_name not in AGENT_TABLE_ALLOWLIST:
        raise AgentDbError(f"Write to table {table_name!r} not allowed for agent adapter")
