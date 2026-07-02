"""Live trade journal — why we entered/exited and what AI layers said."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.portfolio.approval import LiveApprovalResult


def record_entry_decision(
    conn: sqlite3.Connection,
    *,
    trade_id: int | None,
    market_slug: str,
    strategy_name: str,
    side: str,
    entry_price: float | None,
    approval: LiveApprovalResult,
    allowed: bool,
    block_reason: str | None = None,
) -> None:
    payload = {
        "phase": "entry",
        "trade_id": trade_id,
        "market_slug": market_slug,
        "strategy_name": strategy_name,
        "side": side,
        "entry_price": entry_price,
        "allowed": allowed,
        "block_reason": block_reason,
        "ai": {
            "decision": approval.ai_decision,
            "confidence_pct": approval.ai_confidence_pct,
        },
        "brain": approval.brain,
        "scientist": approval.scientist,
        "review": approval.review,
        "risk": approval.risk,
        "summary": approval.summary,
    }
    conn.execute(
        """
        INSERT INTO live_journal (trade_id, market_slug, phase, payload_json, created_at)
        VALUES (?, ?, 'entry', ?, ?)
        """,
        (
            trade_id,
            market_slug,
            json.dumps(payload, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def record_exit(
    conn: sqlite3.Connection,
    *,
    trade_id: int,
    market_slug: str,
    exit_reason: str,
    pnl_usdc: float | None,
    notes: str = "",
) -> None:
    payload = {
        "phase": "exit",
        "trade_id": trade_id,
        "exit_reason": exit_reason,
        "pnl_usdc": pnl_usdc,
        "notes": notes,
    }
    conn.execute(
        """
        INSERT INTO live_journal (trade_id, market_slug, phase, payload_json, created_at)
        VALUES (?, ?, 'exit', ?, ?)
        """,
        (
            trade_id,
            market_slug,
            json.dumps(payload, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def recent_entries(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, trade_id, market_slug, phase, payload_json, created_at
        FROM live_journal
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "trade_id": row["trade_id"],
                "market_slug": row["market_slug"],
                "phase": row["phase"],
                "created_at": row["created_at"],
                **json.loads(row["payload_json"] or "{}"),
            }
        )
    return out
