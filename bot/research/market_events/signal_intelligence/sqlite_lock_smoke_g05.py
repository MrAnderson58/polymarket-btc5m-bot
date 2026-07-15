"""Phase G.0.5 — Concurrent RO command smoke test (zero database is locked)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)


def _writer_pulse(stop: threading.Event, errors: list[str]) -> None:
    """Simulate observe/g3 writers with short write bursts."""
    from bot.research.market_events.db import market_events_connection, with_retry_transaction

    while not stop.is_set():
        try:
            with market_events_connection() as conn:
                with with_retry_transaction(conn):
                    conn.execute(
                        """
                        INSERT INTO market_events_command_trace_g351 (
                          message_id, command, stage, status, reason, latency_ms, created_at
                        ) VALUES (?, '/g05-writer', 'PULSE', 'PASS', NULL, 1, ?)
                        """,
                        (None, int(time.time())),
                    )
        except Exception as exc:
            errors.append(f"writer: {exc}")
        stop.wait(0.02)


def _run_command(cmd: str) -> tuple[str, bool, str | None]:
    from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
        handle_market_events_command,
    )
    try:
        result = handle_market_events_command(cmd)
        if not result.ok and "database is locked" in (result.reply_text or "").lower():
            return cmd, False, result.reply_text
        return cmd, True, None
    except Exception as exc:
        return cmd, False, str(exc)


def run_sqlite_lock_smoke_g05(
    *,
    per_command: int = 100,
    writer_threads: int = 3,
) -> dict[str, Any]:
    """100× /status,/market,/health under concurrent writers → 0 locked errors."""
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations
    from bot.research.market_events.sqlite_manager_g05 import get_last_locked_diag

    with market_events_connection() as conn:
        apply_migrations(conn)
        conn.commit()

    stop = threading.Event()
    writer_errors: list[str] = []
    writers = [
        threading.Thread(target=_writer_pulse, args=(stop, writer_errors), name=f"g05-w{i}", daemon=True)
        for i in range(writer_threads)
    ]
    for w in writers:
        w.start()

    commands = ["/status", "/market", "/health", "/help"] * per_command
    # also fold /top and RO decision for lock coverage
    commands.extend(["/top"] * per_command)
    commands.extend(["/decision BTC"] * max(10, per_command // 10))

    locked = 0
    failed = 0
    ok = 0
    samples: list[str] = []
    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futs = [pool.submit(_run_command, cmd) for cmd in commands]
            for fut in as_completed(futs):
                cmd, success, err = fut.result()
                if success:
                    ok += 1
                else:
                    failed += 1
                    if err and "database is locked" in err.lower():
                        locked += 1
                    if err and len(samples) < 5:
                        samples.append(f"{cmd}: {err}")
    finally:
        stop.set()
        for w in writers:
            w.join(timeout=2.0)

    elapsed = time.perf_counter() - t0
    return {
        "ok": ok,
        "failed": failed,
        "locked": locked,
        "total": len(commands),
        "writer_errors": len(writer_errors),
        "writer_locked": sum(1 for e in writer_errors if "database is locked" in e.lower()),
        "elapsed_sec": round(elapsed, 2),
        "samples": samples,
        "last_locked_diag": get_last_locked_diag(),
        "pass": locked == 0 and failed == 0,
    }


def format_sqlite_lock_smoke_g05(*, per_command: int = 100) -> str:
    stats = run_sqlite_lock_smoke_g05(per_command=per_command)
    lines = [
        "SQLite Lock Smoke",
        "",
        f"Commands total {stats['total']}",
        f"OK {stats['ok']}",
        f"Failed {stats['failed']}",
        f"database is locked {stats['locked']}",
        f"Writer errors {stats['writer_errors']} (locked={stats['writer_locked']})",
        f"Elapsed {stats['elapsed_sec']}s",
        "",
        "Overall",
        "PASS" if stats["pass"] else "FAIL",
    ]
    if stats["samples"]:
        lines.extend(["", "Samples", *stats["samples"]])
    return "\n".join(lines)
