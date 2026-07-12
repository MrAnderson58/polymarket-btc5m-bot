"""Phase F.3 — multi-stage entry tracking (WATCH → PREPARE → READY → ENTRY)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.confidence_f1 import ENTRY_READY

STAGE_WATCH = "WATCH"
STAGE_PREPARE = "PREPARE"
STAGE_READY = "READY"
STAGE_ENTRY = "ENTRY"

VALID_STAGES = frozenset({STAGE_WATCH, STAGE_PREPARE, STAGE_READY, STAGE_ENTRY})


@dataclass(frozen=True)
class EntryStageResult:
    stage: str
    reason: str
    wait_r2: bool
    wait_r3: bool
    scale_after_r2_pct: int | None
    scale_after_r3_pct: int | None


def _has_r2(confirmed: str | None) -> bool:
    rev = (confirmed or "").upper()
    return "R2" in rev or rev in ("R3", "R4", "R5")


def _has_r3(confirmed: str | None) -> bool:
    rev = (confirmed or "").upper()
    return "R3" in rev or rev in ("R4", "R5")


def compute_entry_stage(
    conn: Any,
    *,
    event_id: int,
    entry_recommendation: str | None = None,
    confidence: float = 0.0,
) -> EntryStageResult:
    pending = conn.execute(
        "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    confirmed = pending["confirmed_reversal"] if pending else None

    exh = conn.execute(
        """
        SELECT exhaustion_score, volume_expansion FROM market_events_exhaustion
        WHERE source_event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()

    entry = entry_recommendation or ""
    if entry == ENTRY_READY or (entry == ENTRY_READY and _has_r2(confirmed)):
        return EntryStageResult(
            stage=STAGE_ENTRY,
            reason="R2/R3 подтверждены, confidence достаточный",
            wait_r2=False,
            wait_r3=False,
            scale_after_r2_pct=25,
            scale_after_r3_pct=50,
        )

    if _has_r2(confirmed) and confidence >= 6.0:
        return EntryStageResult(
            stage=STAGE_READY,
            reason="R2 подтверждён — готовность к набору",
            wait_r2=False,
            wait_r3=True,
            scale_after_r2_pct=25,
            scale_after_r3_pct=50,
        )

    if exh and (
        float(exh["exhaustion_score"] or 0) >= 55
        or float(exh["volume_expansion"] or 0) >= 1.8
    ):
        return EntryStageResult(
            stage=STAGE_PREPARE,
            reason="Импульс теряет силу — первая стабилизация",
            wait_r2=True,
            wait_r3=True,
            scale_after_r2_pct=25,
            scale_after_r3_pct=50,
        )

    trend = conn.execute(
        "SELECT trend_score FROM market_events_trend_shock WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    if trend and float(trend["trend_score"] or 0) >= 40:
        return EntryStageResult(
            stage=STAGE_WATCH,
            reason="Trend shock — наблюдение за стабилизацией",
            wait_r2=True,
            wait_r3=True,
            scale_after_r2_pct=25,
            scale_after_r3_pct=50,
        )

    return EntryStageResult(
        stage=STAGE_WATCH,
        reason="Ожидание подтверждения разворота",
        wait_r2=True,
        wait_r3=True,
        scale_after_r2_pct=25,
        scale_after_r3_pct=50,
    )


def persist_entry_stage(conn: Any, event_id: int, stage: EntryStageResult) -> int:
    now = int(time.time())
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_entry_stages_f3 (
          event_id, stage, stage_reason, wait_r2, wait_r3,
          scale_after_r2_pct, scale_after_r3_pct, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          stage = excluded.stage,
          stage_reason = excluded.stage_reason,
          wait_r2 = excluded.wait_r2,
          wait_r3 = excluded.wait_r3,
          scale_after_r2_pct = excluded.scale_after_r2_pct,
          scale_after_r3_pct = excluded.scale_after_r3_pct,
          updated_at = excluded.updated_at
        """,
        (
            event_id, stage.stage, stage.reason,
            1 if stage.wait_r2 else 0,
            1 if stage.wait_r3 else 0,
            stage.scale_after_r2_pct,
            stage.scale_after_r3_pct,
            now,
        ),
    )


def run_entry_stage(conn: Any, event_id: int) -> EntryStageResult:
    from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2

    report = load_signal_report_f2(conn, event_id)
    conf = report.confidence_score if report else 0.0
    entry = report.entry_recommendation if report else None
    stage = compute_entry_stage(conn, event_id=event_id, entry_recommendation=entry, confidence=conf)
    persist_entry_stage(conn, event_id, stage)
    return stage


def load_entry_stage(conn: Any, event_id: int) -> EntryStageResult | None:
    row = conn.execute(
        "SELECT * FROM market_events_entry_stages_f3 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return EntryStageResult(
        stage=str(row["stage"]),
        reason=str(row["stage_reason"] or ""),
        wait_r2=bool(row["wait_r2"]),
        wait_r3=bool(row["wait_r3"]),
        scale_after_r2_pct=int(row["scale_after_r2_pct"]) if row["scale_after_r2_pct"] is not None else None,
        scale_after_r3_pct=int(row["scale_after_r3_pct"]) if row["scale_after_r3_pct"] is not None else None,
    )
