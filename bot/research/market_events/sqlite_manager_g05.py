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

BUSY_TIMEOUT_MS = 30_000  # wait up to 30s inside SQLite for writers
SLOW_TX_MS = 100.0
WRITE_TRACE_MAX = 8_000
WRITE_TRACE_FILE = Path(__file__).resolve().parents[3] / "logs" / "me-sqlite-write-trace.jsonl"
_TRACE_WRITES = os.getenv("ME_SQLITE_TRACE_WRITES", "1").lower() in ("1", "true", "yes")
_TRACE_COMMITS_INFO = os.getenv("ME_SQLITE_TRACE_COMMITS", "0").lower() in ("1", "true", "yes")

_LOCK_RETRY_INITIAL_MS = 50
_LOCK_RETRY_MAX_TOTAL_MS = 15_000

_registry_lock = threading.Lock()
_active: dict[int, "ConnectionLeaseG05"] = {}
_conn_seq = 0
_last_locked_diag: dict[str, Any] | None = None
_write_events: list[dict[str, Any]] = []
_write_events_lock = threading.Lock()
_lock_events: list[dict[str, Any]] = []


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
    tx_mode: str | None = None  # BEGIN / BEGIN IMMEDIATE / implicit
    tx_started_at: float | None = None
    waiting: bool = False
    stack: str = ""
    dirty: bool = False
    last_retry_count: int = 0


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
    """Always set busy_timeout; enable WAL on writable connections."""
    if not readonly:
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() == "wal":
                conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            # Another connection may be mid-transaction; busy_timeout still applies.
            pass
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if not readonly:
        conn.execute("PRAGMA foreign_keys=ON")


def _is_locked_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if isinstance(exc, sqlite3.OperationalError):
        return "database is locked" in msg or "database table is locked" in msg
    return "database is locked" in msg or "database table is locked" in msg


def _busy_retry_sleep_schedule() -> list[float]:
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


def call_with_busy_retry(fn, *, lease: "ConnectionLeaseG05 | None" = None, sql: str | None = None):
    """Retry SQLITE_BUSY beyond PRAGMA busy_timeout (application-level backoff)."""
    schedule = _busy_retry_sleep_schedule()
    last_exc: BaseException | None = None
    for attempt in range(len(schedule) + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_locked_error(exc):
                raise
            maybe_log_database_locked(exc, lease=lease, sql=sql)
            last_exc = exc
            if lease is not None:
                lease.last_retry_count = attempt + 1
                lease.waiting = True
            if attempt >= len(schedule):
                break
            time.sleep(schedule[attempt])
    if lease is not None:
        lease.waiting = False
    assert last_exc is not None
    raise last_exc


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


def _sql_type(sql: str) -> str:
    token = (sql or "").strip().split(None, 1)
    return (token[0].upper() if token else "UNKNOWN")


def _sql_table(sql: str) -> str:
    import re

    preview = " ".join((sql or "").strip().split())
    patterns = (
        r"(?i)\bINTO\s+([A-Za-z0-9_]+)",
        r"(?i)\bUPDATE\s+([A-Za-z0-9_]+)",
        r"(?i)\bFROM\s+([A-Za-z0-9_]+)",
        r"(?i)\bTABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z0-9_]+)",
    )
    for pat in patterns:
        m = re.search(pat, preview)
        if m:
            return m.group(1)
    return "—"


def _is_dml_write(sql: str) -> bool:
    u = (sql or "").lstrip().upper()
    return u.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"))


def record_write_event(
    *,
    lease: ConnectionLeaseG05 | None,
    sql: str,
    duration_ms: float,
    retry_count: int = 0,
    kind: str = "execute",
    locked: bool = False,
) -> None:
    if not _TRACE_WRITES and not locked:
        return
    event = {
        "ts": time.time(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        "conn_id": lease.conn_id if lease else None,
        "caller": lease.caller if lease else _caller_frame(),
        "table": _sql_table(sql),
        "sql_type": _sql_type(sql),
        "kind": kind,
        "duration_ms": round(duration_ms, 3),
        "retry_count": int(retry_count),
        "locked": locked,
        "sql": " ".join((sql or "").strip().split())[:200],
    }
    with _write_events_lock:
        _write_events.append(event)
        if len(_write_events) > WRITE_TRACE_MAX:
            del _write_events[: len(_write_events) - WRITE_TRACE_MAX]
        if locked:
            _lock_events.append(event)
            if len(_lock_events) > 500:
                del _lock_events[: len(_lock_events) - 500]
    if locked or duration_ms >= SLOW_TX_MS:
        try:
            WRITE_TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(WRITE_TRACE_FILE, "a", encoding="utf-8") as fp:
                import json as _json
                fp.write(_json.dumps(event, separators=(",", ":")) + "\n")
        except Exception:
            pass


def get_write_events(*, since_ts: float | None = None) -> list[dict[str, Any]]:
    with _write_events_lock:
        rows = list(_write_events)
    if since_ts is None:
        return rows
    return [e for e in rows if float(e.get("ts") or 0) >= since_ts]


def get_recent_lock_events(*, lookback_sec: float = 300.0) -> list[dict[str, Any]]:
    cutoff = time.time() - lookback_sec
    with _write_events_lock:
        mem = [e for e in _lock_events if float(e.get("ts") or 0) >= cutoff]
    out = list(mem)
    try:
        if WRITE_TRACE_FILE.is_file():
            import json as _json
            for line in WRITE_TRACE_FILE.read_text(errors="ignore").splitlines()[-15000:]:
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                if ev.get("locked") and float(ev.get("ts") or 0) >= cutoff:
                    out.append(ev)
    except Exception:
        pass
    seen: set[tuple] = set()
    uniq: list[dict[str, Any]] = []
    for ev in sorted(out, key=lambda e: float(e.get("ts") or 0)):
        key = (ev.get("ts"), ev.get("pid"), ev.get("sql"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ev)
    return uniq


def cluster_lock_incidents(
    events: list[dict[str, Any]],
    *,
    gap_sec: float = 2.0,
) -> list[dict[str, Any]]:
    """One collision = first lock for (pid, table) within gap_sec."""
    items = sorted(events, key=lambda e: float(e.get("ts") or 0))
    last: dict[tuple[Any, Any], float] = {}
    out: list[dict[str, Any]] = []
    for ev in items:
        key = (ev.get("pid"), str(ev.get("table") or ""))
        ts = float(ev.get("ts") or 0)
        prev = last.get(key)
        if prev is None or (ts - prev) > gap_sec:
            out.append(ev)
        last[key] = ts
    return out


_INTEGRITY_FILE = Path(__file__).resolve().parents[3] / "logs" / "me-sqlite-integrity.jsonl"
_integrity_lock = threading.Lock()


def record_integrity_event(kind: str, *, detail: str = "") -> None:
    """Append integrity marker (deferred write / lost write / lost paper)."""
    event = {
        "ts": time.time(),
        "kind": str(kind),
        "detail": str(detail)[:240],
        "pid": os.getpid(),
    }
    try:
        _INTEGRITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _integrity_lock:
            with open(_INTEGRITY_FILE, "a", encoding="utf-8") as fp:
                import json as _json
                fp.write(_json.dumps(event, separators=(",", ":")) + "\n")
    except Exception:
        pass


def get_integrity_counts(*, lookback_sec: float = 300.0) -> dict[str, int]:
    cutoff = time.time() - lookback_sec
    counts = {"writes_lost": 0, "paper_trades_lost": 0, "mfe_mae_deferred": 0}
    try:
        if not _INTEGRITY_FILE.is_file():
            return counts
        import json as _json
        for line in _INTEGRITY_FILE.read_text(errors="ignore").splitlines()[-5000:]:
            try:
                ev = _json.loads(line)
            except Exception:
                continue
            if float(ev.get("ts") or 0) < cutoff:
                continue
            kind = str(ev.get("kind") or "")
            if kind in counts:
                counts[kind] += 1
            elif kind == "write_lost":
                counts["writes_lost"] += 1
            elif kind == "paper_trade_lost":
                counts["paper_trades_lost"] += 1
    except Exception:
        pass
    return counts


def summarize_lock_diagnostics(*, lookback_sec: float = 300.0) -> dict[str, Any]:
    """Doctor-facing lock breakdown: raw events vs retries vs real incidents."""
    raw_events = get_recent_lock_events(lookback_sec=lookback_sec)
    incidents = cluster_lock_incidents(raw_events, gap_sec=2.0)
    raw_n = len(raw_events)
    inc_n = len(incidents)
    retry_n = max(0, raw_n - inc_n)
    integrity = get_integrity_counts(lookback_sec=lookback_sec)
    return {
        "lookback_sec": float(lookback_sec),
        "raw_lock_events": raw_n,
        "retry_attempts": retry_n,
        "real_lock_incidents": inc_n,
        "writes_lost": int(integrity.get("writes_lost") or 0),
        "paper_trades_lost": int(integrity.get("paper_trades_lost") or 0),
        "mfe_mae_deferred": int(integrity.get("mfe_mae_deferred") or 0),
    }


def format_lock_diagnostics_block(summary: dict[str, Any] | None = None) -> list[str]:
    s = summary if summary is not None else summarize_lock_diagnostics()
    return [
        f"  Raw lock events ........ {int(s.get('raw_lock_events') or 0)}",
        f"  Retry attempts ......... {int(s.get('retry_attempts') or 0)}",
        f"  Real lock incidents .... {int(s.get('real_lock_incidents') or 0)}",
        f"  Writes lost ............ {int(s.get('writes_lost') or 0)}",
        f"  Paper trades lost ...... {int(s.get('paper_trades_lost') or 0)}",
    ]


def format_sqlite_contention_report(*, window_sec: float = 300.0) -> str:
    """Phase 1–3 contention timeline + per-table frequency."""
    import collections
    import json as _json

    now = time.time()
    cutoff = now - window_sec
    events = get_write_events(since_ts=cutoff)
    try:
        if WRITE_TRACE_FILE.is_file():
            for line in WRITE_TRACE_FILE.read_text(errors="ignore").splitlines()[-5000:]:
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                if float(ev.get("ts") or 0) >= cutoff:
                    events.append(ev)
    except Exception:
        pass

    locks = [e for e in events if e.get("locked")]
    writes = [e for e in events if e.get("sql_type") in ("INSERT", "UPDATE", "DELETE", "REPLACE")]
    by_table: dict[str, list[float]] = collections.defaultdict(list)
    lock_by_table: collections.Counter[str] = collections.Counter()
    for e in writes:
        by_table[str(e.get("table") or "—")].append(float(e.get("duration_ms") or 0))
    for e in locks:
        lock_by_table[str(e.get("table") or "—")] += 1

    lines = [
        "SQLite Contention Report",
        f"Window: {int(window_sec)}s",
        f"Write events: {len(writes)}",
        f"Lock events: {len(locks)}",
        "",
        "Per-table",
        f"{'table':<36} {'w/s':>8} {'avg_ms':>8} {'max_ms':>8} {'locks':>6}",
    ]
    for table, durs in sorted(by_table.items(), key=lambda kv: -len(kv[1])):
        n = len(durs)
        wps = n / window_sec if window_sec else 0.0
        avg = sum(durs) / n if n else 0.0
        mx = max(durs) if durs else 0.0
        lines.append(
            f"{table:<36} {wps:8.2f} {avg:8.2f} {mx:8.2f} {lock_by_table.get(table, 0):6d}",
        )
    lines.extend(["", "Recent lock timeline (up to 20)"])
    for e in locks[-20:]:
        ts = float(e.get("ts") or 0)
        stamp = time.strftime("%H:%M:%S", time.localtime(ts))
        lines.append(
            f"  {stamp} pid={e.get('pid')} table={e.get('table')} "
            f"retries={e.get('retry_count')} sql={str(e.get('sql'))[:80]}",
        )
    if not locks:
        lines.append("  (none)")
    lines.extend([
        "",
        "Overlap pattern (observed hot path)",
        "  shock-paper-core ──┐",
        "  shock-paper-tradfi ┼─► g3_ops_state (heartbeat×3/cycle) ─► COMMIT",
        "                    └─► near_miss / shadow (heartbeat flush)",
    ])
    return "\n".join(lines)


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
        if _is_dml_write(preview):
            self._lease.dirty = True
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
        if elapsed_ms >= SLOW_TX_MS:
            logger.warning(
                "%s sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                kind, self._lease.conn_id, self._lease.pid, elapsed_ms,
                self._lease.last_sql, self._lease.caller,
            )
        elif _TRACE_COMMITS_INFO and kind == "COMMIT":
            logger.info(
                "%s sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                kind, self._lease.conn_id, self._lease.pid, elapsed_ms,
                self._lease.last_sql, self._lease.caller,
            )
        self._lease.tx_started_at = None
        self._lease.tx_mode = None

    def executemany(self, sql: str, params_seq: Any) -> TracedCursorG05:
        self._note_sql(sql)
        t0 = time.time()

        def _run() -> TracedCursorG05:
            cur = self._conn.executemany(sql, params_seq)
            return TracedCursorG05(cur, self._lease)

        try:
            out = call_with_busy_retry(_run, lease=self._lease, sql=sql)
            record_write_event(
                lease=self._lease, sql=sql,
                duration_ms=(time.time() - t0) * 1000,
                retry_count=self._lease.last_retry_count, kind="executemany",
            )
            self._lease.last_retry_count = 0
            self._lease.waiting = False
            return out
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease, sql=sql, duration_ms=(time.time() - t0) * 1000)
            raise

    def execute(self, sql: str, params: Any = ()) -> TracedCursorG05:
        self._note_sql(sql)
        t0 = time.time()

        def _run() -> TracedCursorG05:
            cur = self._conn.execute(sql, params)
            return TracedCursorG05(cur, self._lease)

        try:
            out = call_with_busy_retry(_run, lease=self._lease, sql=sql)
            if _is_dml_write(sql):
                record_write_event(
                    lease=self._lease, sql=sql,
                    duration_ms=(time.time() - t0) * 1000,
                    retry_count=self._lease.last_retry_count, kind="execute",
                )
                self._lease.last_retry_count = 0
            self._lease.waiting = False
            return out
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease, sql=sql, duration_ms=(time.time() - t0) * 1000)
            raise

    def executescript(self, sql: str) -> sqlite3.Cursor:
        self._note_sql(sql[:240])

        def _run() -> sqlite3.Cursor:
            return self._conn.executescript(sql)

        try:
            return call_with_busy_retry(_run, lease=self._lease, sql=sql[:240])
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease, sql=sql[:240])
            raise

    def commit(self) -> None:
        if self._lease.readonly:
            logger.error(
                "WRITE FORBIDDEN on readonly sqlite conn_id=%s caller=%s",
                self._lease.conn_id, self._lease.caller,
            )
            raise sqlite3.OperationalError("attempt to write a readonly database (g05)")
        if not self._lease.dirty and self._lease.tx_started_at is None:
            return
        # Implicit write tx: measure commit wall time only (not full connection lifetime)
        if self._lease.tx_started_at is None:
            self._lease.tx_started_at = time.time()
            self._lease.tx_mode = "implicit"
        try:
            t0 = time.time()

            def _run() -> None:
                self._conn.commit()

            call_with_busy_retry(_run, lease=self._lease, sql=self._lease.last_sql or "COMMIT")
            elapsed_ms = (time.time() - t0) * 1000
            record_write_event(
                lease=self._lease,
                sql=self._lease.last_sql or "COMMIT",
                duration_ms=elapsed_ms,
                retry_count=self._lease.last_retry_count,
                kind="commit",
            )
            self._lease.last_retry_count = 0
            self._lease.waiting = False
            if elapsed_ms >= SLOW_TX_MS:
                logger.warning(
                    "COMMIT sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                    self._lease.conn_id, self._lease.pid, elapsed_ms,
                    self._lease.last_sql, self._lease.caller,
                )
            elif _TRACE_COMMITS_INFO:
                logger.info(
                    "COMMIT sqlite conn_id=%s pid=%s elapsed=%.1fms sql=%s caller=%s",
                    self._lease.conn_id, self._lease.pid, elapsed_ms,
                    self._lease.last_sql, self._lease.caller,
                )
            self._lease.tx_started_at = None
            self._lease.tx_mode = None
            self._lease.dirty = False
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease, sql=self._lease.last_sql or "COMMIT")
            raise

    def rollback(self) -> None:
        self._note_sql("ROLLBACK")
        try:
            self._conn.rollback()
            self._lease.dirty = False
        except Exception as exc:
            maybe_log_database_locked(exc, lease=self._lease, sql="ROLLBACK")
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


def maybe_log_database_locked(
    exc: BaseException,
    *,
    lease: ConnectionLeaseG05 | None = None,
    sql: str | None = None,
    duration_ms: float = 0.0,
) -> None:
    global _last_locked_diag
    msg = str(exc).lower()
    if "database is locked" not in msg and "database table is locked" not in msg:
        return

    writers = [l for l in get_active_leases() if not l.readonly]
    owner = lease or (writers[0] if writers else None)
    sql_preview = sql or (owner.last_sql if owner else None)
    diag = {
        "ts": time.time(),
        "error": str(exc),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "owner_pid": owner.pid if owner else None,
        "owner_conn_id": owner.conn_id if owner else None,
        "last_sql": sql_preview,
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
    record_write_event(
        lease=owner,
        sql=sql_preview or "—",
        duration_ms=duration_ms,
        retry_count=owner.last_retry_count if owner else 0,
        kind="lock",
        locked=True,
    )
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    logger.error(
        "%s database is locked diag last_sql=%s last_tx=%s owner_pid=%s caller=%s\n%s",
        stamp,
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
    "/learning-health",
    "/cost",
    "/history",
    "/artifacts",
    "/report",
    "/doctor",
    "/debug",
    "/btc",
    "/macro",
    "/sp500",
    "/events",
    "/narrative",
    "/context",
    "/signals",
    "/open",
    "/closed",
    "/stats",
    "/leaderboard",
    "/daily",
})
