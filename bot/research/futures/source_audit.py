"""Phase 1 — audit existing futures/telegram source data."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.db_config import get_futures_research_database_path
from bot.research.futures.parser import parse_signal_text
from bot.research.futures.schema import SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_data import SOURCE_TABLES
from bot.research.futures.source_reader import SourceReader, open_source_reader


def audit_source_data(
    research_conn: sqlite3.Connection,
    source: SourceReader | None = None,
) -> dict[str, Any]:
    ensure_tables(research_conn)
    owns_source = source is None
    if source is None:
        source = open_source_reader(sqlite_conn=research_conn)

    try:
        tables = source.list_source_tables()
        messages = list(source.iter_telegram_messages())
        channels = source.channel_stats()

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
        if research_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (SIGNALS_TABLE,),
        ).fetchone():
            parsed_in_db = research_conn.execute(
                f"SELECT COUNT(*) FROM {SIGNALS_TABLE}"
            ).fetchone()[0]

        return {
            "source": {
                "backend": source.info.backend,
                "status": source.info.status,
                "reason_code": source.info.reason_code,
                "dsn_host": source.info.dsn_host,
                "dsn_database": source.info.dsn_database,
                "read_only": source.info.read_only,
                "details": source.info.details,
            },
            "research_db": {
                "backend": "sqlite",
                "path": get_futures_research_database_path(),
            },
            "tables": tables,
            "messages": {
                "count": len(messages),
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "duration_seconds": (last_ts - first_ts) if first_ts and last_ts else 0,
                "channels": channels,
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
    finally:
        if owns_source:
            source.close()


def check_source_connection(*, sqlite_conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Lightweight connectivity probe — no full message scan."""
    from bot.research.futures.source_reader import SourceConfigError

    try:
        source = open_source_reader(sqlite_conn=sqlite_conn)
        tables = source.list_source_tables()
        msg_table = "telegram_messages" if tables.get("telegram_messages", {}).get("exists") else "telegram_signals"
        msg_count = tables.get(msg_table, {}).get("row_count", 0)
        result = {
            "ok": True,
            "source": {
                "backend": source.info.backend,
                "status": source.info.status,
                "dsn_host": source.info.dsn_host,
                "dsn_database": source.info.dsn_database,
            },
            "message_table": msg_table,
            "message_count": msg_count,
            "discovered_tables": [n for n in SOURCE_TABLES if tables.get(n, {}).get("exists")],
        }
        source.close()
        return result
    except SourceConfigError as exc:
        return {"ok": False, "reason_code": exc.reason_code, "error": str(exc)}


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
    src = audit.get("source", {})
    research = audit.get("research_db", {})
    lines = ["FUTURES SOURCE DATA AUDIT", "=" * 40, ""]
    lines.extend([
        "SOURCE DB",
        f"  backend: {src.get('backend', 'unknown')}",
        f"  status: {src.get('status', 'unknown')}",
        f"  host: {src.get('dsn_host') or 'n/a'}",
        f"  database: {src.get('dsn_database') or 'n/a'}",
        f"  read_only: {src.get('read_only', True)}",
    ])
    if src.get("reason_code"):
        lines.append(f"  reason_code: {src['reason_code']}")
    lines.extend([
        "",
        "RESEARCH DB",
        f"  backend: {research.get('backend', 'sqlite')}",
        f"  path: {research.get('path', 'n/a')}",
        "",
        "TABLES",
    ])
    for name, info in audit["tables"].items():
        status = "present" if info["exists"] else "missing"
        lines.append(f"  {name}: {status} ({info['row_count']} rows)")
        if info.get("columns"):
            cols = info["columns"]
            lines.append(f"    columns: {', '.join(cols[:12])}{'...' if len(cols) > 12 else ''}")
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
        lines.extend(["", "GAPS (>24h)"])
        for g in audit["gaps"][:10]:
            lines.append(f"  {g['from_ts']} -> {g['to_ts']} ({g['gap_hours']}h)")

    lines.extend([
        "",
        f"Research signals in DB: {audit['research_signals_parsed']}",
        f"Parser version: {audit['parser_version']}",
    ])
    return "\n".join(lines)


def render_check_source(result: dict[str, Any]) -> str:
    if result.get("ok"):
        src = result["source"]
        return (
            f"SOURCE OK: {src['backend']} @ {src.get('dsn_host')}/{src.get('dsn_database')}\n"
            f"  message_table: {result.get('message_table')} ({result.get('message_count')} rows)\n"
            f"  tables: {', '.join(result.get('discovered_tables', []))}"
        )
    return f"SOURCE FAILED: {result.get('reason_code')}\n  {result.get('error')}"
