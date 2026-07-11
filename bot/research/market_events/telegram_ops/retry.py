"""Retry failed Telegram deliveries from delivery log."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.alert_engine.telegram_delivery import (
    STATUS_SENT,
    _backoff_delay,
    deliver_telegram,
)


def retry_failed_deliveries(
    conn: Any,
    *,
    limit: int = 100,
    retry_all: bool = False,
) -> list[str]:
    """Retry failed rows from market_event_telegram_delivery_log."""
    sql = """
        SELECT id, event_id, alert_type, message_text, message_preview, chat_id
        FROM market_event_telegram_delivery_log
        WHERE status = 'failed'
        ORDER BY created_at DESC
    """
    if retry_all:
        rows = conn.execute(sql).fetchall()
    else:
        rows = conn.execute(f"{sql} LIMIT ?", (limit,)).fetchall()

    lines: list[str] = [
        f"Retrying {len(rows)} failed delivery log entries...",
        "",
    ]
    sent = 0
    failed = 0
    skipped = 0

    for idx, row in enumerate(rows):
        text = row["message_text"] or row["message_preview"]
        if not text:
            skipped += 1
            lines.append(f"✗ id={row['id']} {row['alert_type']}: skipped (no message body)")
            continue

        if idx > 0:
            time.sleep(_backoff_delay(min(idx - 1, 5)))

        result = deliver_telegram(
            text,
            conn=conn,
            alert_type=str(row["alert_type"]),
            event_id=int(row["event_id"]),
            chat_id=row["chat_id"],
        )
        if result.ok and result.http_code == 200:
            sent += 1
            lines.append(
                f"✓ id={row['id']} {row['alert_type']}: sent (msg_id={result.message_id})",
            )
        else:
            failed += 1
            reason = result.error or f"http_{result.http_code}"
            lines.append(f"✗ id={row['id']} {row['alert_type']}: failed ({reason})")

    lines.extend([
        "",
        f"Summary: sent={sent} failed={failed} skipped={skipped}",
    ])
    return lines
