"""Read-only audit of Telegram Bot API inbound data flow and research visibility."""

from __future__ import annotations

import json
from typing import Any

from bot.research.futures_agent.env_bootstrap import resolve_agent_db_config
from bot.research.futures_agent.research_taxonomy import classify_research_content
from bot.research.futures_agent.telegram_inbound_bridge import (
    INBOUND_CHANNEL_PREFIX,
    bridge_input_to_research,
)

# Bound as query param — never embed in SQL literals (psycopg2 treats % as placeholders).
TELEGRAM_SOURCE_LIKE = "telegram:%"

DATA_FLOW_DOC = """
DATA FLOW (telegram-poll → storage → reply)
  CLI: bot/research/futures_agent/__main__.py → run_poll_loop()
  Poll: telegram_inbound.run_poll_loop() → _fetch_updates() → _commit_update_batch()
  Handler: telegram_inbound.handle_update() → process_telegram_message()
  Auth: telegram_config.is_chat_allowed()
  Extract: telegram_inbound.extract_message_text(), extract_forward_origin()
  Ingest: ingestion.ingest_from_telegram() → futures_agent_inputs (NOT telegram_messages)
  Parse: pipeline.process_input() → futures_agent_signals + futures_agent_targets
  Snapshot: snapshot.snapshot_signal() → futures_agent_market_snapshots (+ btc_context, relative_strength)
  Reply: telegram_replies.format_*() → responses.send_telegram_reply()
  Bridge (E.3.2+): telegram_inbound_bridge.bridge_input_to_research() → futures_agent_trader_posts

DESTINATION TABLES (live inbound)
  futures_agent_inputs          — immutable raw text + telegram metadata
  futures_agent_signals         — parser_v2 taxonomy / gate
  futures_agent_targets         — parsed TP levels
  futures_agent_market_snapshots — Binance context (gated signals only)
  futures_agent_telegram_research_bridge — idempotent link input → trader_posts
  futures_agent_trader_posts    — Stage 3 research corpus (via bridge only for inbound)

NOT WRITTEN by telegram-poll
  trading_ai.telegram_messages  — historical signalyp corpus (external collector, read-only)
  futures_agent_trader_theses — requires manual thesis-extract on trader_posts
  market_event_context          — Phase E linker (reads theses/posts, not inputs directly)
"""


def _parse_status_detail(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _visible_to_trader_posts(conn: Any, input_id: int) -> tuple[bool, int | None]:
    row = conn.execute(
        """
        SELECT b.post_id FROM futures_agent_telegram_research_bridge b
        WHERE b.input_id = ?
        """,
        (input_id,),
    ).fetchone()
    if row:
        return True, int(row["post_id"])
    return False, None


def _visible_to_market_context(conn: Any, post_id: int | None, symbol: str | None) -> bool:
    if post_id is None:
        return False
    try:
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations as me_migrate

        with market_events_connection() as me:
            me_migrate(me)
            n = me.execute(
                """
                SELECT COUNT(*) AS n FROM market_event_context
                WHERE source_record_id = ? OR context_json LIKE ?
                """,
                (str(post_id), f'%"post_id": {post_id}%'),
            ).fetchone()
            return int(n["n"] if n else 0) > 0
    except Exception:
        return False


def _classify_gap(content_type: str) -> str:
    gaps = {
        "EXPLICIT_SIGNAL": "thesis-extract required for market_event_context via theses",
        "TRADER_THESIS": "thesis-extract required for market_event_context",
        "MARKET_COMMENTARY": "bridged post linkable by symbol+timestamp (no thesis required)",
        "NEWS_EVENT": "bridged post linkable by symbol+timestamp + keywords",
        "TRADE_UPDATE": "stored in inputs; bridge classifies as TRADE_UPDATE",
        "OTHER": "stored; limited context match without symbol",
    }
    return gaps.get(content_type, "stored in inputs; bridge to trader_posts when synced")


def run_telegram_inbound_audit(conn: Any, *, limit: int = 20) -> str:
    cfg = resolve_agent_db_config()
    total = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_inputs
        WHERE source LIKE ?
        """,
        (TELEGRAM_SOURCE_LIKE,),
    ).fetchone()
    inbound_total = int(total["n"] if total else 0)

    bridged = conn.execute(
        "SELECT COUNT(*) AS n FROM futures_agent_telegram_research_bridge",
    ).fetchone()
    bridged_n = int(bridged["n"] if bridged else 0)

    rows = conn.execute(
        """
        SELECT i.id, i.source, i.telegram_message_id, i.raw_text, i.received_at,
               i.input_type, i.processing_status, i.status_detail,
               s.symbol, s.direction, s.taxonomy, s.passes_gate, s.gate_reason
        FROM futures_agent_inputs i
        LEFT JOIN futures_agent_signals s ON s.input_id = i.id
        WHERE i.source LIKE ?
        ORDER BY i.received_at DESC
        LIMIT ?
        """,
        (TELEGRAM_SOURCE_LIKE, limit),
    ).fetchall()

    lines = [
        "TELEGRAM INBOUND AUDIT (read-only)",
        f"agent_db: {cfg.backend} ({cfg.config_source})",
        DATA_FLOW_DOC.strip(),
        "",
        f"inbound_messages_total: {inbound_total}",
        f"bridged_to_trader_posts: {bridged_n}",
        f"research_posts_channel_prefix: {INBOUND_CHANNEL_PREFIX}:<chat_id>",
        "",
        "GAP ANALYSIS",
        "  Live telegram-poll → futures_agent_inputs (isolated from trading_ai.telegram_messages)",
        "  Historical signalyp → ingest-research → futures_agent_trader_posts (separate path)",
        "  Bridge (additive): inputs → trader_posts via futures_agent_telegram_research_bridge",
        "  market_event_context: links trader_theses (after thesis-extract) and bridged posts",
        "  thesis-extract is NOT auto-run on inbound bridge (no duplicate thesis creation)",
        "",
        f"=== Last {limit} inbound messages ===",
    ]

    if not rows:
        lines.append("No telegram inbound messages in agent DB.")
        return "\n".join(lines)

    for r in rows:
        detail = _parse_status_detail(r["status_detail"])
        chat_id = detail.get("telegram_chat_id")
        forward = detail.get("forward_origin")
        classification = classify_research_content(r["raw_text"] or "")
        visible_post, post_id = _visible_to_trader_posts(conn, int(r["id"]))
        ctx_visible = _visible_to_market_context(conn, post_id, r["symbol"])
        text_preview = (r["raw_text"] or "")[:120].replace("\n", " ")
        lines.append(
            f"  id={r['id']} msg={r['telegram_message_id']} chat={chat_id or r['source']} "
            f"ts={r['received_at']} status={r['processing_status']}",
        )
        lines.append(
            f"    raw_text: {'yes' if r['raw_text'] else 'no'} ({len(r['raw_text'] or '')} chars) "
            f"preview={text_preview!r}",
        )
        lines.append(
            f"    forward_metadata: {'yes' if forward else 'no'} "
            f"parsed_symbol={r['symbol'] or '-'} direction={r['direction'] or '-'} "
            f"parser_taxonomy={r['taxonomy'] or '-'} gate={r['passes_gate']}",
        )
        lines.append(
            f"    research_class={classification.content_type} conf={classification.confidence:.2f}",
        )
        lines.append(
            f"    visible_trader_posts={visible_post} post_id={post_id or '-'} "
            f"visible_market_event_context={ctx_visible}",
        )
        lines.append(f"    pipeline_note: {_classify_gap(classification.content_type)}")
        lines.append("")

    return "\n".join(lines)


def sync_unbridged_inputs(conn: Any, *, limit: int | None = 500) -> dict[str, int]:
    """Backfill bridge for historical inbound messages (idempotent)."""
    q = """
        SELECT i.id FROM futures_agent_inputs i
        LEFT JOIN futures_agent_telegram_research_bridge b ON b.input_id = i.id
        WHERE i.source LIKE ? AND b.input_id IS NULL
        ORDER BY i.received_at ASC
    """
    params: list[Any] = [TELEGRAM_SOURCE_LIKE]
    if limit is not None:
        q += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(q, params).fetchall()
    stats = {"scanned": 0, "bridged": 0, "duplicate": 0, "skipped": 0}
    for r in rows:
        stats["scanned"] += 1
        result = bridge_input_to_research(conn, int(r["id"]))
        if result.bridged:
            stats["bridged"] += 1
        elif result.duplicate:
            stats["duplicate"] += 1
        else:
            stats["skipped"] += 1
    return stats
