"""S44.1 — batched source-health writes (same connection as collectors)."""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection

logger = logging.getLogger(__name__)


def record_source_health(
    *,
    source_type: str,
    source_name: str,
    status: str,
    last_update: int | None = None,
    error: str | None = None,
    latency_ms: float | None = None,
    items: int = 0,
    conn: Any | None = None,
) -> None:
    """Upsert health row. Prefer passing collector `conn` to avoid lock contention."""
    now = int(time.time())
    params = (
        source_type[:32],
        source_name[:120],
        status[:32],
        int(last_update or now),
        (error or "")[:1000] or None,
        float(latency_ms) if latency_ms is not None else None,
        int(items),
        now,
    )
    sql = """
        INSERT INTO market_source_health (
          source_type, source_name, status, last_update, error,
          latency_ms, items, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_type, source_name) DO UPDATE SET
          status=excluded.status,
          last_update=excluded.last_update,
          error=excluded.error,
          latency_ms=excluded.latency_ms,
          items=excluded.items,
          updated_at=excluded.updated_at
    """
    try:
        if conn is not None:
            execute_with_retry(conn, sql, params)
            return
        with market_events_connection() as c:
            execute_with_retry(c, sql, params)
            c.commit()
    except Exception:
        logger.exception(
            "failed to record source health type=%s name=%s",
            source_type,
            source_name,
        )


def fetch_source_health(conn: Any | None = None) -> list[dict[str, Any]]:
    def _read(c: Any) -> list[dict[str, Any]]:
        rows = c.execute(
            """
            SELECT source_type, source_name, status, last_update, error,
                   latency_ms, items, updated_at
            FROM market_source_health
            ORDER BY source_type, source_name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    if conn is not None:
        return _read(conn)
    with market_events_connection() as c:
        return _read(c)


def format_source_health_report(rows: list[dict[str, Any]] | None = None) -> str:
    rows = rows if rows is not None else fetch_source_health()
    lines = ["# Source Health", ""]
    if not rows:
        lines.append("(no collector health rows yet)")
        return "\n".join(lines) + "\n"
    lines.append("| Source | Type | Status | Last update | Latency ms | Errors | Items |")
    lines.append("|---|---|---|---|---|---|---|")
    now = int(time.time())
    for r in rows:
        age = now - int(r.get("last_update") or 0)
        err = (r.get("error") or "—").replace("|", "/")[:80]
        lines.append(
            f"| {r.get('source_name')} | {r.get('source_type')} | "
            f"{r.get('status')} | {age}s ago | "
            f"{r.get('latency_ms') if r.get('latency_ms') is not None else '—'} | "
            f"{err} | {r.get('items') or 0} |"
        )
    return "\n".join(lines) + "\n"
