"""Phase 1 — audit existing futures/telegram source data."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.parser import parse_signal_text
from bot.research.futures.schema import SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_data import (
    SOURCE_TABLES,
    channel_stats,
    iter_telegram_messages,
    table_columns,
    table_exists,
    table_row_count,
    table_ts_range,
)


def audit_source_data(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_tables(conn)

    tables: dict[str, Any] = {}
    for name in SOURCE_TABLES:
        exists = table_exists(conn, name)
        info: dict[str, Any] = {
            "exists": exists,
            "row_count": table_row_count(conn, name) if exists else 0,
            "columns": table_columns(conn, name) if exists else [],
        }
        if exists:
            for ts_col in ("timestamp", "message_ts", "ts", "created_at", "recorded_at"):
                if ts_col in info["columns"]:
                    info["ts_range"] = table_ts_range(conn, name, ts_col)
                    break
        tables[name] = info

    messages = iter_telegram_messages(conn)
    parseable = 0
    long_count = 0
    short_count = 0
    symbols: set[str] = set()
    ts_issues = 0

    for msg in messages:
        parsed = parse_signal_text(msg.text)
        if parsed.side:
            parseable += 1
            if parsed.side == "LONG":
                long_count += 1
            elif parsed.side == "SHORT":
                short_count += 1
        if parsed.symbol:
            symbols.add(parsed.symbol)
        if msg.timestamp <= 0:
            ts_issues += 1

    first_ts = min((m.timestamp for m in messages), default=None)
    last_ts = max((m.timestamp for m in messages), default=None)

    parsed_in_db = 0
    if table_exists(conn, SIGNALS_TABLE):
        parsed_in_db = table_row_count(conn, SIGNALS_TABLE)

    return {
        "tables": tables,
        "messages": {
            "count": len(messages),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "duration_seconds": (last_ts - first_ts) if first_ts and last_ts else 0,
            "channels": channel_stats(conn),
            "parseable_long_short": parseable,
            "long_count": long_count,
            "short_count": short_count,
            "unique_symbols": sorted(symbols),
            "symbol_count": len(symbols),
            "timestamp_quality_issues": ts_issues,
        },
        "research_signals_parsed": parsed_in_db,
        "parser_version": PARSER_VERSION,
        "candle_availability": _candle_availability_note(first_ts, last_ts),
        "gaps": _detect_gaps(messages),
    }


def _candle_availability_note(first_ts: int | None, last_ts: int | None) -> dict[str, Any]:
    return {
        "source": "binance_spot_and_futures_api",
        "note": "Historical candles fetched on-demand; derivatives coverage reported per snapshot",
        "message_range": (first_ts, last_ts),
    }


def _detect_gaps(messages: list, *, gap_hours: int = 24) -> list[dict[str, Any]]:
    if len(messages) < 2:
        return []
    gap_sec = gap_hours * 3600
    gaps = []
    prev = messages[0]
    for msg in messages[1:]:
        delta = msg.timestamp - prev.timestamp
        if delta > gap_sec:
            gaps.append({
                "from_ts": prev.timestamp,
                "to_ts": msg.timestamp,
                "gap_hours": round(delta / 3600, 1),
            })
        prev = msg
    return gaps[:20]


def render_audit_report(audit: dict[str, Any]) -> str:
    lines = ["FUTURES SOURCE DATA AUDIT", "=" * 40, ""]
    lines.append("TABLES")
    for name, info in audit["tables"].items():
        status = "present" if info["exists"] else "missing"
        lines.append(f"  {name}: {status} ({info['row_count']} rows)")
        if info.get("columns"):
            lines.append(f"    columns: {', '.join(info['columns'][:12])}{'...' if len(info['columns']) > 12 else ''}")
        if info.get("ts_range"):
            lines.append(f"    ts range: {info['ts_range']}")

    m = audit["messages"]
    lines.extend([
        "",
        "MESSAGES",
        f"  count: {m['count']}",
        f"  date range: {m['first_timestamp']} .. {m['last_timestamp']}",
        f"  duration: {m['duration_seconds']}s",
        f"  parseable LONG/SHORT: {m['parseable_long_short']}",
        f"  LONG: {m['long_count']}  SHORT: {m['short_count']}",
        f"  symbols ({m['symbol_count']}): {', '.join(m['unique_symbols'][:20])}",
        f"  timestamp issues: {m['timestamp_quality_issues']}",
        "",
        "CHANNELS",
    ])
    for ch in m["channels"][:20]:
        lines.append(f"  {ch['source']}: {ch['count']} messages")

    if audit["gaps"]:
        lines.extend(["", "GAPS (>24h)",])
        for g in audit["gaps"][:10]:
            lines.append(f"  {g['from_ts']} -> {g['to_ts']} ({g['gap_hours']}h)")

    lines.extend([
        "",
        f"Research signals in DB: {audit['research_signals_parsed']}",
        f"Parser version: {audit['parser_version']}",
    ])
    return "\n".join(lines)
