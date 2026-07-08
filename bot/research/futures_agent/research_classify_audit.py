"""Dry-run classification audit on source messages."""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.source_reader import open_configured_source_reader
from bot.research.futures_agent.research_ingest import iter_source_messages
from bot.research.futures_agent.research_taxonomy import (
    ResearchContentType,
    classify_research_content,
)
from bot.research.futures_agent.research_utils import extract_symbols
from bot.research.futures_agent.thesis_extract import _extract_levels, _infer_direction

_RE_THIRD_PARTY_MARKERS = re.compile(
    r"(?i)\b(?:whale|0x[a-f0-9]{8,}|wallet\s+0x|borrowed\s+\w+\s+to\s+sell)\b",
)


@dataclass
class ClassifyAuditReport:
    sample_size: int = 0
    class_counts: Counter = field(default_factory=Counter)
    channel_class: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    examples: dict[str, list[dict]] = field(default_factory=dict)
    suspicious_explicit: list[dict] = field(default_factory=list)
    symbol_rate: float = 0.0
    direction_rate: float = 0.0
    level_rate: float = 0.0


def run_classify_audit(
    *,
    sample_size: int = 500,
    channel: str | None = None,
    seed: int = 42,
) -> ClassifyAuditReport:
    reader = open_configured_source_reader()
    report = ClassifyAuditReport()
    try:
        reservoir: list[dict[str, Any]] = []
        total_seen = 0
        rng = random.Random(seed)
        for row in iter_source_messages(reader, channel=channel, chunk_size=5000):
            total_seen += 1
            if len(reservoir) < sample_size:
                reservoir.append(row)
            else:
                j = rng.randint(0, total_seen - 1)
                if j < sample_size:
                    reservoir[j] = row

        symbols_found = directions_found = levels_found = 0
        for row in reservoir:
            text = row["raw_text"]
            cls = classify_research_content(text)
            ctype = cls.content_type.value
            report.sample_size += 1
            report.class_counts[ctype] += 1
            report.channel_class[row["channel_name"]][ctype] += 1

            syms = extract_symbols(text)
            if syms:
                symbols_found += 1
            direction = _infer_direction(text, cls.content_type)
            if direction != "NEUTRAL":
                directions_found += 1
            if _extract_levels(text):
                levels_found += 1

            if len(report.examples.get(ctype, [])) < 2:
                report.examples.setdefault(ctype, []).append({
                    "channel": row["channel_name"],
                    "preview": text[:240].replace("\n", " "),
                })

            if cls.content_type == ResearchContentType.EXPLICIT_SIGNAL and (
                cls.suspicious_explicit
                or _RE_THIRD_PARTY_MARKERS.search(text)
            ):
                if len(report.suspicious_explicit) < 15:
                    report.suspicious_explicit.append({
                        "channel": row["channel_name"],
                        "reasons": cls.reasons,
                        "preview": text[:240].replace("\n", " "),
                    })

        n = max(report.sample_size, 1)
        report.symbol_rate = symbols_found / n
        report.direction_rate = directions_found / n
        report.level_rate = levels_found / n
    finally:
        reader.close()
    return report


def render_classify_audit(report: ClassifyAuditReport) -> str:
    lines = [
        "RESEARCH CLASSIFY AUDIT (dry-run)",
        f"sample_size: {report.sample_size}",
        "",
        "Class counts:",
    ]
    for ctype, n in report.class_counts.most_common():
        lines.append(f"  {ctype}: {n}")

    lines.extend([
        "",
        f"Symbol extraction rate: {report.symbol_rate:.1%}",
        f"Direction extraction rate: {report.direction_rate:.1%}",
        f"Level extraction rate: {report.level_rate:.1%}",
        "",
        "Per-channel distribution:",
    ])
    for ch, counts in sorted(report.channel_class.items()):
        top = counts.most_common(3)
        summary = ", ".join(f"{k}={v}" for k, v in top)
        lines.append(f"  {ch}: {summary}")

    lines.append("")
    lines.append("Examples per class:")
    for ctype, exs in sorted(report.examples.items()):
        lines.append(f"  [{ctype}]")
        for ex in exs:
            lines.append(f"    {ex['channel']}: {ex['preview']}")

    if report.suspicious_explicit:
        lines.append("")
        lines.append("Suspicious EXPLICIT_SIGNAL examples:")
        for ex in report.suspicious_explicit:
            lines.append(f"  {ex['channel']}: {ex['preview']}")

    return "\n".join(lines)
