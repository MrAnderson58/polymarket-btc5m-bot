"""Phase F.5.1 — full signal pipeline trace for rejected / silent signals."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

from bot.research.market_events.db import insert_returning_id

STAGE_RAW = "RAW"
STAGE_PARSER = "Parser"
STAGE_VALIDATION = "Validation"
STAGE_SNAPSHOT = "Snapshot"
STAGE_AI = "AI"
STAGE_F1 = "F1"
STAGE_F2 = "F2"
STAGE_F3 = "F3"
STAGE_F4 = "F4"
STAGE_F5 = "F5"
STAGE_F7 = "F7"
STAGE_G1 = "G1"
STAGE_G2 = "G2"
STAGE_CONFIDENCE = "Confidence"
STAGE_TELEGRAM_FILTER = "Telegram filter"
STAGE_TELEGRAM = "Telegram"
STAGE_DASHBOARD = "Dashboard"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAILED"
STATUS_SKIP = "SKIPPED"
STATUS_NOT_SENT = "not sent"
STATUS_STORED = "stored"

INTELLIGENCE_STAGES = (STAGE_F1, STAGE_F2, STAGE_F3, STAGE_F4, STAGE_F5, STAGE_G1, STAGE_G2, STAGE_F7)

_TRACE_TABLE = "market_events_signal_trace_f51"


def record_trace(
    conn: Any,
    *,
    event_id: int,
    stage: str,
    status: str,
    message_id: int | None = None,
    reason: str | None = None,
    latency_ms: int | None = None,
) -> None:
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TRACE_TABLE} (
          event_id, message_id, stage, status, reason, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            message_id,
            stage,
            status,
            reason,
            latency_ms,
            int(time.time()),
        ),
    )


@contextmanager
def trace_stage(
    conn: Any,
    *,
    event_id: int,
    stage: str,
    message_id: int | None = None,
    skip_reason: str | None = None,
) -> Iterator[None]:
    if skip_reason is not None:
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=stage,
            status=STATUS_SKIP,
            reason=skip_reason,
        )
        yield
        return

    t0 = time.perf_counter()
    err: str | None = None
    try:
        yield
    except Exception as exc:
        err = str(exc) or exc.__class__.__name__
        raise
    finally:
        latency = int((time.perf_counter() - t0) * 1000)
        if err is not None:
            record_trace(
                conn,
                event_id=event_id,
                message_id=message_id,
                stage=stage,
                status=STATUS_FAIL,
                reason=err,
                latency_ms=latency,
            )
        else:
            record_trace(
                conn,
                event_id=event_id,
                message_id=message_id,
                stage=stage,
                status=STATUS_PASS,
                latency_ms=latency,
            )


def run_traced(
    conn: Any,
    *,
    event_id: int,
    stage: str,
    fn: Any,
    message_id: int | None = None,
    enabled: bool = True,
    skip_reason: str = "disabled",
) -> Any:
    """Run callable with trace; swallow errors like hooks.py."""
    if not enabled:
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=stage,
            status=STATUS_SKIP,
            reason=skip_reason,
        )
        return None

    t0 = time.perf_counter()
    try:
        result = fn()
        latency = int((time.perf_counter() - t0) * 1000)
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=stage,
            status=STATUS_PASS,
            latency_ms=latency,
        )
        return result
    except Exception as exc:
        latency = int((time.perf_counter() - t0) * 1000)
        reason = str(exc) or exc.__class__.__name__
        if stage == STAGE_AI and "timeout" in reason.lower():
            reason = "timeout"
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=stage,
            status=STATUS_FAIL,
            reason=reason,
            latency_ms=latency,
        )
        return None


def _linked_telegram(conn: Any, event_id: int) -> tuple[int | None, dict[str, Any]]:
    row = conn.execute(
        """
        SELECT source_record_id, context_json FROM market_event_context
        WHERE event_id = ? AND context_type = 'TELEGRAM_SIGNAL'
        ORDER BY context_ts DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None, {}
    ctx = json.loads(row["context_json"] or "{}")
    message_id: int | None = None
    try:
        message_id = int(row["source_record_id"])
    except (TypeError, ValueError):
        pass
    return message_id, ctx


def record_shock_received(conn: Any, *, event_id: int) -> int | None:
    message_id, _ = _linked_telegram(conn, event_id)
    record_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        stage=STAGE_RAW,
        status=STATUS_PASS,
        reason="received",
    )
    return message_id


def record_parser_validation_snapshot(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    message_id: int | None = None,
    snapshot_ok: bool = True,
) -> None:
    ev = conn.execute(
        "SELECT symbol, direction FROM market_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not ev:
        return

    if message_id is None:
        message_id, tg_ctx = _linked_telegram(conn, event_id)
    else:
        _, tg_ctx = _linked_telegram(conn, event_id)

    parse_symbol = tg_ctx.get("symbol") or ev["symbol"] or symbol
    parse_direction = tg_ctx.get("direction") or ev["direction"] or "?"

    if not parse_symbol or parse_symbol == "?":
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_PARSER,
            status=STATUS_FAIL,
            reason="unknown symbol",
        )
    else:
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_PARSER,
            status=STATUS_PASS,
            reason=f"symbol={parse_symbol}\ndirection={parse_direction}",
        )

    content_type = tg_ctx.get("content_type") or tg_ctx.get("taxonomy") or ""
    if content_type == "EXPLICIT_SIGNAL" or tg_ctx.get("explicit"):
        val_reason = "explicit signal"
        val_status = STATUS_PASS
    elif ev["symbol"]:
        val_reason = "shock validated"
        val_status = STATUS_PASS
    else:
        val_reason = "not an explicit signal"
        val_status = STATUS_SKIP

    record_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        stage=STAGE_VALIDATION,
        status=val_status,
        reason=val_reason,
    )

    if not snapshot_ok:
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_SNAPSHOT,
            status=STATUS_FAIL,
            reason="symbol not found on exchange",
        )
        return

    resolver = conn.execute(
        """
        SELECT exchange_attempts_json FROM market_event_exchange_symbols
        WHERE canonical_symbol = ?
        """,
        (parse_symbol.upper(),),
    ).fetchone()

    venue_found: dict[str, bool] = {"Binance": False, "Bybit": False}
    if resolver:
        attempts = json.loads(resolver["exchange_attempts_json"] or "[]")
        for att in attempts:
            venue = str(att.get("venue") or "").lower()
            found = bool(att.get("found"))
            if venue == "binance":
                venue_found["Binance"] = found
            elif venue == "bybit":
                venue_found["Bybit"] = found

    snap = conn.execute(
        "SELECT 1 FROM market_event_snapshots WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    if snap and not any(venue_found.values()):
        venue_found["Binance"] = True
        venue_found["Bybit"] = True

    if not snap and not any(venue_found.values()):
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_SNAPSHOT,
            status=STATUS_FAIL,
            reason="symbol not found on exchange",
        )
        return

    for label, ok in venue_found.items():
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_SNAPSHOT,
            status=STATUS_PASS if ok else STATUS_FAIL,
            reason=label,
        )


def record_f5_delivery_trace(
    conn: Any,
    *,
    event_id: int,
    message_id: int | None,
    dynamic_confidence: float,
    telegram_eligible: bool,
    telegram_skip_reason: str | None,
    telegram_sent: bool,
) -> None:
    record_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        stage=STAGE_CONFIDENCE,
        status=STATUS_PASS,
        reason=f"{dynamic_confidence:.1f}",
    )

    if telegram_eligible:
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_TELEGRAM_FILTER,
            status=STATUS_PASS,
            reason="eligible",
        )
    else:
        reason = telegram_skip_reason or "filtered"
        if reason == "low_confidence":
            from bot.research.market_events.signal_intelligence.config import F5_MIN_TELEGRAM_CONFIDENCE
            reason = f"confidence below threshold ({F5_MIN_TELEGRAM_CONFIDENCE:.1f})"
        record_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            stage=STAGE_TELEGRAM_FILTER,
            status=STATUS_SKIP,
            reason=reason,
        )

    record_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        stage=STAGE_TELEGRAM,
        status=STATUS_PASS if telegram_sent else STATUS_NOT_SENT,
        reason=None if telegram_sent else None,
    )
    record_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        stage=STAGE_DASHBOARD,
        status=STATUS_STORED,
    )


def fetch_trace_rows(conn: Any, *, event_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT event_id, message_id, stage, status, reason, latency_ms, created_at
        FROM {_TRACE_TABLE}
        WHERE event_id = ?
        ORDER BY id ASC
        """,
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_traced_event_ids(conn: Any, *, limit: int = 20) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT event_id, MAX(created_at) AS last_ts
        FROM {_TRACE_TABLE}
        GROUP BY event_id
        ORDER BY last_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [int(r["event_id"]) for r in rows]


def _status_glyph(status: str) -> str:
    if status == STATUS_PASS:
        return "✔"
    if status in (STATUS_NOT_SENT, STATUS_STORED):
        return ""
    return status


def _format_stage_block(stage: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [stage]
    if stage == STAGE_CONFIDENCE and rows:
        lines.append(rows[0].get("reason") or "")
        return lines

    for row in rows:
        status = row["status"]
        reason = row.get("reason") or ""

        if stage == STAGE_TELEGRAM and status == STATUS_NOT_SENT:
            lines.append("not sent")
            continue
        if stage == STAGE_DASHBOARD and status == STATUS_STORED:
            lines.append("stored")
            continue

        glyph = _status_glyph(status)
        if status == STATUS_FAIL:
            lines.append("FAILED")
            if reason:
                lines.append("")
                lines.append("reason:")
                lines.append(reason)
            continue
        if status == STATUS_SKIP:
            lines.append("SKIPPED")
            if reason:
                lines.append("reason:")
                lines.append(reason)
            continue

        if stage == STAGE_PARSER and reason and "\n" in reason:
            for part in reason.split("\n"):
                lines.append(f"✔ {part}")
            continue

        if glyph and reason:
            lines.append(f"{glyph} {reason}")
        elif glyph:
            lines.append(glyph)
        elif reason:
            lines.append(reason)

    return lines


def format_signal_trace(conn: Any, *, event_id: int) -> str:
    rows = fetch_trace_rows(conn, event_id=event_id)
    if not rows:
        return f"Message {event_id}\n\n(no trace recorded)"

    message_id = next((r["message_id"] for r in rows if r.get("message_id")), None)
    header = f"Message {message_id or event_id}"

    grouped: dict[str, list[dict[str, Any]]] = {}
    stage_order: list[str] = []
    for row in rows:
        stage = row["stage"]
        if stage not in grouped:
            grouped[stage] = []
            stage_order.append(stage)
        grouped[stage].append(row)

    lines = [header, ""]
    if STAGE_RAW in grouped:
        lines.extend(_format_stage_block(STAGE_RAW, grouped[STAGE_RAW]))
        lines.append("")

    for stage in (STAGE_PARSER, STAGE_VALIDATION, STAGE_SNAPSHOT):
        if stage in grouped:
            if stage == STAGE_SNAPSHOT and any(
                r["status"] == STATUS_FAIL for r in grouped[stage]
            ) and len(grouped[stage]) == 1:
                lines.append(stage)
                lines.append("")
                lines.append("FAILED")
                lines.append("")
                lines.append("reason:")
                lines.append(grouped[stage][0].get("reason") or "")
            else:
                lines.extend(_format_stage_block(stage, grouped[stage]))
            lines.append("")

    intel_label = "Signal Intelligence"
    intel_rows = [s for s in INTELLIGENCE_STAGES if s in grouped]
    if intel_rows:
        lines.append(intel_label)
        for s in intel_rows:
            st = grouped[s][0]["status"]
            if st == STATUS_FAIL:
                lines.append(f"{s} FAILED")
                reason = grouped[s][0].get("reason")
                if reason:
                    lines.append(f"  reason: {reason}")
            elif st == STATUS_SKIP:
                lines.append(f"{s} SKIPPED")
            else:
                lines.append(f"✔ {s}")
        lines.append("")

    if STAGE_AI in grouped:
        ai = grouped[STAGE_AI][0]
        lines.append("AI")
        if ai["status"] == STATUS_FAIL:
            lines.append("")
            lines.append("FAILED")
            lines.append("")
            lines.append(ai.get("reason") or "")
        else:
            lines.append("✔")
        lines.append("")

    for stage in (STAGE_CONFIDENCE, STAGE_TELEGRAM_FILTER, STAGE_TELEGRAM, STAGE_DASHBOARD):
        if stage in grouped:
            lines.extend(_format_stage_block(stage, grouped[stage]))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def trace_for_timeline(conn: Any, *, event_id: int) -> list[dict[str, Any]]:
    """Structured trace steps for dashboard /timeline/{event_id}."""
    rows = fetch_trace_rows(conn, event_id=event_id)
    steps: list[dict[str, Any]] = []
    for row in rows:
        detail = row.get("reason") or row["status"]
        if row["status"] == STATUS_PASS and row.get("reason"):
            detail = row["reason"]
        steps.append({
            "stage": row["stage"],
            "ts": int(row["created_at"]),
            "status": row["status"],
            "detail": detail,
            "latency_ms": row.get("latency_ms"),
        })
    return steps
