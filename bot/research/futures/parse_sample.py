"""Parse-quality sample report — read-only on source DB."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bot.research.futures.config import PARSER_VERSION, PARSER_VERSION_V2
from bot.research.futures.parser_factory import get_parser
from bot.research.futures.source_reader import SourceReader, resolve_research_source


def _parse_status_v1(parsed) -> str:
    if parsed.side and parsed.symbol:
        return "SUCCESS"
    if parsed.fields_found:
        return "PARTIAL"
    return "FAILED"


def parse_sample(
    source: SourceReader | None = None,
    *,
    research_conn: sqlite3.Connection | None = None,
    source_conn: sqlite3.Connection | None = None,
    source_filter: str | None = None,
    limit: int = 30,
    parser_version: str | None = None,
    stratified: bool = False,
) -> dict[str, Any]:
    version = parser_version or PARSER_VERSION_V2
    parser = get_parser(version)

    if source is not None:
        source_reader, owns = source, False
    elif research_conn is not None:
        source_reader, owns = resolve_research_source(
            research_conn, source_conn=source_conn,
        )
    elif source_conn is not None:
        from bot.research.futures.source_reader import SqliteSourceReader
        source_reader, owns = SqliteSourceReader(source_conn), False
    else:
        raise ValueError("parse_sample requires source, research_conn, or source_conn")

    samples: list[dict[str, Any]] = []
    stats = {
        "parser_version": version,
        "source_rows_read": 0,
        "candidate_messages": 0,
        "success": 0,
        "partial": 0,
        "failed": 0,
        "gate_pass": 0,
    }

    keywords = ("LONG", "вход", "стоп", "TP") if stratified else (None,)
    seen: set[str] = set()
    per = max(8, limit // max(len(keywords), 1))

    try:
        for kw in keywords:
            rows_iter = source_reader.iter_raw_rows(
                source=source_filter,
                text_ilike=kw,
                limit=per if stratified else limit,
                order="DESC" if stratified else "ASC",
            )
            for row in rows_iter:
                if len(samples) >= limit:
                    break
                stats["source_rows_read"] += 1
                msg = source_reader.map_row(row)
                if msg is None or msg.message_id in seen:
                    continue
                seen.add(msg.message_id)
                stats["candidate_messages"] += 1

                if version == PARSER_VERSION_V2:
                    result = parser.parse(msg.text)
                    parsed = result.parsed
                    status = "SUCCESS" if result.passes_gate else (
                        "PARTIAL" if parsed.fields_found else "FAILED"
                    )
                    if result.passes_gate:
                        stats["gate_pass"] += 1
                    reason = result.gate_reason or (
                        "ok" if result.passes_gate else result.message_type.value
                    )
                    taxonomy = result.message_type.value
                else:
                    parsed = parser.parse(msg.text)
                    status = _parse_status_v1(parsed)
                    reason = ", ".join(parsed.errors) if parsed.errors else status.lower()
                    taxonomy = None

                stats[status.lower()] += 1
                samples.append({
                    "message_id": msg.message_id,
                    "source": msg.source,
                    "timestamp": msg.timestamp,
                    "status": status,
                    "taxonomy": taxonomy,
                    "reason": reason,
                    "raw_text_preview": msg.text[:240].replace("\n", " "),
                    "symbol": parsed.symbol,
                    "side": parsed.side,
                    "entry_min": parsed.entry_min,
                    "entry_max": parsed.entry_max,
                    "stop_loss": parsed.stop_loss,
                    "take_profits": parsed.take_profits,
                    "leverage": parsed.leverage,
                    "confidence": parsed.confidence,
                    "fields_found": parsed.fields_found,
                })
    finally:
        if owns:
            source_reader.close()

    return {
        "source_filter": source_filter,
        "limit": limit,
        "stratified": stratified,
        "stats": stats,
        "samples": samples,
    }


def render_parse_sample(report: dict[str, Any]) -> str:
    lines = [
        "FUTURES PARSE SAMPLE",
        "=" * 40,
        f"parser: {report['stats'].get('parser_version', 'unknown')}",
        f"source filter: {report.get('source_filter') or '(all)'}",
        f"limit: {report['limit']}",
        f"stratified: {report.get('stratified', False)}",
        "",
    ]
    st = report["stats"]
    lines.extend([
        "STATS",
        f"  source_rows_read: {st['source_rows_read']}",
        f"  candidate_messages: {st['candidate_messages']}",
        f"  SUCCESS: {st['success']}",
        f"  PARTIAL: {st['partial']}",
        f"  FAILED: {st['failed']}",
        f"  gate_pass (v2): {st.get('gate_pass', 0)}",
        "",
    ])
    for i, s in enumerate(report["samples"], start=1):
        lines.append(f"--- sample {i} [{s['status']}] {s['source']} id={s['message_id']} ---")
        if s.get("taxonomy"):
            lines.append(f"  taxonomy: {s['taxonomy']}")
        lines.append(f"  reason: {s['reason']}")
        lines.append(f"  text: {s['raw_text_preview']}")
        parsed_line = {
            "symbol": s["symbol"],
            "side": s["side"],
            "entry": [s["entry_min"], s["entry_max"]],
            "sl": s["stop_loss"],
            "tp": s["take_profits"],
        }
        lines.append(f"  parsed: {json.dumps(parsed_line, default=str)}")
        lines.append("")
    return "\n".join(lines).rstrip()
