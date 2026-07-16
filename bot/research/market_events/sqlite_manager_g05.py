"""Phase G.0.5 — Unified SQLite connection manager (lock root-cause elimination).

All market_events SQLite opens MUST go through this module.
Direct sqlite3.connect() elsewhere in market_events is forbidden.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 10_000
SLOW_TX_MS = 100.0

_registry_lock = threading.Lock()
_active: dict[int, "ConnectionLeaseG05"] = {}
_conn_seq = 0
_last_locked_diag: dict[str, Any] | None = None


@dataclass
class ConnectionLeaseG05:
    conn_id: int
    pid: int
    thread_id: int
    thread_name: str
    readonly: bool
    wal: bool
    path: str
    caller: str
    opened_at: float
    last_sql: str = ""
    last_sql_at: float | None = None
    tx_started_at: float | None = None
    tx_mode: str | None = None  # BEGIN / BEGIN IMMEDIATE / implicit
    waiting: bool = False
    stack: str = ""


def _caller_frame(skip: int = 3) -> str:
    stack = traceback.extract_stack(limit=skip + 8)
    # walk from oldest→newest, pick first frame outside this module
    for fr in reversed(stack[:-1]):
        if "sqlite_manager_g05" in fr.filename or "db.py" in fr.filename and "market_events" in fr.filename:
            # allow one level from db.py wrappers; prefer deeper app frames
            if "sqlite_manager_g05" in fr.filename:
                continue
        if fr.filename.endswith("contextlib.py"):
            continue
        return f"{Path(fr.filename).name}:{fr.lineno}:{fr.name}"
    if stack:
        fr = stack[-2]
        return f"{Path(fr.filename).name}:{fr.lineno}:{fr.name}"
    return "unknown"


def _is_wal(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return bool(row and str(row[0]).lower() == "wal")
    except Exception:
        return False


def apply_sqlite_pragmas(conn: sqlite3.Connection, *, readonly: bool = False) -> None:
    if not readonly:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal).lower() == "wal":
            conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if not readonly:
        conn.execute("PRAGMA foreign_keys=ON")


def _register(lease: ConnectionLeaseG05) -> None:
    with _registry_lock:
        _active[lease.conn_id] = lease


def _unregister(conn_id: int) -> ConnectionLeaseG05 | None:
    with _registry_lock:
        return _active.pop(conn_id, None)


def get_active_leases() -> list[ConnectionLeaseG05]:
    with _registry_lock:
        return list(_active.values())


def get_last_locked_diag() -> dict[str, Any] | None:
    return _last_locked_diag


class TracedCursorG05:
    def __init__(self, cursor: sqlite3.Cursor, lease: ConnectionLeaseG05) -> None:
        self._cursor = cursor
        self._lease = lease

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size: int | None = None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)


class TracedConnectionG05:
    """sqlite3.Connection wrapper: SQL + transaction duration tracing."""

    def __init__(self, conn: sqlite3.Connection, lease: ConnectionLeaseG05) -> None:
        self._conn = conn
        self._lease = lease

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._conn.row_factory = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def _note_sql(self, sql: str) -> None:
        preview = " ".join(sql.strip().split())[:240]
        self._lease.last_sql = preview
        self._lease.last_sql_at = time.time()
        upper = preview.upper()
        if upper.startswith("BEGIN"):
            self._lease.tx_started_at = time.time()
            self._lease.tx_mode = preview.split(";")[0][:40]
            logger.info(
                "BEGIN sqlite conn_id=%s pid=%s mode=%s caller=%s",
                self._lease.conn_id, self._lease.pid, self._lease.tx_mode, self._lease.caller,
            )
        elif upper.startswith("COMMIT") or upper == "COMMIT":
            self._finish_tx("COMMIT")
        elif upper.startswith("ROLLBACK"):
            self._finish_tx("ROLLBACK")

    def _finish_tx(self, kind: str) -> None:
        started = self._lease.tx_started_at
        elapsed_ms = (time.time() - started) * 1000 if started else 0.0
        if elapsed_ms >= SLOW_TX_MS or kind == "COMMIT":
            level = logging.WARNING if elapsed_ms >= SLOW_TX_MS else logging.INFO
            logger.log(
                level,
                "%s sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                kind, self._lease.conn_id, self._lease.pid, elapsed_ms,
                self._lease.last_sql, self._lease.caller,
            )
        self._lease.tx_started_at = None
        self._lease.tx_mode = None

    def execute(self, sql: str, params: Any = ()) -> TracedCursorG05:
        self._note_sql(sql)
        try:
            cur = self._conn.execute(sql, params)
            return TracedCursorG05(cur, self._lease)
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease)
            raise

    def executescript(self, sql: str) -> sqlite3.Cursor:
        self._note_sql(sql[:240])
        try:
            return self._conn.executescript(sql)
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease)
            raise

    def commit(self) -> None:
        if self._lease.readonly:
            logger.error(
                "WRITE FORBIDDEN on readonly sqlite conn_id=%s caller=%s",
                self._lease.conn_id, self._lease.caller,
            )
            raise sqlite3.OperationalError("attempt to write a readonly database (g05)")
        # Implicit write tx: measure commit wall time only (not full connection lifetime)
        if self._lease.tx_started_at is None:
            self._lease.tx_started_at = time.time()
            self._lease.tx_mode = "implicit"
        try:
            t0 = time.time()
            self._conn.commit()
            elapsed_ms = (time.time() - t0) * 1000
            level = logging.WARNING if elapsed_ms >= SLOW_TX_MS else logging.INFO
            logger.log(
                level,
                "COMMIT sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                self._lease.conn_id, self._lease.pid, elapsed_ms,
                self._lease.last_sql, self._lease.caller,
            )
            self._lease.tx_started_at = None
            self._lease.tx_mode = None
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease)
            raise

    def rollback(self) -> None:
        self._note_sql("ROLLBACK")
        try:
            self._conn.rollback()
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease)
            raise

    def close(self) -> None:
        duration_ms = (time.time() - self._lease.opened_at) * 1000
        logger.info(
            "CLOSE sqlite conn_id=%s pid=%s thread=%s duration=%.1fms readonly=%s caller=%s",
            self._lease.conn_id,
            self._lease.pid,
            self._lease.thread_name,
            duration_ms,
            "yes" if self._lease.readonly else "no",
            self._lease.caller,
        )
        _unregister(self._lease.conn_id)
        self._conn.close()


def connect_sqlite(
    path: Path | str,
    *,
    readonly: bool = False,
    timeout_sec: float | None = None,
    create_dirs: bool = True,
) -> TracedConnectionG05:
    """SOLE entry point for market_events SQLite connects."""
    global _conn_seq
    path = Path(path)
    timeout = timeout_sec if timeout_sec is not None else BUSY_TIMEOUT_MS / 1000.0
    caller = _caller_frame()

    if readonly:
        if not path.exists():
            raise FileNotFoundError(f"SQLite DB not found for readonly open: {path}")
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        raw = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        if create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(path), timeout=timeout)

    raw.row_factory = sqlite3.Row
    apply_sqlite_pragmas(raw, readonly=readonly)
    wal = _is_wal(raw)

    with _registry_lock:
        _conn_seq += 1
        conn_id = _conn_seq

    lease = ConnectionLeaseG05(
        conn_id=conn_id,
        pid=os.getpid(),
        thread_id=threading.get_ident(),
        thread_name=threading.current_thread().name,
        readonly=readonly,
        wal=wal,
        path=str(path.resolve()) if path.exists() else str(path),
        caller=caller,
        opened_at=time.time(),
        stack="".join(traceback.format_stack(limit=12)),
    )
    _register(lease)
    logger.info(
        "OPEN sqlite conn_id=%s pid=%s thread=%s caller=%s readonly=%s wal=%s path=%s",
        conn_id,
        lease.pid,
        lease.thread_name,
        caller,
        "yes" if readonly else "no",
        "yes" if wal else "no",
        path.name,
    )
    return TracedConnectionG05(raw, lease)


@contextmanager
def sqlite_connection(
    path: Path | str,
    *,
    readonly: bool = False,
    commit_on_exit: bool = True,
) -> Iterator[TracedConnectionG05]:
    conn = connect_sqlite(path, readonly=readonly)
    try:
        yield conn
        if commit_on_exit and not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()


def maybe_log_database_locked(exc: BaseException, *, lease: ConnectionLeaseG05 | None = None) -> None:
    global _last_locked_diag
    msg = str(exc).lower()
    if "database is locked" not in msg and "database table is locked" not in msg:
        return

    writers = [l for l in get_active_leases() if not l.readonly]
    owner = lease or (writers[0] if writers else None)
    diag = {
        "error": str(exc),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "owner_pid": owner.pid if owner else None,
        "owner_conn_id": owner.conn_id if owner else None,
        "last_sql": owner.last_sql if owner else None,
        "last_transaction": owner.tx_mode if owner else None,
        "tx_elapsed_ms": (
            round((time.time() - owner.tx_started_at) * 1000, 1)
            if owner and owner.tx_started_at
            else None
        ),
        "caller": owner.caller if owner else _caller_frame(),
        "stacktrace": "".join(traceback.format_stack(limit=20)),
        "active": [
            {
                "conn_id": l.conn_id,
                "pid": l.pid,
                "thread": l.thread_name,
                "readonly": l.readonly,
                "sql": l.last_sql,
                "tx": l.tx_mode,
                "duration_ms": round((time.time() - l.opened_at) * 1000, 1),
                "caller": l.caller,
            }
            for l in get_active_leases()
        ],
    }
    _last_locked_diag = diag
    logger.error(
        "database is locked diag last_sql=%s last_tx=%s owner_pid=%s caller=%s\n%s",
        diag["last_sql"],
        diag["last_transaction"],
        diag["owner_pid"],
        diag["caller"],
        diag["stacktrace"],
    )


def format_sqlite_lock_debug_g05() -> str:
    now = time.time()
    leases = get_active_leases()
    writers = [l for l in leases if not l.readonly]
    readers = [l for l in leases if l.readonly]
    lines = [
        "SQLite Lock Debug",
        "",
        f"Active connections: {len(leases)}",
        f"Writers: {len(writers)}",
        f"Readers: {len(readers)}",
        "",
    ]
    if writers:
        # current writer = longest open writer or one with open tx
        writers_sorted = sorted(
            writers,
            key=lambda l: (0 if l.tx_started_at else 1, -(l.tx_started_at or l.opened_at)),
        )
        w = writers_sorted[0]
        started = w.tx_started_at or w.opened_at
        lines.extend([
            "Current writer",
            f"PID {w.pid}",
            f"Thread {w.thread_name}",
            f"SQL {w.last_sql or '—'}",
            f"Started {time.strftime('%H:%M:%S', time.localtime(started))}",
            f"Duration {round((now - started) * 1000, 1)}ms",
            f"Caller {w.caller}",
            f"WAL {'yes' if w.wal else 'no'}",
            "",
        ])
    else:
        lines.extend(["Current writer", "none", ""])

    lines.append("Waiting readers")
    if readers:
        for r in readers:
            lines.append(
                f"  pid={r.pid} thread={r.thread_name} duration="
                f"{round((now - r.opened_at) * 1000, 1)}ms caller={r.caller}",
            )
    else:
        lines.append("  none")
    lines.append("")
    lines.append("Waiting writers")
    wait_w = [l for l in writers if l.waiting]
    if wait_w:
        for w in wait_w:
            lines.append(f"  pid={w.pid} thread={w.thread_name} sql={w.last_sql}")
    else:
        # other writers besides primary
        extras = writers[1:] if len(writers) > 1 else []
        if extras:
            for w in extras:
                lines.append(
                    f"  pid={w.pid} thread={w.thread_name} duration="
                    f"{round((now - w.opened_at) * 1000, 1)}ms caller={w.caller}",
                )
        else:
            lines.append("  none")

    if _last_locked_diag:
        lines.extend([
            "",
            "Last database is locked",
            f"error {_last_locked_diag.get('error')}",
            f"owner_pid {_last_locked_diag.get('owner_pid')}",
            f"last_sql {_last_locked_diag.get('last_sql')}",
            f"last_transaction {_last_locked_diag.get('last_transaction')}",
        ])
    return "\n".join(lines)


# Pure read-only telegram commands — zero SQLite writes (no traces).
PURE_READONLY_COMMANDS = frozenset({
    "/status",
    "/market",
    "/health",
    "/top",
    "/help",
    "/decision",
    "/explain-decision",
    "/pattern",
    "/news",
    "/review",
    "/paper",
    "/learning-status",
})
