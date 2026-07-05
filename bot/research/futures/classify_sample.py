"""Stratified message classification audit for production sources."""

from __future__ import annotations

import random
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from bot.research.futures.source_data import RawMessage, row_to_raw_message
from bot.research.futures.source_reader import SourceReader, resolve_research_source
from bot.research.futures.taxonomy import MessageType, classify_message

STRATA = (
    ("long_short", "LONG"),
    ("entry_ru_en", "вход"),
    ("stop_ru_en", "стоп"),
    ("tp_ru_en", "тейк"),
    ("sl_en", "SL"),
    ("tp_en", "TP"),
)

SAMPLE_PREVIEW_LEN = 180


def _preview(text: str) -> str:
    t = text.replace("\n", " ").strip()
    return t[:SAMPLE_PREVIEW_LEN] + ("…" if len(t) > SAMPLE_PREVIEW_LEN else "")


def _load_stratum(
    source: SourceReader,
    *,
    source_filter: str | None,
    text_ilike: str | None,
    limit: int,
    order: str = "DESC",
) -> list[RawMessage]:
    out: list[RawMessage] = []
    for row in source.iter_raw_rows(
        source=source_filter, text_ilike=text_ilike, limit=limit, order=order,
    ):
        msg = source.map_row(row)
        if msg:
            out.append(msg)
    return out


def classify_sample(
    *,
    research_conn: sqlite3.Connection | None = None,
    source: SourceReader | None = None,
    source_filter: str | None = None,
    limit: int = 500,
    per_stratum: int = 40,
    random_sample: int = 50,
) -> dict[str, Any]:
    if source is None:
        assert research_conn is not None
        source, owns = resolve_research_source(research_conn)
    else:
        owns = False

    try:
        counts: Counter[str] = Counter()
        examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        strata_loaded: dict[str, int] = {}
        all_messages: list[RawMessage] = []
        seen_ids: set[str] = set()

        def ingest(msgs: list[RawMessage], stratum: str) -> None:
            strata_loaded[stratum] = len(msgs)
            for msg in msgs:
                if msg.message_id in seen_ids:
                    continue
                seen_ids.add(msg.message_id)
                all_messages.append(msg)

        for name, keyword in STRATA:
            ingest(
                _load_stratum(source, source_filter=source_filter, text_ilike=keyword, limit=per_stratum),
                name,
            )

        ingest(
            _load_stratum(source, source_filter=source_filter, text_ilike=None, limit=random_sample, order="DESC"),
            "recent_desc",
        )
        ingest(
            _load_stratum(source, source_filter=source_filter, text_ilike=None, limit=random_sample, order="ASC"),
            "chronological_asc",
        )

        if len(all_messages) < limit:
            extra = _load_stratum(
                source, source_filter=source_filter, text_ilike=None,
                limit=limit - len(all_messages), order="DESC",
            )
            ingest(extra, "fill")

        random.shuffle(all_messages)
        all_messages = all_messages[:limit]

        for msg in all_messages:
            tax = classify_message(msg.text)
            counts[tax.message_type.value] += 1
            if len(examples[tax.message_type.value]) < 3:
                examples[tax.message_type.value].append({
                    "message_id": msg.message_id,
                    "timestamp": msg.timestamp,
                    "month": datetime.utcfromtimestamp(msg.timestamp).strftime("%Y-%m") if msg.timestamp else None,
                    "preview": _preview(msg.text),
                    "reasons": tax.reasons,
                })

        month_counts: Counter[str] = Counter()
        for msg in all_messages:
            if msg.timestamp:
                month_counts[datetime.utcfromtimestamp(msg.timestamp).strftime("%Y-%m")] += 1

        return {
            "source_filter": source_filter,
            "total_classified": len(all_messages),
            "strata_loaded": strata_loaded,
            "type_counts": dict(counts),
            "month_counts": dict(month_counts),
            "examples": dict(examples),
        }
    finally:
        if owns:
            source.close()


def render_classify_sample(report: dict[str, Any]) -> str:
    lines = [
        "FUTURES MESSAGE TAXONOMY AUDIT",
        "=" * 40,
        f"source filter: {report.get('source_filter') or '(all)'}",
        f"total classified: {report['total_classified']}",
        "",
        "STRATA LOADED",
    ]
    for k, v in report.get("strata_loaded", {}).items():
        lines.append(f"  {k}: {v}")
    lines.extend(["", "TYPE COUNTS"])
    for t in MessageType:
        n = report["type_counts"].get(t.value, 0)
        if n:
            lines.append(f"  {t.value}: {n}")
    if report.get("month_counts"):
        lines.extend(["", "MONTH SLICES"])
        for month, n in sorted(report["month_counts"].items()):
            lines.append(f"  {month}: {n}")
    lines.extend(["", "EXAMPLES"])
    for t, exs in report.get("examples", {}).items():
        lines.append(f"  [{t}]")
        for ex in exs:
            lines.append(f"    id={ex['message_id']} ts={ex['timestamp']} month={ex.get('month')}")
            lines.append(f"      {ex['preview']}")
    return "\n".join(lines)
