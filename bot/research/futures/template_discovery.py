"""Discover recurring explicit signal template families."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from bot.research.futures.source_reader import SourceReader, resolve_research_source
from bot.research.futures.taxonomy import MessageType, classify_message

_FIELD_PATTERNS = {
    "symbol": re.compile(r"(?i)(?:#?\$?[A-Z]{2,10}\s+(?:LONG|SHORT)|(?:LONG|SHORT)\s+#?\$?[A-Z]{2,10})"),
    "direction": re.compile(r"(?i)\b(LONG|SHORT|BUY|SELL)\b"),
    "entry": re.compile(r"(?i)(?:entry|enter|вход)\s*[:@]?\s*\d"),
    "stop": re.compile(r"(?i)(?:sl|stop|стоп)\s*[:@]?\s*\d"),
    "tp": re.compile(r"(?i)(?:tp\d*|take profit|цель|тейк)\s*[:@]?\s*\d"),
    "leverage": re.compile(r"(?i)(?:lev|leverage|x)\s*[:@]?\s*\d"),
    "timeframe": re.compile(r"(?i)\b(\d+\s*[mhdw]|scalp|intraday|swing)\b"),
}


def _template_fingerprint(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()][:8]
    norm = []
    for ln in lines:
        ln = re.sub(r"\d+(?:\.\d+)?", "#", ln)
        ln = re.sub(r"\s+", " ", ln)
        norm.append(ln[:60])
    return " | ".join(norm[:4])


def discover_templates(
    *,
    research_conn: sqlite3.Connection | None = None,
    source: SourceReader | None = None,
    source_filter: str | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    if source is None:
        assert research_conn is not None
        source, owns = resolve_research_source(research_conn)
    else:
        owns = False

    families: Counter[str] = Counter()
    family_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    family_fields: dict[str, Counter[str]] = defaultdict(Counter)
    family_dates: dict[str, list[int]] = defaultdict(list)

    try:
        n = 0
        for row in source.iter_raw_rows(source=source_filter, limit=limit, order="ASC"):
            msg = source.map_row(row)
            if not msg:
                continue
            tax = classify_message(msg.text)
            if tax.message_type != MessageType.EXPLICIT_SIGNAL:
                continue
            fp = _template_fingerprint(msg.text)
            families[fp] += 1
            family_dates[fp].append(msg.timestamp)
            for field, pat in _FIELD_PATTERNS.items():
                if pat.search(msg.text):
                    family_fields[fp][field] += 1
            if len(family_examples[fp]) < 2:
                fields_present = [f for f, c in family_fields[fp].items() if c]
                family_examples[fp].append({
                    "message_id": msg.message_id,
                    "timestamp": msg.timestamp,
                    "preview": msg.text[:300].replace("\n", " "),
                    "fields": fields_present,
                })
            n += 1

        ranked = []
        for fp, count in families.most_common(25):
            ts_list = [t for t in family_dates[fp] if t]
            ranked.append({
                "template": fp,
                "count": count,
                "date_range": (
                    datetime.utcfromtimestamp(min(ts_list)).isoformat() if ts_list else None,
                    datetime.utcfromtimestamp(max(ts_list)).isoformat() if ts_list else None,
                ),
                "fields_available": {
                    k: family_fields[fp][k] for k in sorted(family_fields[fp])
                },
                "examples": family_examples[fp],
            })

        return {
            "source_filter": source_filter,
            "explicit_signals_scanned": n,
            "template_families": ranked,
            "unique_templates": len(families),
        }
    finally:
        if owns:
            source.close()


def render_template_report(report: dict[str, Any]) -> str:
    lines = [
        "SIGNAL TEMPLATE DISCOVERY",
        "=" * 40,
        f"source: {report.get('source_filter') or '(all)'}",
        f"explicit signals scanned: {report['explicit_signals_scanned']}",
        f"unique templates: {report['unique_templates']}",
        "",
    ]
    for i, fam in enumerate(report["template_families"][:15], start=1):
        lines.append(f"--- family {i} (n={fam['count']}) ---")
        lines.append(f"  template: {fam['template']}")
        lines.append(f"  date range: {fam['date_range']}")
        lines.append(f"  fields: {fam['fields_available']}")
        for ex in fam["examples"]:
            lines.append(f"  example id={ex['message_id']}: {ex['preview'][:160]}")
        lines.append("")
    return "\n".join(lines).rstrip()
