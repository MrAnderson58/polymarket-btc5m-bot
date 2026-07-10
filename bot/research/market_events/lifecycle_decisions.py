"""Lifecycle decision persistence — additive E.3."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.reversal_confirmation import ReversalResult


def persist_reversal_decisions(
    conn: Any,
    *,
    event_id: int,
    decision_ts: int,
    results: list[ReversalResult],
) -> None:
    for rev in results:
        status = "confirmed" if rev.confirmed else "rejected"
        conn.execute(
            """
            INSERT INTO market_event_lifecycle_decisions (
              event_id, decision_ts, stage, status, reason, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                decision_ts,
                f"REVERSAL_{rev.variant}",
                status,
                rev.notes or status,
                json.dumps({
                    "confirmed": rev.confirmed,
                    "confirm_ts": rev.confirm_ts,
                    "confirm_price": rev.confirm_price,
                }),
                int(time.time()),
            ),
        )


def load_lifecycle_decisions(conn: Any, event_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM market_event_lifecycle_decisions
        WHERE event_id = ? ORDER BY decision_ts, stage
        """,
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]
