"""S2.0 persistence — store Market / News / Decision agent outputs for analysis."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


def persist_decision_run_s20(
    conn: Any,
    *,
    symbol: str,
    market: dict[str, Any],
    news: dict[str, Any],
    decision: dict[str, Any],
    telegram_rendered: str | None = None,
) -> int:
    """Insert run + three agent rows. Returns run id."""
    now = int(time.time())
    run_uuid = str(uuid.uuid4())
    cur = conn.execute(
        """
        INSERT INTO market_decision_runs_s20 (
          run_uuid, symbol, decision, probability, confidence,
          summary, risks_json, telegram_rendered, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_uuid,
            symbol.upper(),
            str(decision.get("decision") or "FLAT"),
            int(decision.get("probability") or 0),
            float(decision.get("confidence") or 0),
            str(decision.get("summary") or ""),
            json.dumps(decision.get("risks") or [], ensure_ascii=False),
            telegram_rendered,
            now,
        ),
    )
    run_id = int(cur.lastrowid)

    for agent_name, payload in (
        ("market", market),
        ("news", news),
        ("decision", decision),
    ):
        conn.execute(
            """
            INSERT INTO market_decision_agent_outputs_s20 (
              run_id, agent_name, payload_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (run_id, agent_name, json.dumps(payload, ensure_ascii=False), now),
        )
    return run_id


def latest_decision_run_s20(conn: Any, symbol: str | None = None) -> dict[str, Any] | None:
    if symbol:
        row = conn.execute(
            """
            SELECT * FROM market_decision_runs_s20
            WHERE symbol = ? ORDER BY id DESC LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM market_decision_runs_s20 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return dict(row)
