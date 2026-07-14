"""Phase G.3.5.1 / G.3.6 — Telegram slash-command router (bypasses parser_v2 entirely)."""

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
    "/dataset",
    "/vision",
    "/analyze",
    "/watch",
    "/watchlist",
    "/why-not",
    "/top-blockers",
    "/pipeline",
    "/trend-status",
    "/history-backfill",
    "/experimental",
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
    cmd, _args = _parse_command_args(stripped)
    return cmd in SUPPORTED_COMMANDS or cmd.startswith("/")


def normalize_command(text: str) -> str:
    cmd, _ = _parse_command_args(text)
    return cmd


def _parse_command_args(text: str) -> tuple[str, list[str]]:
    from bot.research.market_events.signal_intelligence.telegram_commands_g36 import parse_command_args
    return parse_command_args(text)


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


def handle_market_events_command(
    text: str,
    *,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> CommandRouteResultG351:
    """Route slash command to market_events report builders — no parser/ingest."""
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.event_schema import apply_migrations

    t0 = time.perf_counter()
    cmd, args = _parse_command_args(text)

    with market_events_connection() as conn:
        apply_migrations(conn)
        _record_command_trace(conn, message_id=message_id, command=cmd, stage="COMMAND_RECEIVED", status="PASS")

        try:
            if cmd == "/help":
                reply = "\n".join([
                    "G3.6 Commands",
                    "",
                    "/status  /health",
                    "/market  /analyze BTC",
                    "/candidates  /top",
                    "/replay  /score",
                    "/vision  (send chart photo)",
                    "/watch SOL  /watchlist",
                    "/why-not BTC  /top-blockers  /pipeline",
                    "/trend-status BTC  /history-backfill",
                    "/experimental",
                    "/research  /research-debug  /dataset",
                    "/help",
                ])
            elif cmd == "/status":
                from bot.research.market_events.signal_intelligence.health_g3 import format_g3_health_report
                from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                    append_heartbeat_to_status,
                )
                reply = append_heartbeat_to_status(format_g3_health_report(conn))
            elif cmd == "/health":
                from bot.research.market_events.signal_intelligence.health_g36 import format_g36_health_report
                from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                    append_heartbeat_to_status,
                )
                reply = append_heartbeat_to_status(format_g36_health_report(conn))
            elif cmd == "/market":
                from bot.research.market_events.signal_intelligence.telegram_intelligence_g35 import (
                    build_hourly_market_brief_g35,
                )
                reply = build_hourly_market_brief_g35(conn)
            elif cmd == "/candidates":
                from bot.research.market_events.signal_intelligence.candidate_g31 import format_candidates_report
                reply = format_candidates_report(conn, limit=20)
            elif cmd == "/top":
                from bot.research.market_events.signal_intelligence.telegram_commands_g36 import format_top_g36
                reply = format_top_g36(conn)
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
            elif cmd == "/dataset":
                from bot.research.market_events.signal_intelligence.research_dataset_g51 import (
                    format_dataset_telegram_g51,
                )
                reply = format_dataset_telegram_g51(conn)
            elif cmd == "/vision":
                reply = "Send a chart screenshot (TradingView, Bybit, Binance, OKX, Hyperliquid, CoinGlass)."
            elif cmd == "/analyze":
                from bot.research.market_events.signal_intelligence.analyze_symbol_g36 import (
                    format_analyze_symbol_g36,
                )
                sym = args[0] if args else "BTC"
                reply = format_analyze_symbol_g36(conn, sym)
            elif cmd == "/watch":
                from bot.research.market_events.signal_intelligence.watchlist_g36 import (
                    add_watchlist_symbol_g36,
                    format_watchlist_g36,
                )
                if not args:
                    reply = "Usage: /watch SOL"
                else:
                    add_watchlist_symbol_g36(conn, symbol=args[0], chat_id=chat_id)
                    conn.commit()
                    reply = f"Added {args[0].upper()} to watchlist.\n\n" + format_watchlist_g36(conn)
            elif cmd == "/watchlist":
                from bot.research.market_events.signal_intelligence.watchlist_g36 import format_watchlist_g36
                reply = format_watchlist_g36(conn)
            elif cmd == "/why-not":
                from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
                    format_why_not_g37,
                )
                sym = args[0] if args else "BTC"
                reply = format_why_not_g37(conn, sym)
            elif cmd == "/top-blockers":
                from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
                    format_top_blockers_g37,
                )
                reply = format_top_blockers_g37(conn, hours=24)
            elif cmd == "/pipeline":
                from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
                    format_pipeline_funnel_g37,
                    format_recommendations_g37,
                    format_distribution_g37,
                    market_score_stuck_audit_g37,
                )
                audit = market_score_stuck_audit_g37(conn, hours=24)
                reply = "\n\n".join([
                    format_pipeline_funnel_g37(conn),
                    format_distribution_g37(conn, field="market_score"),
                    format_distribution_g37(conn, field="rr"),
                    format_recommendations_g37(conn),
                    f"Score audit: {audit['message']}",
                ])
            elif cmd == "/trend-status":
                from bot.research.market_events.signal_intelligence.trend_history_g38 import (
                    format_trend_status_g38,
                )
                sym = args[0] if args else None
                reply = format_trend_status_g38(conn, symbol=sym, hours=24)
            elif cmd == "/history-backfill":
                from bot.research.market_events.signal_intelligence.trend_history_g38 import (
                    format_history_backfill_summary_g38,
                    run_history_backfill_g38,
                )
                stats = run_history_backfill_g38(conn, hours=24, run_pipeline=True)
                conn.commit()
                reply = format_history_backfill_summary_g38(stats)
            elif cmd == "/experimental":
                from bot.research.market_events.signal_intelligence.experimental_g39 import (
                    format_experimental_today_g39,
                )
                reply = format_experimental_today_g39(conn)
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


def route_telegram_command(
    text: str,
    *,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> CommandRouteResultG351 | None:
    if not is_slash_command(text):
        return None
    cmd, _ = _parse_command_args(text)
    if cmd not in SUPPORTED_COMMANDS:
        return CommandRouteResultG351(
            command=cmd,
            reply_text="Unknown command. Use /help.",
            ok=False,
            latency_ms=0,
        )
    return handle_market_events_command(text, message_id=message_id, chat_id=chat_id)
