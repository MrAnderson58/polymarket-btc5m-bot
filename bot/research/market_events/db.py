"""Phase E.1 database connection — isolated SQLite."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH


def ensure_db_dir() -> Path:
    path = MARKET_EVENTS_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def market_events_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or ensure_db_dir()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_returning_id(conn: Any, sql: str, params: tuple | list) -> int:
    cur = conn.execute(sql, params)
    lid = cur.lastrowid
    if lid is None:
        raise RuntimeError("INSERT did not return lastrowid")
    return int(lid)


def connection_is_postgres(conn: Any) -> bool:
    """market_events DB is SQLite-only; PG wrapper passthrough for shared helpers."""
    try:
        from bot.research.futures_agent.db import connection_is_postgres as _fa_pg
        return _fa_pg(conn)
    except Exception:
        return False
