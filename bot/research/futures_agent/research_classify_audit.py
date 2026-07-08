"""Dry-run classification audit on source messages."""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.source_reader import SourceReader
from bot.research.futures_agent.source_requirements import open_stage3_source_reader
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

_RE_EXIT_VERBS = re.compile(
    r"(?i)\b(close(?:d)?|exited?|exit|take profit|took profit|partial exit|reduce(?:d)? position)\b",
)

_RE_WHALE_ACTION = re.compile(
    r"(?i)\b(bought|sold|borrowed|deposited|withdrew|transferred|repaid|opened|closed|liquidat(?:ed|ion))\b",
)

_RE_DEBANK_PROFILE = re.compile(r"(?i)debank\.com/profile")


@dataclass
class ClassifyAuditReport:
    sample_size: int = 0
    class_counts: Counter = field(default_factory=Counter)
    channel_class: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    examples: dict[str, list[dict]] = field(default_factory=dict)
    suspicious_explicit: list[dict] = field(default_factory=list)
    suspicious_trader_thesis: list[dict] = field(default_factory=list)
    suspicious_whale_flow: list[dict] = field(default_factory=list)
    suspicious_news_event: list[dict] = field(default_factory=list)
    symbol_rate: float = 0.0
    direction_rate: float = 0.0
    level_rate: float = 0.0
    sampled_by_channel: dict[str, int] = field(default_factory=dict)
    requested_by_channel: dict[str, int] = field(default_factory=dict)


def run_classify_audit(
    *,
    sample_size: int = 500,
    channel: str | None = None,
    seed: int = 42,
    stratified: bool = False,
) -> ClassifyAuditReport:
    reader = open_stage3_source_reader()
    report = ClassifyAuditReport()
    try:
        rng = random.Random(seed)
        reservoir: list[dict[str, Any]] = []

        if stratified and channel is None:
            channel_rows = reader.channel_stats()
            ch_names = sorted([r["source"] for r in channel_rows if r.get("count", 0) > 0 and r.get("source")])
            if not ch_names:
                ch_names = ["all"]

            sizes: dict[str, int] = {}
            n = len(ch_names)
            if sample_size <= n:
                for i, ch in enumerate(ch_names):
                    sizes[ch] = 1 if i < sample_size else 0
            else:
                base = sample_size // n
                rem = sample_size - base * n
                for i, ch in enumerate(ch_names):
                    sizes[ch] = base + (1 if i < rem else 0)

            report.requested_by_channel = dict(sizes)

            reservoirs_by_channel: dict[str, list[dict[str, Any]]] = {
                ch: [] for ch in ch_names if sizes.get(ch, 0) > 0
            }
            seen_by_channel: dict[str, int] = {ch: 0 for ch in reservoirs_by_channel}

            for row in iter_source_messages(reader, channel=None, chunk_size=5000):
                ch = row["channel_name"]
                if ch not in reservoirs_by_channel:
                    continue
                seen_by_channel[ch] += 1
                k = sizes[ch]
                cur = reservoirs_by_channel[ch]
                if len(cur) < k:
                    cur.append(row)
                else:
                    j = rng.randint(0, seen_by_channel[ch] - 1)
                    if j < k:
                        cur[j] = row

            for ch, cur in reservoirs_by_channel.items():
                report.sampled_by_channel[ch] = len(cur)
                reservoir.extend(cur)
        else:
            total_seen = 0
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

            lowered = text.lower()

            # Suspicious blocks (precision-first): likely fragments that should not become outcomes/scores.
            if cls.content_type == ResearchContentType.EXPLICIT_SIGNAL:
                syms2 = extract_symbols(text)
                levels2 = _extract_levels(text)
                has_entry = any(l.level_type.startswith("ENTRY") for l in levels2)
                has_stop = any(l.level_type == "STOP" for l in levels2)
                has_target = any(l.level_type == "TARGET" for l in levels2)
                actionable = (has_entry and has_stop) or (has_entry and has_target)
                if (not syms2) or (not actionable):
                    if len(report.suspicious_explicit) < 25:
                        report.suspicious_explicit.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "precision_check_failed",
                        })

            if cls.content_type == ResearchContentType.TRADER_THESIS:
                if _RE_EXIT_VERBS.search(text) and ("long" in lowered or "short" in lowered):
                    if len(report.suspicious_trader_thesis) < 25:
                        report.suspicious_trader_thesis.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "looks_like_exit_or_pnl",
                        })

            if cls.content_type == ResearchContentType.WHALE_FLOW:
                has_debank = bool(_RE_DEBANK_PROFILE.search(text))
                has_econ = bool(_RE_WHALE_ACTION.search(text))
                if has_debank and not has_econ:
                    if len(report.suspicious_whale_flow) < 25:
                        report.suspicious_whale_flow.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "url_only_no_econ_action",
                        })

            if cls.content_type == ResearchContentType.NEWS_EVENT:
                if "hackers" in lowered and not ("hacked" in lowered or "hack" in lowered or "exploit" in lowered):
                    if len(report.suspicious_news_event) < 25:
                        report.suspicious_news_event.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "hackers_noise",
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

    if report.sampled_by_channel:
        lines.append("")
        lines.append("Sampled by channel:")
        for ch in sorted(report.sampled_by_channel):
            req = report.requested_by_channel.get(ch, 0)
            act = report.sampled_by_channel[ch]
            lines.append(f"  {ch}: requested={req} actual={act}")

    lines.append("")
    lines.append("Examples per class:")
    for ctype, exs in sorted(report.examples.items()):
        lines.append(f"  [{ctype}]")
        for ex in exs:
            lines.append(f"    {ex['channel']}: {ex['preview']}")

    def _render_suspicious(title: str, items: list[dict]) -> None:
        nonlocal lines
        if not items:
            return
        lines.append("")
        lines.append(title)
        for ex in items:
            lines.append(f"  {ex['channel']}: {ex['preview']}")

    _render_suspicious("Suspicious EXPLICIT_SIGNAL examples:", report.suspicious_explicit)
    _render_suspicious("Suspicious TRADER_THESIS examples:", report.suspicious_trader_thesis)
    _render_suspicious("Suspicious WHALE_FLOW examples:", report.suspicious_whale_flow)
    _render_suspicious("Suspicious NEWS_EVENT examples:", report.suspicious_news_event)

    return "\n".join(lines)
