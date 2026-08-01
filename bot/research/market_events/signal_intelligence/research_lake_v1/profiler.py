"""SQL query profiler for Research Lake builds."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SqlEvent:
    sql: str
    rows: int
    elapsed_sec: float
    params_preview: str = ""


@dataclass
class SqlProfiler:
    slow_threshold_sec: float = 1.0
    events: list[SqlEvent] = field(default_factory=list)
    _active: bool = True
    print_each: bool = False
    _print_fn: Any = None

    def record(self, sql: str, *, rows: int, elapsed_sec: float, params: Any = None) -> None:
        preview = ""
        if params is not None:
            try:
                preview = repr(params)[:120]
            except Exception:
                preview = ""
        sql_norm = " ".join(str(sql).split())
        ev = SqlEvent(
            sql=sql_norm,
            rows=int(rows),
            elapsed_sec=float(elapsed_sec),
            params_preview=preview,
        )
        self.events.append(ev)
        if self.print_each and self._print_fn is not None:
            slow = " SLOW" if ev.elapsed_sec >= self.slow_threshold_sec else ""
            self._print_fn(
                f"[sql{slow}] {ev.elapsed_sec:.4f}s rows={ev.rows} "
                f"{sql_norm[:180]}{'…' if len(sql_norm) > 180 else ''}"
            )

    def wrap_connection(self, conn: Any) -> Any:
        """Monkey-patch conn.execute / executemany / executescript for timing."""
        if getattr(conn, "_rlake_profiled", False):
            return conn
        orig_execute = conn.execute
        orig_executemany = conn.executemany
        orig_executescript = getattr(conn, "executescript", None)
        profiler = self

        def execute(sql: str, params: Any = ()):
            t0 = time.perf_counter()
            cur = orig_execute(sql, params)
            # rowcount may be -1 for SELECT; fetchall not forced here
            rows = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            profiler.record(sql, rows=rows, elapsed_sec=time.perf_counter() - t0, params=params)
            return cur

        def executemany(sql: str, seq_of_params: Any):
            t0 = time.perf_counter()
            # materialize length cheaply when possible
            try:
                n = len(seq_of_params)  # type: ignore[arg-type]
            except Exception:
                seq_of_params = list(seq_of_params)
                n = len(seq_of_params)
            cur = orig_executemany(sql, seq_of_params)
            rows = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else n
            profiler.record(sql, rows=rows, elapsed_sec=time.perf_counter() - t0, params=f"batch[{n}]")
            return cur

        def executescript(sql: str):
            t0 = time.perf_counter()
            cur = orig_executescript(sql)
            profiler.record(sql, rows=0, elapsed_sec=time.perf_counter() - t0)
            return cur

        conn.execute = execute  # type: ignore[method-assign]
        conn.executemany = executemany  # type: ignore[method-assign]
        if orig_executescript is not None:
            conn.executescript = executescript  # type: ignore[method-assign]
        conn._rlake_profiled = True  # type: ignore[attr-defined]
        conn._rlake_profiler = profiler  # type: ignore[attr-defined]
        return conn

    def timed(self, label: str, fn: Any) -> Any:
        t0 = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - t0
        rows = 0
        if isinstance(result, list):
            rows = len(result)
        elif isinstance(result, dict):
            rows = len(result)
        self.record(f"-- {label}", rows=rows, elapsed_sec=elapsed)
        return result

    def slow_events(self) -> list[SqlEvent]:
        return [e for e in self.events if e.elapsed_sec >= self.slow_threshold_sec]

    def top_slow(self, n: int = 20) -> list[SqlEvent]:
        return sorted(self.events, key=lambda e: -e.elapsed_sec)[:n]

    def explain_slow(self, conn: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Prefer true slow queries; if none, still EXPLAIN top wall-time SELECTs for the report.
        candidates = [e for e in self.top_slow(50) if e.elapsed_sec >= self.slow_threshold_sec]
        if not candidates:
            candidates = [
                e for e in self.top_slow(10)
                if not e.sql.startswith("--")
                and e.sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE"))
            ]
        for ev in candidates:
            sql = ev.sql
            if sql.startswith("--"):
                continue
            key = sql[:200]
            if key in seen:
                continue
            seen.add(key)
            plan_rows: list[str] = []
            try:
                if not sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")):
                    continue
                # Replace unbound ? so EXPLAIN still plans (SQLite accepts literals).
                explain_sql = sql
                cur = conn.execute(f"EXPLAIN QUERY PLAN {explain_sql}", ())
                for r in cur.fetchall():
                    plan_rows.append(" | ".join(str(x) for x in r))
            except Exception as exc:
                # Retry with dummy bound params for statements that still carry ?
                try:
                    n_q = sql.count("?")
                    cur = conn.execute(f"EXPLAIN QUERY PLAN {sql}", tuple(0 for _ in range(n_q)))
                    plan_rows = [" | ".join(str(x) for x in r) for r in cur.fetchall()]
                except Exception as exc2:
                    plan_rows.append(f"(explain failed: {exc}; retry: {exc2})")
            out.append({
                "sql": sql,
                "elapsed_sec": round(ev.elapsed_sec, 4),
                "rows": ev.rows,
                "plan": plan_rows,
            })
            if len(out) >= 20:
                break
        return out

    def summary(self) -> dict[str, Any]:
        total = sum(e.elapsed_sec for e in self.events)
        return {
            "n_queries": len(self.events),
            "total_sql_sec": round(total, 4),
            "n_slow": len(self.slow_events()),
            "top_slow": [
                {
                    "sql": e.sql[:500],
                    "rows": e.rows,
                    "elapsed_sec": round(e.elapsed_sec, 4),
                    "params": e.params_preview,
                }
                for e in self.top_slow(20)
            ],
            "all_queries": [
                {
                    "sql": e.sql[:500],
                    "rows": e.rows,
                    "elapsed_sec": round(e.elapsed_sec, 4),
                    "params": e.params_preview,
                }
                for e in self.events
            ],
        }


def profiled_fetchall(profiler: SqlProfiler, conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    t0 = time.perf_counter()
    rows = list(conn.execute(sql, params).fetchall())
    # If connection already wrapped, execute already recorded; still OK to double-count lightly.
    # Prefer recording with actual row count when wrap didn't know SELECT size:
    profiler.record(sql, rows=len(rows), elapsed_sec=time.perf_counter() - t0, params=params)
    return rows


__all__ = ["SqlEvent", "SqlProfiler", "profiled_fetchall"]
