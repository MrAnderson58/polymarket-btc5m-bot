"""Phase E.1 database connection — isolated SQLite with concurrent-write hardening."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH

T = TypeVar("T")

BUSY_TIMEOUT_MS = 10_000
_LOCK_RETRY_INITIAL_MS = 50
_LOCK_RETRY_MAX_TOTAL_MS = 10_000


def is_database_locked(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


def lock_retry_sleep_schedule() -> list[float]:
    """Exponential backoff: 50, 100, 200, … ms until cumulative ~10s."""
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
    """Retry callable on sqlite3 database lock with exponential backoff."""
    schedule = lock_retry_sleep_schedule()
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(len(schedule) + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_database_locked(exc):
                raise
            last_exc = exc
            if attempt >= len(schedule):
                break
            time.sleep(schedule[attempt])
    assert last_exc is not None
    raise last_exc


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """Per-connection pragmas — never switches journal_mode (see ensure_wal_enabled)."""
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(journal).lower() == "wal":
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")


def ensure_wal_enabled(db_path: Path | None = None) -> str:
    """Switch database to WAL once — call from migrate/start-all before workers."""
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


def format_db_info(db_path: Path | None = None) -> str:
    """Human-readable SQLite configuration and WAL file status."""
    path = db_path or ensure_db_dir()
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")

    lines = [
        "MARKET EVENTS DATABASE INFO",
        "",
        f"path: {path}",
        f"exists: {path.exists()}",
        f"wal_file: {'present' if wal_path.exists() else 'absent'} ({wal_path})",
        f"shm_file: {'present' if shm_path.exists() else 'absent'} ({shm_path})",
        "",
        f"sqlite_version: {sqlite3.sqlite_version}",
        "",
    ]

    if not path.exists():
        lines.append("(database file does not exist yet)")
        return "\n".join(lines)

    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        apply_sqlite_pragmas(conn)
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
        db_list = conn.execute("PRAGMA database_list").fetchall()
    finally:
        conn.close()

    lines.extend([
        f"journal_mode: {journal}",
        f"busy_timeout: {timeout}",
        f"foreign_keys: {fk}",
        f"page_size: {page_size}",
        f"cache_size: {cache_size}",
        "",
        "database_list:",
    ])
    for row in db_list:
        lines.append(f"  seq={row[0]} name={row[1]!r} file={row[2]!r}")
    return "\n".join(lines)


def ensure_db_dir() -> Path:
    path = MARKET_EVENTS_DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def market_events_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or ensure_db_dir()
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
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
    """BEGIN IMMEDIATE transaction with lock retries on commit."""
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


def execute_with_retry(conn: Any, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
    """INSERT/UPDATE/DELETE with automatic lock retry."""
    return retry_on_db_locked(lambda: conn.execute(sql, params))


def insert_returning_id(conn: Any, sql: str, params: tuple | list) -> int:
    """INSERT with lastrowid and automatic lock retry."""

    def _run() -> int:
        cur = conn.execute(sql, params)
        lid = cur.lastrowid
        if lid is None:
            raise RuntimeError("INSERT did not return lastrowid")
        return int(lid)

    return retry_on_db_locked(_run)


def connection_is_postgres(conn: Any) -> bool:
    """market_events DB is SQLite-only; PG wrapper passthrough for shared helpers."""
    try:
        from bot.research.futures_agent.db import connection_is_postgres as _fa_pg
        return _fa_pg(conn)
    except Exception:
        return False
