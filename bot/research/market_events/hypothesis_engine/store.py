"""Persist research hypotheses and evidence rows."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.hypothesis_engine.schema import (
    STATUS_VALIDATED,
    ensure_hypothesis_engine_schema,
)
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema


def _now() -> int:
    return int(time.time())


def _s(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def record_hypothesis_history(
    conn: Any,
    *,
    hypothesis_key: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    note: str | None = None,
    now: int | None = None,
) -> None:
    """Mirror status/score changes into knowledge_history for audit trail."""
    ensure_knowledge_engine_schema(conn)
    if _s(old_value) == _s(new_value):
        return
    from bot.research.market_events.research_db_session import knowledge_history_write_guard

    try:
        with knowledge_history_write_guard():
            conn.execute(
                """
                INSERT INTO knowledge_history (
                  entity_type, entity_key, field_name, old_value, new_value, note, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "hypothesis",
                    hypothesis_key,
                    field_name,
                    _s(old_value),
                    _s(new_value),
                    note,
                    int(now if now is not None else _now()),
                ),
            )
    except Exception:
        pass


def upsert_hypothesis(
    conn: Any,
    *,
    hypothesis_key: str,
    title: str,
    description: str,
    generated_from: str,
    confidence: float,
    priority: float,
    evidence_score: int,
    sample_size: int,
    status: str,
    evidence: list[dict[str, Any]],
    now: int | None = None,
) -> int:
    """Insert or update a hypothesis and replace its evidence rows. Returns id."""
    ensure_hypothesis_engine_schema(conn)
    ts = int(now if now is not None else _now())
    existing = conn.execute(
        "SELECT * FROM research_hypotheses WHERE hypothesis_key = ?",
        (hypothesis_key,),
    ).fetchone()

    validated_at = None
    if status == STATUS_VALIDATED:
        validated_at = ts

    if existing:
        hid = int(existing["id"])
        for field, new_v in (
            ("title", title),
            ("confidence", confidence),
            ("priority", priority),
            ("evidence_score", evidence_score),
            ("sample_size", sample_size),
            ("status", status),
        ):
            old_v = existing[field] if field in existing.keys() else None
            record_hypothesis_history(
                conn,
                hypothesis_key=hypothesis_key,
                field_name=field,
                old_value=old_v,
                new_value=new_v,
                note="hypothesis-engine",
                now=ts,
            )
        conn.execute(
            """
            UPDATE research_hypotheses SET
              title=?, description=?, generated_from=?,
              confidence=?, priority=?, evidence_score=?, sample_size=?,
              status=?, validated_at=COALESCE(?, validated_at), last_checked_at=?
            WHERE id=?
            """,
            (
                title,
                description,
                generated_from,
                confidence,
                priority,
                evidence_score,
                sample_size,
                status,
                validated_at,
                ts,
                hid,
            ),
        )
        conn.execute("DELETE FROM hypothesis_evidence WHERE hypothesis_id = ?", (hid,))
    else:
        cur = conn.execute(
            """
            INSERT INTO research_hypotheses (
              hypothesis_key, title, description, generated_from,
              confidence, priority, evidence_score, sample_size,
              status, created_at, validated_at, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis_key,
                title,
                description,
                generated_from,
                confidence,
                priority,
                evidence_score,
                sample_size,
                status,
                ts,
                validated_at,
                ts,
            ),
        )
        hid = int(cur.lastrowid)
        record_hypothesis_history(
            conn,
            hypothesis_key=hypothesis_key,
            field_name="created",
            old_value=None,
            new_value=status,
            note="hypothesis-engine first seen",
            now=ts,
        )

    for ev in evidence:
        conn.execute(
            """
            INSERT INTO hypothesis_evidence (
              hypothesis_id, source_type, source_name, reference, weight
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                hid,
                str(ev.get("source_type") or ""),
                str(ev.get("source_name") or ""),
                ev.get("reference"),
                float(ev.get("weight") or 1.0),
            ),
        )
    return hid


def list_hypotheses(conn: Any) -> list[dict[str, Any]]:
    ensure_hypothesis_engine_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT * FROM research_hypotheses
            ORDER BY priority DESC, confidence DESC, id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def list_evidence(conn: Any, hypothesis_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT * FROM hypothesis_evidence
            WHERE hypothesis_id = ?
            ORDER BY weight DESC, id ASC
            """,
            (hypothesis_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def load_hypothesis_bundle(conn: Any) -> list[dict[str, Any]]:
    hyps = list_hypotheses(conn)
    out: list[dict[str, Any]] = []
    for h in hyps:
        item = dict(h)
        item["evidence"] = list_evidence(conn, int(h["id"]))
        out.append(item)
    return out
