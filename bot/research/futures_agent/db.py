"""Futures agent database — PostgreSQL primary, SQLite for tests/dev."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from bot.research.futures_agent.config import AGENT_TABLE_ALLOWLIST
from bot.research.futures_agent.env_bootstrap import resolve_agent_db_config


class AgentDbError(RuntimeError):
    pass


def connection_is_postgres(conn: Any) -> bool:
    """True when conn is a PostgreSQL agent wrapper."""
    return isinstance(conn, _PgConnWrapper)


def _is_postgres_dsn(dsn: str) -> bool:
    return dsn.startswith(("postgres://", "postgresql://"))


def resolve_agent_url() -> str:
    return resolve_agent_db_config().url


def _adapt_sql_placeholders(sql: str, params: tuple | list | None) -> str:
    """Convert SQLite ? placeholders to psycopg2 %s only when binding params."""
    if not params:
        return sql
    return sql.replace("?", "%s")


@contextmanager
def agent_connection(url: str | None = None) -> Iterator[Any]:
    """Yield a DB connection.

    Precedence: explicit url argument > configured env/.env > SQLite fallback.
    Explicit sqlite:/// URLs work even when project .env configures PostgreSQL.
    """
    cfg = resolve_agent_db_config()
    dsn = url if url is not None else cfg.url

    if (
        url is None
        and cfg.postgres_url_configured
        and not _is_postgres_dsn(dsn)
    ):
        raise AgentDbError(
            "FUTURES_AGENT_DATABASE_URL is configured for PostgreSQL but resolved to non-PostgreSQL URL. "
            f"Resolved: {dsn!r}"
        )

    if _is_postgres_dsn(dsn):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:
            raise AgentDbError("psycopg2 required for PostgreSQL agent DB") from exc
        parsed = urlparse(dsn)
        try:
            conn = psycopg2.connect(
                host=parsed.hostname,
                port=parsed.port or 5432,
                user=parsed.username,
                password=parsed.password,
                dbname=(parsed.path or "/").lstrip("/") or "trading_ai",
                connect_timeout=5,
            )
        except Exception as exc:
            raise AgentDbError(
                f"PostgreSQL connection failed for database "
                f"{(parsed.path or '/').lstrip('/') or 'trading_ai'}: {exc}"
            ) from exc
        conn.autocommit = False
        wrapper = _PgConnWrapper(conn)
        try:
            yield wrapper
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
    """PostgreSQL wrapper — execute() never auto-appends RETURNING."""

    def __init__(self, conn) -> None:
        import psycopg2.extras
        self._conn = conn
        self._extras = psycopg2.extras

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        if params:
            pg_sql = _adapt_sql_placeholders(sql, params)
            cur.execute(pg_sql, params)
        else:
            # No bound params: pass SQL verbatim so literal % (e.g. LIKE 'foo_%') is preserved.
            cur.execute(sql)
        return _PgCursor(cur)

    def insert_returning_id(self, sql: str, params: tuple | list | None = None) -> int:
        pg_sql = _adapt_sql_placeholders(sql, params).rstrip().rstrip(";")
        if "RETURNING" not in pg_sql.upper():
            pg_sql += " RETURNING id"
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.execute(pg_sql, params or ())
        row = cur.fetchone()
        if not row or row.get("id") is None:
            raise AgentDbError("INSERT RETURNING id did not return a row")
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


def insert_returning_id(conn: Any, sql: str, params: tuple | list | None = None) -> int:
    """Insert row and return generated id (PostgreSQL or SQLite)."""
    if isinstance(conn, _PgConnWrapper):
        return conn.insert_returning_id(sql, params)
    cur = conn.execute(sql, params or ())
    lid = getattr(cur, "lastrowid", None)
    if lid is None:
        raise AgentDbError("SQLite INSERT did not set lastrowid")
    return int(lid)


def insert_telegram_research_bridge(
    conn: Any,
    *,
    input_id: int,
    post_id: int,
    bridge_status: str,
    content_hash: str,
    bridged_at: int,
) -> bool:
    """Insert bridge row idempotently. Returns True when a new row is created."""
    validate_write_table("futures_agent_telegram_research_bridge")
    params = (input_id, post_id, bridge_status, content_hash, bridged_at)
    if connection_is_postgres(conn):
        row = conn.execute(
            """
            INSERT INTO futures_agent_telegram_research_bridge (
              input_id, post_id, bridge_status, content_hash, bridged_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (input_id) DO NOTHING
            RETURNING input_id
            """,
            params,
        ).fetchone()
        return row is not None

    existing = conn.execute(
        "SELECT input_id FROM futures_agent_telegram_research_bridge WHERE input_id = ?",
        (input_id,),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """
        INSERT INTO futures_agent_telegram_research_bridge (
          input_id, post_id, bridge_status, content_hash, bridged_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        params,
    )
    return True


def validate_write_table(table_name: str) -> None:
    if table_name not in AGENT_TABLE_ALLOWLIST:
        raise AgentDbError(f"Write to table {table_name!r} not allowed for agent adapter")
