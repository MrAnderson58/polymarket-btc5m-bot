"""Phase G.0.4 — Telegram inbound tracing, diagnose, simulate, self-test."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

STAGE_ORDER = (
    "Telegram Update",
    "Router",
    "Command",
    "Parser",
    "Taxonomy",
    "Signal",
    "Reply",
)


@dataclass
class InboundTraceG04:
    message_id: int | None = None
    chat_id: int | None = None
    preview: str = ""
    received: str = "—"
    router: str = "—"
    command: str = "—"
    parser: str = "—"
    taxonomy: str = "—"
    signal_label: str = "—"
    reply: str = "—"
    error: str | None = None
    stages: dict[str, str] = field(default_factory=dict)

    def set_stage(self, name: str, value: str) -> None:
        self.stages[name] = value
        logger.info("inbound[%s] %s → %s", self.message_id or "-", name, value)


@dataclass
class SignalDiagnoseG04:
    label: str
    taxonomy: str
    passes_gate: bool
    reasons: list[str]
    missing: list[str]
    symbol: str | None
    side: str | None
    entry: float | None
    stop_loss: float | None
    take_profits: list[float]


def normalize_inbound_text_g04(text: str) -> str:
    """Normalize common Telegram quirks before parse (NBSP, lone $ line)."""
    if not text:
        return ""
    t = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = t.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "$" and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and not nxt.startswith("$"):
                out.append(f"${nxt}")
                i += 2
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def diagnose_signal_g04(text: str) -> SignalDiagnoseG04:
    """Classify / diagnose inbound text; expose human Missing-* reasons."""
    from bot.research.futures.parser_v2 import parse_signal_v2
    from bot.research.futures.taxonomy import MessageType

    normalized = normalize_inbound_text_g04(text)
    result = parse_signal_v2(normalized)
    parsed = result.parsed
    missing: list[str] = []
    if parsed.symbol is None:
        missing.append("Unknown Symbol")
    if parsed.side is None:
        missing.append("No Direction")
    if parsed.entry_min is None:
        missing.append("Missing Entry")
    if not parsed.take_profits:
        missing.append("Missing TP")
    if parsed.stop_loss is None:
        missing.append("Missing SL")

    if result.passes_gate and result.message_type == MessageType.EXPLICIT_SIGNAL:
        label = "SIGNAL"
    elif parsed.symbol and parsed.side and (
        parsed.entry_min is not None or parsed.stop_loss is not None or parsed.take_profits
    ):
        # Structured enough — treat as SIGNAL even if taxonomy lagging
        label = "SIGNAL"
    elif parsed.symbol or parsed.side or parsed.entry_min is not None:
        label = "PARTIAL"
    else:
        label = "OTHER"

    reasons = list(result.taxonomy_reasons)
    if not result.passes_gate:
        reasons.append(result.gate_reason or "gate_rejected")
        reasons.extend(missing)

    return SignalDiagnoseG04(
        label=label,
        taxonomy=result.message_type.value,
        passes_gate=result.passes_gate
        or (
            label == "SIGNAL"
            and parsed.symbol is not None
            and parsed.side is not None
            and (
                parsed.entry_min is not None
                or parsed.stop_loss is not None
                or bool(parsed.take_profits)
            )
        ),
        reasons=reasons,
        missing=missing,
        symbol=parsed.symbol,
        side=parsed.side,
        entry=parsed.entry_min,
        stop_loss=parsed.stop_loss,
        take_profits=list(parsed.take_profits),
    )


def format_parser_reject_reasons_g04(text: str) -> str:
    diag = diagnose_signal_g04(text)
    if diag.label == "SIGNAL" and diag.passes_gate:
        return "OK"
    if diag.missing:
        return ", ".join(diag.missing)
    return diag.reasons[-1] if diag.reasons else "did not pass gate"


def persist_inbound_trace_g04(trace: InboundTraceG04) -> None:
    """Short-lived write connection — never share with report builders."""
    try:
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations

        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events_inbound_trace_g04 (
                  message_id, chat_id, preview, received, router, command,
                  parser, taxonomy, signal_label, reply, error, stages_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.message_id,
                    trace.chat_id,
                    (trace.preview or "")[:240],
                    trace.received,
                    trace.router,
                    trace.command,
                    trace.parser,
                    trace.taxonomy,
                    trace.signal_label,
                    (trace.reply or "")[:500],
                    (trace.error or "")[:500] if trace.error else None,
                    json.dumps(trace.stages, ensure_ascii=False),
                    int(time.time()),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("inbound trace persist failed: %s", exc)


def record_command_trace_write_g04(
    *,
    message_id: int | None,
    command: str,
    stage: str,
    status: str,
    reason: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Write command stage on a short-lived writer (outside RO report conn)."""
    try:
        from bot.research.market_events.db import insert_returning_id, market_events_connection
        from bot.research.market_events.event_schema import apply_migrations

        with market_events_connection() as conn:
            apply_migrations(conn)
            insert_returning_id(
                conn,
                """
                INSERT INTO market_events_command_trace_g351 (
                  message_id, command, stage, status, reason, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    command,
                    stage,
                    status,
                    reason,
                    latency_ms,
                    int(time.time()),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("command write-trace skipped: %s", exc)


def format_inbound_debug_g04(conn: Any, *, limit: int = 20) -> str:
    rows = conn.execute(
        """
        SELECT message_id, preview, received, router, command, parser,
               taxonomy, signal_label, reply, error, created_at
        FROM market_events_inbound_trace_g04
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return "Telegram Inbound Debug\n\n(no traces yet)"

    lines = ["Telegram Inbound Debug", f"Last {len(rows)} messages", ""]
    for i, r in enumerate(reversed(list(rows)), 1):
        lines.extend([
            f"#{i} message_id={r['message_id']}",
            "",
            "received",
            r["received"] or "—",
            "",
            "router",
            r["router"] or "—",
            "",
            "command",
            r["command"] or "—",
            "",
            "parser",
            r["parser"] or "—",
            "",
            "taxonomy",
            r["taxonomy"] or "—",
            "",
            "signal",
            r["signal_label"] or "—",
            "",
            "reply",
            (r["reply"] or "—")[:200],
            "",
            "error",
            r["error"] or "none",
            "",
            "----------",
            "",
        ])
    return "\n".join(lines).rstrip()


def simulate_telegram_message_g04(
    text: str,
    *,
    chat_id: int = 0,
    message_id: int | None = None,
    send_reply: bool = False,
) -> str:
    """Run full inbound pipeline without Bot API I/O (except optional send)."""
    from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
        is_slash_command,
        route_telegram_command,
    )

    msg_id = message_id if message_id is not None else int(time.time())
    preview = text.strip().replace("\n", " ")[:80]
    trace = InboundTraceG04(
        message_id=msg_id,
        chat_id=chat_id,
        preview=preview,
        received="OK",
    )
    trace.set_stage("Telegram Update", "OK (simulated)")

    lines = [
        "Simulate Telegram Message",
        "",
        "Input",
        text.strip(),
        "",
    ]

    try:
        if is_slash_command(text):
            trace.set_stage("Router", "COMMAND")
            trace.router = "COMMAND"
            route = route_telegram_command(text.strip(), message_id=msg_id, chat_id=chat_id)
            cmd = route.command if route else "—"
            reply = route.reply_text if route else "Unknown command"
            trace.command = cmd
            trace.parser = "SKIPPED (command)"
            trace.taxonomy = "N/A"
            trace.signal_label = "N/A"
            trace.reply = "OK" if route and route.ok else "FAIL"
            trace.set_stage("Command", cmd)
            trace.set_stage("Parser", "SKIPPED")
            trace.set_stage("Taxonomy", "N/A")
            trace.set_stage("Signal", "N/A")
            trace.set_stage("Reply", "OK" if route and route.ok else "FAIL")
            lines.extend([
                "Router",
                "COMMAND",
                "",
                "Command",
                cmd,
                "",
                "Reply",
                reply,
                "",
            ])
        else:
            trace.set_stage("Router", "SIGNAL")
            trace.router = "SIGNAL"
            diag = diagnose_signal_g04(text)
            reject = format_parser_reject_reasons_g04(text)
            trace.command = "—"
            trace.parser = "OK" if diag.symbol or diag.side else "FAIL"
            trace.taxonomy = diag.taxonomy
            trace.signal_label = diag.label
            if diag.label == "SIGNAL" and (
                diag.passes_gate
                or (
                    diag.symbol
                    and diag.side
                    and diag.entry is not None
                    and diag.take_profits
                    and diag.stop_loss is not None
                )
            ):
                reply = (
                    f"SIGNAL\n"
                    f"{diag.symbol} {diag.side}\n"
                    f"Entry: {diag.entry}\n"
                    f"TP: {', '.join(str(x) for x in diag.take_profits) or '—'}\n"
                    f"SL: {diag.stop_loss}\n"
                    f"Taxonomy: {diag.taxonomy}\n"
                    f"Gate: {'PASS' if diag.passes_gate else 'STRUCTURAL'}"
                )
                trace.reply = "OK"
                trace.set_stage("Reply", "OK")
            else:
                reply = (
                    f"NOT SIGNAL ({diag.label})\n"
                    f"Taxonomy: {diag.taxonomy}\n"
                    f"Reason: {reject}"
                )
                trace.reply = "REJECT"
                trace.set_stage("Reply", "REJECT")
                if diag.missing:
                    trace.error = ", ".join(diag.missing)

            trace.set_stage("Command", "—")
            trace.set_stage("Parser", trace.parser)
            trace.set_stage("Taxonomy", diag.taxonomy)
            trace.set_stage("Signal", diag.label)

            lines.extend([
                "Router",
                "SIGNAL",
                "",
                "Parser",
                f"symbol={diag.symbol} side={diag.side} "
                f"entry={diag.entry} tp={diag.take_profits} sl={diag.stop_loss}",
                "",
                "Taxonomy",
                diag.taxonomy,
                "",
                "Signal",
                diag.label,
                "",
                "Missing",
                ", ".join(diag.missing) if diag.missing else "none",
                "",
                "Reply",
                reply,
                "",
            ])

        persist_inbound_trace_g04(trace)
        return "\n".join(lines).rstrip()
    except Exception as exc:
        trace.error = str(exc)
        trace.set_stage("Reply", f"ERROR: {exc}")
        persist_inbound_trace_g04(trace)
        raise


def format_telegram_self_test_g04() -> str:
    """Self-test receive → router → parser → reply → database (no Telegram API)."""
    from bot.research.market_events.db import (
        market_events_connection,
        market_events_readonly_connection,
    )
    from bot.research.market_events.event_schema import apply_migrations
    from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
        handle_market_events_command,
        is_slash_command,
    )

    sample = "$SXT шорт\nEntry 0.00907\nTP 0.00896\nSL 0.00967"
    checks: list[tuple[str, bool, str]] = []

    # receive
    checks.append(("receive", bool(sample.strip()), "text non-empty"))

    # router
    cmd_ok = is_slash_command("/status") and not is_slash_command(sample)
    checks.append(("router", cmd_ok, "slash→command, free-text→signal"))

    # parser / SIGNAL
    diag = diagnose_signal_g04(sample)
    signal_ok = diag.label == "SIGNAL" and diag.symbol == "SXT" and diag.side == "SHORT"
    checks.append((
        "parser",
        signal_ok,
        f"label={diag.label} taxonomy={diag.taxonomy} symbol={diag.symbol} side={diag.side}",
    ))

    # database write + readonly
    db_ok = False
    db_detail = ""
    try:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute("SELECT 1")
            conn.commit()
        with market_events_readonly_connection() as ro:
            row = ro.execute("SELECT 1 AS ok").fetchone()
            db_ok = bool(row)
            db_detail = "write+readonly OK"
    except Exception as exc:
        db_detail = str(exc)
    checks.append(("database", db_ok, db_detail))

    # reply — command path + signal simulate
    reply_ok = False
    reply_detail = ""
    try:
        status = handle_market_events_command("/status", message_id=None)
        sim = simulate_telegram_message_g04(sample, message_id=int(time.time()))
        reply_ok = status.ok and "SIGNAL" in sim and "SXT" in sim
        reply_detail = f"/status ok={status.ok}; simulate SIGNAL={'SIGNAL' in sim}"
    except Exception as exc:
        reply_detail = str(exc)
    checks.append(("reply", reply_ok, reply_detail))

    lines = ["Telegram Self-Test", ""]
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        lines.extend([name, "PASS" if ok else "FAIL", detail, ""])
    lines.extend(["Overall", "PASS" if all_ok else "FAIL"])
    return "\n".join(lines)
