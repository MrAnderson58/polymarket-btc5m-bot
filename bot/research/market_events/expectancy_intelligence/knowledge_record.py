"""Structured paper-trade knowledge records (Trade Intelligence memory)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema

logger = logging.getLogger(__name__)

_S55 = "market_events_trade_features_s55"


def _lessons_from_close(
    *,
    pnl_pct: float,
    gate_decision: str | None,
    expected_pnl: float | None,
    exit_reason: str | None,
) -> list[str]:
    lessons: list[str] = []
    if expected_pnl is not None and expected_pnl > 0 and pnl_pct < 0:
        lessons.append("Positive neighbor EV at entry but realized loss — review similarity pool.")
    if expected_pnl is not None and expected_pnl < 0 and pnl_pct > 0:
        lessons.append("Negative EV gate estimate but trade won — neighbor set may be stale.")
    if str(exit_reason or "").upper() in ("STOP", "SL"):
        lessons.append("Stopped out — check MAE vs stop placement.")
    if pnl_pct > 1.0:
        lessons.append("Strong winner — capture feature snapshot for future neighbors.")
    if not lessons:
        lessons.append("Routine close — no automatic lesson.")
    return lessons


def build_knowledge_payload(
    conn: Any,
    *,
    paper_trade_id: int,
    row: dict[str, Any],
    pnl_pct: float,
    pnl_usd: float,
    mfe_pct: float,
    mae_pct: float,
    exit_reason: str | None,
    result: str | None,
    duration_sec: int | None,
) -> dict[str, Any]:
    s55_row = None
    try:
        s55_row = conn.execute(
            f"""
            SELECT * FROM {_S55}
            WHERE paper_trade_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (int(paper_trade_id),),
        ).fetchone()
    except Exception:
        s55_row = None

    feats = row_to_features(s55_row) if s55_row else {}
    gate = str(s55_row["gate_decision"] or "") if s55_row else ""
    expected = float(s55_row["gate_expected_pnl_pct"] or 0.0) if s55_row else None

    return {
        "market_context": {
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "s40_signal_type": row.get("s40_signal_type"),
            "s40_signal_id": row.get("s40_signal_id"),
            "market_regime": feats.get("market_regime"),
            "fear_greed": feats.get("fear_greed"),
        },
        "entry": {
            "price": row.get("entry"),
            "ts": row.get("created_at"),
            "stop": row.get("stop"),
            "tp1": row.get("tp1"),
            "tp2": row.get("tp2"),
        },
        "exit": {
            "price": row.get("exit_price"),
            "ts": row.get("closed_at"),
            "exit_reason": exit_reason,
            "duration_sec": duration_sec,
        },
        "pnl": {"pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "result": result},
        "features": feats,
        "decision": {
            "gate_decision": gate,
            "expected_pnl_pct": expected,
            "similar_count": s55_row["similar_count"] if s55_row else None,
        },
        "outcome": {"result": result, "exit_reason": exit_reason},
        "mfe_mae": {"mfe_pct": mfe_pct, "mae_pct": mae_pct},
        "reason": exit_reason or gate or "close",
        "lessons": _lessons_from_close(
            pnl_pct=pnl_pct,
            gate_decision=gate,
            expected_pnl=expected,
            exit_reason=exit_reason,
        ),
    }


def record_paper_trade_knowledge(
    conn: Any,
    *,
    paper_trade_id: int,
    row: dict[str, Any],
    pnl_pct: float,
    pnl_usd: float,
    mfe_pct: float,
    mae_pct: float,
    exit_reason: str | None,
    result: str | None,
    duration_sec: int | None,
    now: int | None = None,
) -> None:
    """Upsert structured knowledge for a closed paper trade (diagnostics only)."""
    now = int(now if now is not None else time.time())
    try:
        ensure_trade_intelligence_schema(conn)
        payload = build_knowledge_payload(
            conn,
            paper_trade_id=paper_trade_id,
            row=row,
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            exit_reason=exit_reason,
            result=result,
            duration_sec=duration_sec,
        )
        blob = json.dumps(payload, default=str)
        existing = conn.execute(
            "SELECT id FROM ti_paper_knowledge WHERE paper_trade_id = ?",
            (int(paper_trade_id),),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE ti_paper_knowledge SET knowledge_json = ?, updated_at = ?
                WHERE paper_trade_id = ?
                """,
                (blob, now, int(paper_trade_id)),
            )
        else:
            conn.execute(
                """
                INSERT INTO ti_paper_knowledge (paper_trade_id, knowledge_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(paper_trade_id), blob, now, now),
            )
    except Exception as exc:
        logger.warning("paper knowledge record failed: %s", exc)
