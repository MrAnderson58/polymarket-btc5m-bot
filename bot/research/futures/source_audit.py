"""Phase 1 — audit existing futures/telegram source data."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.db_config import get_futures_research_database_path
from bot.research.futures.parser import parse_signal_text
from bot.research.futures.schema import SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_data import SOURCE_TABLES
from bot.research.futures.source_reader import (
    DEFAULT_PARSE_SAMPLE_LIMIT,
    FULL_PARSE_SCAN_MAX_ROWS,
    SourceReader,
    open_source_reader,
)


def _compute_parse_stats(
    source: SourceReader,
    *,
    source_filter: str | None,
    total_count: int,
    parse_sample_limit: int,
) -> dict[str, Any]:
    if source_filter:
        scan_limit = total_count if total_count <= FULL_PARSE_SCAN_MAX_ROWS else parse_sample_limit
        scope = "full" if scan_limit >= total_count else "sample"
    else:
        scan_limit = min(total_count, parse_sample_limit) if total_count else 0
        scope = "sample"

    parseable = 0
    long_count = 0
    short_count = 0
    symbols: set[str] = set()
    ts_issues = 0
    scanned = 0

    for msg in source.iter_telegram_messages(limit=scan_limit, source=source_filter):
        scanned += 1
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

    return {
        "parseable_long_short": parseable,
        "long_count": long_count,
        "short_count": short_count,
        "unique_symbols": sorted(symbols),
        "symbol_count": len(symbols),
        "timestamp_quality_issues": ts_issues,
        "parse_stats_scope": scope,
        "parse_sample_size": scanned,
    }


def audit_source_data(
    research_conn: sqlite3.Connection,
    source: SourceReader | None = None,
    *,
    source_filter: str | None = None,
    parse_sample_limit: int = DEFAULT_PARSE_SAMPLE_LIMIT,
) -> dict[str, Any]:
    ensure_tables(research_conn)
    owns_source = source is None
    if source is None:
        source = open_source_reader(sqlite_conn=research_conn)

    try:
        tables = source.list_source_tables()
        channels = source.channel_stats()
        msg_stats = source.message_stats(source=source_filter)
        parse_block = _compute_parse_stats(
            source,
            source_filter=source_filter,
            total_count=msg_stats.count,
            parse_sample_limit=parse_sample_limit,
        )

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
                "filter": source_filter,
            },
            "research_db": {
                "backend": "sqlite",
                "path": get_futures_research_database_path(),
            },
            "column_map": msg_stats.column_map,
            "tables": tables,
            "messages": {
                "count": msg_stats.count,
                "first_timestamp": msg_stats.first_timestamp,
                "last_timestamp": msg_stats.last_timestamp,
                "duration_seconds": msg_stats.duration_seconds,
                "channels": channels,
                **parse_block,
            },
            "research_signals_parsed": parsed_in_db,
            "parser_version": PARSER_VERSION,
            "candle_availability": _candle_availability_note(
                msg_stats.first_timestamp, msg_stats.last_timestamp,
            ),
        }
    finally:
        if owns_source:
            source.close()


def check_source_connection(*, sqlite_conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    from bot.research.futures.source_reader import SourceConfigError

    try:
        source = open_source_reader(sqlite_conn=sqlite_conn)
        tables = source.list_source_tables()
        resolved = source.resolve_message_table()
        msg_table = resolved[0] if resolved else "telegram_messages"
        msg_count = tables.get(msg_table, {}).get("row_count", 0)
        column_map = tables.get(msg_table, {}).get("column_map")
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
            "column_map": column_map,
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
        f"  channel filter: {src.get('filter') or '(all)'}",
    ])
    if src.get("reason_code"):
        lines.append(f"  reason_code: {src['reason_code']}")
    if audit.get("column_map"):
        cm = audit["column_map"]
        lines.append(
            f"  column map: text={cm.get('text')} ts={cm.get('timestamp')} "
            f"id={cm.get('message_id')} source={cm.get('source')}"
        )
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
        if info.get("column_map"):
            cm = info["column_map"]
            lines.append(
                f"    map: text={cm.get('text')} ts={cm.get('timestamp')} id={cm.get('message_id')}"
            )
        if info.get("ts_range"):
            lines.append(f"    ts range: {info['ts_range']}")

    m = audit["messages"]
    lines.extend([
        "",
        "MESSAGES",
        f"  count: {m['count']}",
        f"  date range: {m['first_timestamp']} .. {m['last_timestamp']}",
        f"  duration: {m['duration_seconds']}s",
        f"  parse stats scope: {m.get('parse_stats_scope', 'n/a')} (n={m.get('parse_sample_size', 0)})",
        f"  parseable LONG/SHORT: {m['parseable_long_short']}",
        f"  LONG: {m['long_count']}  SHORT: {m['short_count']}",
        f"  symbols ({m['symbol_count']}): {', '.join(m['unique_symbols'][:20])}",
        f"  timestamp issues: {m['timestamp_quality_issues']}",
        "",
        "CHANNELS",
    ])
    for ch in m["channels"][:20]:
        lines.append(f"  {ch['source']}: {ch['count']} messages")

    lines.extend([
        "",
        f"Research signals in DB: {audit['research_signals_parsed']}",
        f"Parser version: {audit['parser_version']}",
    ])
    return "\n".join(lines)


def render_check_source(result: dict[str, Any]) -> str:
    if result.get("ok"):
        src = result["source"]
        cm = result.get("column_map") or {}
        map_line = ""
        if cm:
            map_line = (
                f"\n  column map: text={cm.get('text')} ts={cm.get('timestamp')} "
                f"id={cm.get('message_id')} source={cm.get('source')}"
            )
        return (
            f"SOURCE OK: {src['backend']} @ {src.get('dsn_host')}/{src.get('dsn_database')}\n"
            f"  message_table: {result.get('message_table')} ({result.get('message_count')} rows)"
            f"{map_line}\n"
            f"  tables: {', '.join(result.get('discovered_tables', []))}"
        )
    return f"SOURCE FAILED: {result.get('reason_code')}\n  {result.get('error')}"
