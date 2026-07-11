"""Phase E.5.1 — DB integrity and growth checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH
from bot.research.market_events.system_validation.types import ValidationResult

_GROWTH_WARN_ROWS = {
    "market_event_alert_log": 50_000,
    "market_event_analysis_jobs": 20_000,
    "market_events_price_observations": 500_000,
    "market_events": 10_000,
}


def check_db_integrity(conn: Any) -> ValidationResult:
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        ok = row and row[0] == "ok"
    except Exception as exc:
        return ValidationResult("db_integrity", "FAIL", f"quick_check error: {exc}")
    return ValidationResult(
        "db_integrity",
        "PASS" if ok else "FAIL",
        "SQLite quick_check ok" if ok else f"quick_check: {row}",
    )


def check_db_file_size() -> ValidationResult:
    path = Path(MARKET_EVENTS_DATABASE_PATH)
    if not path.exists():
        return ValidationResult("db_file_size", "WARN", f"DB file missing: {path}")
    size_mb = path.stat().st_size / (1024 * 1024)
    status = "PASS" if size_mb < 500 else "WARN"
    return ValidationResult(
        "db_file_size",
        status,
        f"{path.name}: {size_mb:.2f} MB",
        {"size_bytes": path.stat().st_size, "path": str(path)},
    )


def check_table_growth(conn: Any) -> ValidationResult:
    warnings: list[str] = []
    counts: dict[str, int] = {}
    for table in (
        "market_events",
        "market_event_alert_log",
        "market_event_analysis_jobs",
        "market_events_price_observations",
        "paper_strategy_runs",
        "market_events_pending_shocks",
    ):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = int(n)
            limit = _GROWTH_WARN_ROWS.get(table)
            if limit and n > limit:
                warnings.append(f"{table}={n} (>{limit})")
        except Exception:
            pass
    status = "WARN" if warnings else "PASS"
    detail = "; ".join(warnings) if warnings else "table counts within expected bounds"
    return ValidationResult("table_growth", status, detail, counts)


def check_stale_jobs(conn: Any) -> ValidationResult:
    stale_running = conn.execute(
        """
        SELECT COUNT(*) FROM market_event_analysis_jobs
        WHERE status = 'running' AND updated_at < ?
        """,
        (int(__import__("time").time()) - 3600,),
    ).fetchone()
    n = int(stale_running[0] if stale_running else 0)
    status = "WARN" if n else "PASS"
    return ValidationResult(
        "stale_ai_jobs",
        status,
        f"stale running AI jobs (>1h): {n}",
        {"stale_running": n},
    )
