"""Link shock events to existing research records (read-only references)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from bot.research.market_events.config import CONTEXT_WINDOWS_SEC
from bot.research.market_events.db import insert_returning_id


def _agent_conn():
    try:
        from bot.research.futures_agent.db import agent_connection
        return agent_connection()
    except Exception:
        return None


def _trades_conn():
    db_path = Path(__file__).resolve().parent.parent.parent.parent / "data" / "trades.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def link_event_context(conn: Any, *, event_id: int, event_ts: int, symbol: str) -> int:
    linked = 0
    linked += _link_agent_theses(conn, event_id, event_ts, symbol)
    linked += _link_bridged_inbound_posts(conn, event_id, event_ts, symbol)
    linked += _link_polymarket_state(conn, event_id, event_ts)
    return linked


def _insert_context(
    conn: Any,
    *,
    event_id: int,
    context_type: str,
    source: str,
    source_record_id: str,
    context_ts: int,
    event_ts: int,
    relevance_score: float,
    context_json: dict[str, Any],
) -> bool:
    try:
        insert_returning_id(
            conn,
            """
            INSERT OR IGNORE INTO market_event_context (
              event_id, context_type, source, source_record_id,
              context_ts, time_delta_seconds, relevance_score, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, context_type, source, source_record_id,
                context_ts, event_ts - context_ts, relevance_score,
                json.dumps(context_json), int(__import__("time").time()),
            ),
        )
        return True
    except Exception:
        return False


def _link_agent_theses(conn: Any, event_id: int, event_ts: int, symbol: str) -> int:
    n = 0
    ctx = _agent_conn()
    if ctx is None:
        return 0
    try:
        with ctx as agent:
            window = CONTEXT_WINDOWS_SEC["TRADER_THESIS"]
            rows = agent.execute(
                """
                SELECT t.id AS thesis_id, t.direction, t.symbol, p.message_ts, p.content_type,
                       p.channel_name, p.id AS post_id
                FROM futures_agent_trader_theses t
                JOIN futures_agent_trader_posts p ON p.id = t.post_id
                WHERE t.symbol = ? AND p.message_ts BETWEEN ? AND ?
                ORDER BY p.message_ts DESC
                LIMIT 50
                """,
                (symbol, event_ts - window, event_ts),
            ).fetchall()
            for row in rows:
                ctype = "TELEGRAM_SIGNAL" if row["content_type"] == "EXPLICIT_SIGNAL" else "TRADER_THESIS"
                if row["content_type"] == "NEWS_EVENT":
                    ctype = "NEWS"
                elif row["content_type"] not in ("EXPLICIT_SIGNAL", "TRADER_THESIS", "NEWS_EVENT"):
                    ctype = "MARKET_COMMENTARY"
                if _insert_context(
                    conn,
                    event_id=event_id,
                    context_type=ctype,
                    source=row["channel_name"] or "agent",
                    source_record_id=str(row["thesis_id"]),
                    context_ts=int(row["message_ts"]),
                    event_ts=event_ts,
                    relevance_score=0.7,
                    context_json={
                        "post_id": row["post_id"],
                        "direction": row["direction"],
                        "content_type": row["content_type"],
                    },
                ):
                    n += 1
    except Exception:
        pass
    return n


def _link_bridged_inbound_posts(conn: Any, event_id: int, event_ts: int, symbol: str) -> int:
    """Link bridged telegram_inbound posts without requiring thesis extraction."""
    n = 0
    ctx = _agent_conn()
    if ctx is None:
        return 0
    try:
        from bot.research.futures_agent.research_utils import parse_symbols_json
        from bot.research.futures_agent.telegram_inbound_bridge import INBOUND_CHANNEL_PREFIX

        with ctx as agent:
            window = CONTEXT_WINDOWS_SEC["TELEGRAM_SIGNAL"]
            rows = agent.execute(
                """
                SELECT p.id AS post_id, p.channel_name, p.message_ts, p.content_type,
                       p.symbols_json, p.raw_text
                FROM futures_agent_trader_posts p
                JOIN futures_agent_telegram_research_bridge b ON b.post_id = p.id
                WHERE p.channel_name LIKE ? AND p.message_ts BETWEEN ? AND ?
                ORDER BY p.message_ts DESC
                LIMIT 50
                """,
                (f"{INBOUND_CHANNEL_PREFIX}:%", event_ts - window, event_ts),
            ).fetchall()
            for row in rows:
                symbols = parse_symbols_json(row["symbols_json"])
                raw = row["raw_text"] or ""
                if symbol not in symbols and symbol.upper() not in raw.upper():
                    continue
                ctype = "TELEGRAM_SIGNAL"
                if row["content_type"] == "NEWS_EVENT":
                    ctype = "NEWS"
                elif row["content_type"] in ("MARKET_COMMENTARY", "TECHNICAL_LEVELS"):
                    ctype = "MARKET_COMMENTARY"
                elif row["content_type"] == "TRADE_UPDATE":
                    ctype = "MARKET_COMMENTARY"
                if _insert_context(
                    conn,
                    event_id=event_id,
                    context_type=ctype,
                    source=row["channel_name"] or "telegram_inbound",
                    source_record_id=str(row["post_id"]),
                    context_ts=int(row["message_ts"]),
                    event_ts=event_ts,
                    relevance_score=0.6,
                    context_json={
                        "post_id": row["post_id"],
                        "content_type": row["content_type"],
                        "bridge": "telegram_inbound",
                        "raw_text": (raw[:2000] if raw else None),
                        "has_photo": row["content_type"] == "TECHNICAL_LEVELS",
                    },
                ):
                    n += 1
    except Exception:
        pass
    return n


def _link_polymarket_state(conn: Any, event_id: int, event_ts: int) -> int:
    n = 0
    trades = _trades_conn()
    if trades is None:
        return 0
    try:
        window = CONTEXT_WINDOWS_SEC["POLYMARKET_STATE"]
        rows = trades.execute(
            """
            SELECT id, market_slug, window_start_ts, strike_price, btc_price,
                   yes_bid, yes_ask, seconds_remaining
            FROM market_checks
            WHERE window_start_ts BETWEEN ? AND ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (event_ts - window, event_ts + 60),
        ).fetchall()
        for row in rows:
            if _insert_context(
                conn,
                event_id=event_id,
                context_type="POLYMARKET_STATE",
                source="trades.db",
                source_record_id=str(row["id"]),
                context_ts=int(row["window_start_ts"]),
                event_ts=event_ts,
                relevance_score=0.5,
                context_json={
                    "market_slug": row["market_slug"],
                    "strike_price": row["strike_price"],
                    "btc_price": row["btc_price"],
                    "yes_bid": row["yes_bid"],
                    "yes_ask": row["yes_ask"],
                },
            ):
                n += 1
    finally:
        trades.close()
    return n
