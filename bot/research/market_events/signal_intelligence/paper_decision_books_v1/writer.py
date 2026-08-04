"""Serialized journal writer — single writer, large batches, one commit."""

from __future__ import annotations

import threading
import time
from typing import Any, Sequence

from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    JOURNAL_TABLE,
    ensure_decision_journal_schema,
)

_JOURNAL_MUTEX = threading.RLock()
_DEFAULT_CHUNK = 25_000


def _payload_tuple(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(r["trade_id"]),
        r.get("symbol"),
        r.get("opened_at"),
        r.get("decision"),
        r.get("book"),
        int(r.get("accepted") or 0),
        r.get("direction"),
        r.get("confidence"),
        r.get("timeline_similarity"),
        r.get("fingerprint_similarity"),
        r.get("dna"),
        r.get("rules"),
        r.get("edge"),
        r.get("replay"),
        r.get("brain"),
        r.get("causality"),
        r.get("decision_rank"),
        r.get("reasons_json"),
        r.get("historical_wr"),
        r.get("historical_pf"),
        r.get("historical_ev"),
        r.get("result"),
        r.get("pnl"),
        int(r.get("created_at") or time.time()),
    )


_UPSERT_SQL = f"""
INSERT INTO {JOURNAL_TABLE} (
    trade_id, symbol, opened_at, decision, book, accepted, direction,
    confidence, timeline_similarity, fingerprint_similarity,
    dna, rules, edge, replay, brain, causality,
    decision_rank, reasons_json,
    historical_wr, historical_pf, historical_ev,
    result, pnl, created_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?, ?
)
ON CONFLICT(trade_id, book) DO UPDATE SET
    decision=excluded.decision,
    accepted=excluded.accepted,
    direction=excluded.direction,
    confidence=excluded.confidence,
    timeline_similarity=excluded.timeline_similarity,
    fingerprint_similarity=excluded.fingerprint_similarity,
    dna=excluded.dna,
    rules=excluded.rules,
    edge=excluded.edge,
    replay=excluded.replay,
    brain=excluded.brain,
    causality=excluded.causality,
    decision_rank=excluded.decision_rank,
    reasons_json=excluded.reasons_json,
    historical_wr=excluded.historical_wr,
    historical_pf=excluded.historical_pf,
    historical_ev=excluded.historical_ev,
    result=excluded.result,
    pnl=excluded.pnl,
    created_at=excluded.created_at
"""


def insert_journal_batch(
    conn: Any,
    rows: Sequence[dict[str, Any]],
    *,
    commit: bool = False,
    ensure_schema: bool = False,
) -> int:
    """
    Batched upsert. By default does NOT commit (caller owns the transaction).
    No per-row commits. No N+1.
    """
    if not rows:
        return 0
    if ensure_schema:
        ensure_decision_journal_schema(conn)
    payload = [_payload_tuple(r) for r in rows]
    conn.executemany(_UPSERT_SQL, payload)
    if commit:
        conn.commit()
    return len(payload)


def replace_journal_atomic(
    conn: Any,
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = _DEFAULT_CHUNK,
) -> int:
    """
    Single-writer full journal rebuild:
    - process + cross-process lock
    - BEGIN IMMEDIATE
    - DELETE + chunked executemany
    - one COMMIT
    """
    with _JOURNAL_MUTEX:
        with research_write_lock():
            ensure_decision_journal_schema(conn)
            try:
                conn.execute("BEGIN IMMEDIATE")
            except Exception:
                # already in a transaction
                pass
            conn.execute(f"DELETE FROM {JOURNAL_TABLE}")
            n = 0
            buf: list[dict[str, Any]] = []
            for r in rows:
                buf.append(r)
                if len(buf) >= chunk_size:
                    n += insert_journal_batch(conn, buf, commit=False, ensure_schema=False)
                    buf = []
            if buf:
                n += insert_journal_batch(conn, buf, commit=False, ensure_schema=False)
            conn.commit()
            return n


def clear_journal(conn: Any, *, commit: bool = True) -> None:
    with _JOURNAL_MUTEX:
        with research_write_lock():
            ensure_decision_journal_schema(conn)
            conn.execute(f"DELETE FROM {JOURNAL_TABLE}")
            if commit:
                try:
                    conn.commit()
                except Exception:
                    pass


__all__ = [
    "clear_journal",
    "insert_journal_batch",
    "replace_journal_atomic",
]
