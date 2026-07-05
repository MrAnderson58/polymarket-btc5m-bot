"""Parse-quality sample report — read-only on source DB."""

from __future__ import annotations

import json
from typing import Any

from bot.research.futures.parser import SignalParser
from bot.research.futures.source_reader import SourceReader, open_source_reader


def _parse_status(parsed) -> str:
    if parsed.side and parsed.symbol:
        return "SUCCESS"
    if parsed.fields_found:
        return "PARTIAL"
    return "FAILED"


def _parse_reason(parsed, status: str) -> str:
    if status == "SUCCESS":
        return "ok"
    if parsed.errors:
        return "; ".join(parsed.errors[:3])
    if status == "PARTIAL":
        missing = []
        if not parsed.side:
            missing.append("no side")
        if not parsed.symbol:
            missing.append("no symbol")
        return ", ".join(missing) or "partial fields"
    return "no signal pattern detected"


def parse_sample(
    source: SourceReader | None = None,
    *,
    source_filter: str | None = None,
    limit: int = 30,
    sqlite_conn=None,
) -> dict[str, Any]:
    owns = source is None
    if source is None:
        source = open_source_reader(sqlite_conn=sqlite_conn)

    parser = SignalParser()
    samples: list[dict[str, Any]] = []
    stats = {
        "source_rows_read": 0,
        "candidate_messages": 0,
        "success": 0,
        "partial": 0,
        "failed": 0,
    }

    try:
        for row in source.iter_raw_rows(limit=limit, source=source_filter):
            stats["source_rows_read"] += 1
            msg = source.map_row(row)
            if msg is None:
                continue
            stats["candidate_messages"] += 1
            parsed = parser.parse(msg.text)
            status = _parse_status(parsed)
            stats[status.lower()] += 1
            samples.append({
                "message_id": msg.message_id,
                "source": msg.source,
                "timestamp": msg.timestamp,
                "status": status,
                "reason": _parse_reason(parsed, status),
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
            source.close()

    return {
        "source_filter": source_filter,
        "limit": limit,
        "stats": stats,
        "samples": samples,
    }


def render_parse_sample(report: dict[str, Any]) -> str:
    lines = [
        "FUTURES PARSE SAMPLE",
        "=" * 40,
        f"source filter: {report.get('source_filter') or '(all)'}",
        f"limit: {report['limit']}",
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
        "",
    ])
    for i, s in enumerate(report["samples"], start=1):
        lines.append(f"--- sample {i} [{s['status']}] {s['source']} id={s['message_id']} ---")
        lines.append(f"  reason: {s['reason']}")
        lines.append(f"  text: {s['raw_text_preview']}")
        parsed_line = {
            "symbol": s["symbol"],
            "side": s["side"],
            "entry": [s["entry_min"], s["entry_max"]],
            "sl": s["stop_loss"],
            "tp": s["take_profits"],
            "leverage": s["leverage"],
            "confidence": s["confidence"],
        }
        lines.append(f"  parsed: {json.dumps(parsed_line, default=str)}")
        lines.append("")
    return "\n".join(lines).rstrip()
