"""Phase G.3.5.1 — Telegram slash-command router (bypasses parser_v2 entirely)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_COMMANDS = frozenset({
    "/status",
    "/market",
    "/candidates",
    "/top",
    "/replay",
    "/score",
    "/health",
    "/help",
    "/research",
    "/research-debug",
})


@dataclass(frozen=True)
class CommandRouteResultG351:
    command: str
    reply_text: str
    ok: bool
    latency_ms: int


def is_slash_command(text: str | None) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False
    cmd = normalize_command(stripped)
    return cmd in SUPPORTED_COMMANDS or cmd.startswith("/")


def normalize_command(text: str) -> str:
    """Extract base command, e.g. '/status@MyBot' → '/status'."""
    token = text.strip().split()[0].lower()
    if "@" in token:
        token = token.split("@", 1)[0]
    return token


def _record_command_trace(
    conn: Any,
    *,
    message_id: int | None,
    command: str,
    stage: str,
    status: str,
    reason: str | None = None,
    latency_ms: int | None = None,
) -> None:
    try:
        from bot.research.market_events.db import insert_returning_id
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
    except Exception as exc:
        logger.debug("command trace skipped: %s", exc)


def handle_market_events_command(text: str, *, message_id: int | None = None) -> CommandRouteResultG351:
    """Route slash command to market_events report builders — no parser/ingest."""
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations

    t0 = time.perf_counter()
    cmd = normalize_command(text)

    with market_events_connection() as conn:
        apply_migrations(conn)
        _record_command_trace(conn, message_id=message_id, command=cmd, stage="COMMAND_RECEIVED", status="PASS")

        try:
            if cmd == "/help":
                reply = "\n".join([
                    "G3.5 Commands",
                    "",
                    "/status  /health",
                    "/market",
                    "/candidates",
                    "/top",
                    "/replay",
                    "/score",
                    "/help",
                    "/research",
                    "/research-debug",
                ])
            elif cmd in ("/status", "/health"):
                from bot.research.market_events.signal_intelligence.health_g3 import format_g3_health_report
                from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                    append_heartbeat_to_status,
                )
                reply = append_heartbeat_to_status(format_g3_health_report(conn))
            elif cmd == "/market":
                from bot.research.market_events.signal_intelligence.telegram_intelligence_g35 import (
                    build_hourly_market_brief_g35,
                )
                reply = build_hourly_market_brief_g35(conn)
            elif cmd == "/candidates":
                from bot.research.market_events.signal_intelligence.candidate_g31 import format_candidates_report
                reply = format_candidates_report(conn, limit=20)
            elif cmd == "/top":
                from bot.research.market_events.signal_intelligence.candidate_g31 import format_candidates_report
                reply = format_candidates_report(conn, limit=1)
            elif cmd == "/replay":
                from bot.research.market_events.signal_intelligence.replay_g32 import format_candidate_replay_report
                reply = format_candidate_replay_report(conn, limit=10)
            elif cmd == "/score":
                from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
                    format_score_breakdown_report,
                )
                reply = format_score_breakdown_report(conn, symbol=None)
            elif cmd == "/research":
                from bot.research.market_events.signal_intelligence.quant_research_g50 import (
                    format_quant_telegram_g50,
                )
                reply = format_quant_telegram_g50(conn)
            elif cmd == "/research-debug":
                from bot.research.market_events.signal_intelligence.quant_research_g50 import (
                    format_quant_debug_telegram_g50,
                )
                reply = format_quant_debug_telegram_g50(conn)
            else:
                reply = "Unknown command. Use /help."

            latency = int((time.perf_counter() - t0) * 1000)
            _record_command_trace(
                conn,
                message_id=message_id,
                command=cmd,
                stage="COMMAND_EXECUTED",
                status="PASS",
                latency_ms=latency,
            )
            conn.commit()
            return CommandRouteResultG351(command=cmd, reply_text=reply, ok=True, latency_ms=latency)
        except Exception as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            _record_command_trace(
                conn,
                message_id=message_id,
                command=cmd,
                stage="COMMAND_FAILED",
                status="FAILED",
                reason=str(exc),
                latency_ms=latency,
            )
            conn.commit()
            logger.warning("command %s failed: %s", cmd, exc)
            return CommandRouteResultG351(
                command=cmd,
                reply_text=f"Command failed: {exc}",
                ok=False,
                latency_ms=latency,
            )


def route_telegram_command(text: str, *, message_id: int | None = None) -> CommandRouteResultG351 | None:
    if not is_slash_command(text):
        return None
    cmd = normalize_command(text)
    if cmd not in SUPPORTED_COMMANDS:
        return CommandRouteResultG351(
            command=cmd,
            reply_text="Unknown command. Use /help.",
            ok=False,
            latency_ms=0,
        )
    return handle_market_events_command(text, message_id=message_id)
