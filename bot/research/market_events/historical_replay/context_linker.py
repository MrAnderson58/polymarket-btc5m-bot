"""Task H — no-lookahead context linking for replay shocks."""

from __future__ import annotations

import json
import re
from typing import Any

from bot.research.market_events.historical_replay.constants import CONTEXT_WINDOWS_SEC

NEWS_KW = re.compile(
    r"(?i)\b(breaking|news|sec|etf|fed|cpi|hack|listing|approval)\b",
)


def _insert_link(
    conn: Any,
    *,
    shock_id: int,
    source: str,
    record_id: str,
    context_ts: int,
    event_ts: int,
    window_sec: int,
    symbol: str,
    symbol_match: bool,
    direction: str | None,
    post_direction: str | None,
    raw: dict,
) -> None:
    if context_ts >= event_ts:
        return
    age = event_ts - context_ts
    if age > window_sec:
        return
    agree = "neutral"
    if direction and post_direction:
        agree = "agree" if direction == post_direction else "conflict"
    conn.execute(
        """
        INSERT OR IGNORE INTO market_events_replay_context_links (
          shock_id, context_source, context_record_id, context_ts,
          age_at_event_sec, window_sec, symbol_match, direction_agreement,
          catalyst_category, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shock_id, source, record_id, context_ts, age, window_sec,
            1 if symbol_match else 0, agree,
            "NEWS" if NEWS_KW.search(raw.get("preview", "")) else "COMMENTARY",
            json.dumps(raw),
        ),
    )


def link_replay_context(conn: Any, *, run_tag: str) -> int:
    """Link bridged telegram + signalyp posts with strict no-lookahead."""
    run = conn.execute(
        "SELECT id FROM market_events_replay_runs WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if not run:
        return 0
    shocks = conn.execute(
        "SELECT id, symbol, event_ts, direction FROM market_events_replay_shocks WHERE run_id = ?",
        (int(run["id"]),),
    ).fetchall()
    linked = 0
    try:
        from bot.research.futures_agent.db import agent_connection
        from bot.research.futures_agent.research_utils import parse_symbols_json
        from bot.research.futures_agent.telegram_inbound_bridge import INBOUND_CHANNEL_PREFIX

        with agent_connection() as agent:
            for sh in shocks:
                sym = sh["symbol"]
                event_ts = int(sh["event_ts"])
                for window in CONTEXT_WINDOWS_SEC:
                    since = event_ts - window
                    posts = agent.execute(
                        """
                        SELECT p.id, p.message_ts, p.raw_text, p.symbols_json, p.content_type,
                               p.channel_name, 'trader_post' AS src
                        FROM futures_agent_trader_posts p
                        WHERE p.message_ts >= ? AND p.message_ts < ?
                          AND (p.symbols_json LIKE ? OR p.raw_text LIKE ?)
                        ORDER BY p.message_ts DESC LIMIT 20
                        """,
                        (since, event_ts, f'%"{sym}"%', f"%{sym}%"),
                    ).fetchall()
                    for p in posts:
                        symbols = parse_symbols_json(p["symbols_json"])
                        preview = (p["raw_text"] or "")[:160]
                        src = "signalyp" if INBOUND_CHANNEL_PREFIX not in (p["channel_name"] or "") else "telegram_inbound"
                        _insert_link(
                            conn,
                            shock_id=int(sh["id"]),
                            source=src,
                            record_id=str(p["id"]),
                            context_ts=int(p["message_ts"]),
                            event_ts=event_ts,
                            window_sec=window,
                            symbol=sym,
                            symbol_match=sym in symbols or sym.upper() in preview.upper(),
                            direction=sh["direction"],
                            post_direction=None,
                            raw={"preview": preview, "content_type": p["content_type"]},
                        )
                        linked += 1
    except Exception:
        pass
    return linked
