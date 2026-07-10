"""Context linking readiness for bridged Telegram inbound messages (no LLM)."""

from __future__ import annotations

import json
import re
from typing import Any

from bot.research.futures_agent.research_utils import parse_symbols_json
from bot.research.futures_agent.telegram_inbound_bridge import INBOUND_CHANNEL_PREFIX

NEWS_KEYWORDS = re.compile(
    r"(?i)\b(breaking|news|sec|etf|fed|cpi|fomc|hack|exploit|listing|delist|"
    r"bankruptcy|lawsuit|approval|ban|regulation)\b",
)
EVENT_KEYWORDS = re.compile(
    r"(?i)\b(pump|dump|liquidat|squeeze|breakout|breakdown|flash|crash|rally)\b",
)


def _match_reasons(
    *,
    symbol: str,
    event_ts: int,
    message_ts: int,
    content_type: str,
    raw_text: str,
    symbols: list[str],
    direction: str | None,
    window_sec: int,
) -> list[str]:
    reasons: list[str] = []
    delta = abs(event_ts - message_ts)
    if delta <= window_sec:
        reasons.append(f"timestamp_window_{delta}s")
    if symbol in symbols or symbol.upper() in raw_text.upper():
        reasons.append("symbol_match")
    if direction:
        reasons.append("direction_available")
    if content_type in ("EXPLICIT_SIGNAL", "TRADER_THESIS"):
        reasons.append("signal_content_type")
    if content_type == "NEWS_EVENT" or NEWS_KEYWORDS.search(raw_text):
        reasons.append("news_keywords")
    if content_type == "MARKET_COMMENTARY" or EVENT_KEYWORDS.search(raw_text):
        reasons.append("event_keywords")
    return reasons


def telegram_context_readiness_report(
    conn: Any,
    *,
    symbol: str | None = None,
    days: int = 7,
    window_sec: int = 6 * 3600,
) -> str:
    """Report matchability of bridged inbound posts to market events."""
    since = int(__import__("time").time()) - days * 86400
    sym_clause = ""
    params: list[Any] = [f"{INBOUND_CHANNEL_PREFIX}:%", since]
    if symbol:
        sym_clause = " AND (p.symbols_json LIKE ? OR p.raw_text LIKE ?)"
        params.extend([f'%"{symbol.upper()}"%', f"%{symbol.upper()}%"])

    posts = conn.execute(
        f"""
        SELECT p.id, p.channel_name, p.message_ts, p.content_type, p.symbols_json,
               p.raw_text, b.input_id
        FROM futures_agent_trader_posts p
        JOIN futures_agent_telegram_research_bridge b ON b.post_id = p.id
        WHERE p.channel_name LIKE ? AND p.message_ts >= ?{sym_clause}
        ORDER BY p.message_ts DESC
        LIMIT 100
        """,
        params,
    ).fetchall()

    events: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations as me_migrate

        with market_events_connection() as me:
            me_migrate(me)
            ev_params: list[Any] = [since]
            ev_q = "SELECT id, symbol, event_ts, direction, classification FROM market_events WHERE event_ts >= ?"
            if symbol:
                ev_q += " AND symbol = ?"
                ev_params.append(symbol.upper())
            ev_q += " ORDER BY event_ts DESC LIMIT 50"
            events = [dict(r) for r in me.execute(ev_q, ev_params).fetchall()]
    except Exception:
        pass

    lines = [
        "TELEGRAM CONTEXT LINKING READINESS (deterministic — no LLM)",
        f"days: {days} match_window_sec: {window_sec}",
        f"bridged_inbound_posts_in_window: {len(posts)}",
        f"market_events_in_window: {len(events)}",
        "",
        "Match dimensions: symbol, timestamp window, content_type, direction, news/event keywords",
        "",
    ]

    if not posts:
        lines.append("No bridged inbound posts in window. Run telegram-bridge-sync if needed.")
        return "\n".join(lines)

    matchable = 0
    for p in posts:
        symbols = parse_symbols_json(p["symbols_json"])
        raw = p["raw_text"] or ""
        best: list[str] = []
        for ev in events:
            reasons = _match_reasons(
                symbol=ev["symbol"],
                event_ts=int(ev["event_ts"]),
                message_ts=int(p["message_ts"]),
                content_type=p["content_type"],
                raw_text=raw,
                symbols=symbols,
                direction=ev.get("direction"),
                window_sec=window_sec,
            )
            if len(reasons) >= 2:
                best = reasons
                break
        if best:
            matchable += 1
        preview = raw[:80].replace("\n", " ")
        lines.append(
            f"  post={p['id']} input={p['input_id']} ts={p['message_ts']} "
            f"type={p['content_type']} symbols={symbols}",
        )
        lines.append(f"    preview={preview!r}")
        lines.append(f"    matchable={'yes' if best else 'weak'} reasons={best or ['needs_symbol_or_time_overlap']}")
        lines.append("")

    lines.append(f"posts_with_strong_match_potential: {matchable}/{len(posts)}")
    lines.append(
        "Note: market_event_context links trader_theses after thesis-extract; "
        "bridged posts without theses use post-level linking when shock occurs.",
    )
    return "\n".join(lines)
