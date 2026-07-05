"""Lifecycle / thread linking feasibility research."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from bot.research.futures.parser_v2 import extract_symbol_v2
from bot.research.futures.source_reader import SourceReader, resolve_research_source
from bot.research.futures.taxonomy import MessageType, classify_message

FOLLOWUP_WINDOW_SEC = 7 * 86400


def analyze_lifecycle(
    *,
    research_conn: sqlite3.Connection | None = None,
    source: SourceReader | None = None,
    source_filter: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    if source is None:
        assert research_conn is not None
        source, owns = resolve_research_source(research_conn)
    else:
        owns = False

    signals: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    try:
        for row in source.iter_raw_rows(source=source_filter, limit=limit, order="ASC"):
            msg = source.map_row(row)
            if not msg:
                continue
            tax = classify_message(msg.text)
            sym, _ = extract_symbol_v2(msg.text)
            rec = {
                "message_id": msg.message_id,
                "timestamp": msg.timestamp,
                "type": tax.message_type,
                "symbol": sym,
                "source": msg.source,
            }
            if tax.message_type == MessageType.EXPLICIT_SIGNAL:
                signals.append(rec)
            elif tax.message_type in (
                MessageType.TP_HIT, MessageType.SL_HIT,
                MessageType.POSITION_CLOSE, MessageType.TRADE_UPDATE,
            ):
                updates.append(rec)

        linked = 0
        ambiguous = 0
        unlinked_updates = 0
        examples: list[dict[str, Any]] = []

        for upd in updates:
            if not upd["symbol"]:
                unlinked_updates += 1
                continue
            candidates = [
                s for s in signals
                if s["symbol"] == upd["symbol"]
                and s["timestamp"] < upd["timestamp"]
                and upd["timestamp"] - s["timestamp"] <= FOLLOWUP_WINDOW_SEC
            ]
            if len(candidates) == 1:
                linked += 1
                if len(examples) < 5:
                    examples.append({"signal": candidates[0], "update": upd, "link": "unique"})
            elif len(candidates) > 1:
                ambiguous += 1
                if len(examples) < 8:
                    examples.append({
                        "signal": candidates[-1],
                        "update": upd,
                        "link": f"ambiguous_{len(candidates)}",
                    })
            else:
                unlinked_updates += 1

        return {
            "source_filter": source_filter,
            "signals_found": len(signals),
            "updates_found": len(updates),
            "linked_updates": linked,
            "ambiguous_updates": ambiguous,
            "unlinked_updates": unlinked_updates,
            "link_rate": round(linked / len(updates), 3) if updates else 0.0,
            "feasible": linked > 0 and (linked / max(len(updates), 1)) >= 0.3,
            "window_days": FOLLOWUP_WINDOW_SEC // 86400,
            "examples": examples,
            "notes": [
                "Conservative same-symbol time-window linking only.",
                "No reply/reference metadata used (not in current schema).",
                "Do not treat ambiguous links as ground truth.",
            ],
        }
    finally:
        if owns:
            source.close()


def render_lifecycle_report(report: dict[str, Any]) -> str:
    lines = [
        "LIFECYCLE LINKING FEASIBILITY",
        "=" * 40,
        f"source: {report.get('source_filter') or '(all)'}",
        f"explicit signals: {report['signals_found']}",
        f"follow-up messages: {report['updates_found']}",
        f"linked (unique): {report['linked_updates']}",
        f"ambiguous: {report['ambiguous_updates']}",
        f"unlinked: {report['unlinked_updates']}",
        f"link rate: {report['link_rate']}",
        f"feasible for research: {'yes' if report['feasible'] else 'no'}",
        "",
        "NOTES",
    ]
    for n in report.get("notes", []):
        lines.append(f"  - {n}")
    if report.get("examples"):
        lines.extend(["", "EXAMPLES"])
        for ex in report["examples"]:
            lines.append(
                f"  {ex['link']}: signal {ex['signal']['message_id']} -> update {ex['update']['message_id']} "
                f"({ex['update']['type'].value})"
            )
    return "\n".join(lines)
