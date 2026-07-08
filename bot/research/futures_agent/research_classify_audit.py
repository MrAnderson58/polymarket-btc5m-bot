"""Dry-run classification audit on source messages."""

from __future__ import annotations

import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.source_reader import SourceReader
from bot.research.futures_agent.research_ingest import iter_source_messages
from bot.research.futures_agent.signal_format_audit import (
    SignalFormatTier,
    is_audit_signal_candidate,
    parse_signal_format_audit,
)
from bot.research.futures_agent.source_requirements import open_stage3_source_reader
from bot.research.futures_agent.research_taxonomy import (
    ResearchContentType,
    _RE_NON_TRADE_UPDATE,
    _has_technical_levels_context,
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

_SUSPICIOUS_CLASS_MAP = {
    "EXPLICIT_SIGNAL": "suspicious_explicit",
    "TRADER_THESIS": "suspicious_trader_thesis",
    "WHALE_FLOW": "suspicious_whale_flow",
    "NEWS_EVENT": "suspicious_news_event",
    "TRADE_UPDATE": "suspicious_trade_update",
    "TECHNICAL_LEVELS": "suspicious_technical_levels",
}


def is_structural_explicit_candidate(text: str) -> bool:
    """Audit-only: delegate to independent format detector (any non-NONE tier)."""
    return is_audit_signal_candidate(text)


@dataclass
class ExplicitRecallAuditReport:
    scanned: int = 0
    structural_candidates: int = 0
    full_signal: int = 0
    deferred_stop_signal: int = 0
    partial_signal: int = 0
    classified_explicit: int = 0
    true_positives: int = 0
    overlap_full: int = 0
    overlap_deferred: int = 0
    overlap_partial: int = 0
    missed_explicit: list[dict] = field(default_factory=list)
    precision_review: list[dict] = field(default_factory=list)
    recall_review: list[dict] = field(default_factory=list)
    channel: str | None = None


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
    suspicious_trade_update: list[dict] = field(default_factory=list)
    suspicious_technical_levels: list[dict] = field(default_factory=list)
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
    suspicious_class: str | None = None,
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

            if cls.content_type == ResearchContentType.TRADE_UPDATE:
                if _RE_NON_TRADE_UPDATE.search(text):
                    if len(report.suspicious_trade_update) < 25:
                        report.suspicious_trade_update.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "non_position_update",
                        })

            if cls.content_type == ResearchContentType.TECHNICAL_LEVELS:
                if not _has_technical_levels_context(text):
                    if len(report.suspicious_technical_levels) < 25:
                        report.suspicious_technical_levels.append({
                            "channel": row["channel_name"],
                            "preview": text[:240].replace("\n", " "),
                            "note": "bare_support_resistance",
                        })

        n = max(report.sample_size, 1)
        report.symbol_rate = symbols_found / n
        report.direction_rate = directions_found / n
        report.level_rate = levels_found / n
    finally:
        reader.close()
    return report


def run_explicit_recall_audit(
    *,
    channel: str | None = "signalyp",
    limit: int | None = None,
    progress_every: int = 500,
) -> ExplicitRecallAuditReport:
    """Scan source rows for structural explicit-signal candidates vs taxonomy."""
    reader = open_stage3_source_reader()
    report = ExplicitRecallAuditReport(channel=channel)
    t0 = time.monotonic()
    try:
        for i, row in enumerate(
            iter_source_messages(reader, channel=channel, chunk_size=5000),
            start=1,
        ):
            if limit is not None and i > limit:
                break
            report.scanned += 1
            text = row["raw_text"]
            parsed = parse_signal_format_audit(text)
            structural = parsed.tier != SignalFormatTier.NONE
            cls = classify_research_content(text)
            is_explicit = cls.content_type == ResearchContentType.EXPLICIT_SIGNAL

            if structural:
                report.structural_candidates += 1
            if parsed.tier == SignalFormatTier.FULL_SIGNAL:
                report.full_signal += 1
                if is_explicit:
                    report.overlap_full += 1
            elif parsed.tier == SignalFormatTier.DEFERRED_STOP_SIGNAL:
                report.deferred_stop_signal += 1
                if is_explicit:
                    report.overlap_deferred += 1
            elif parsed.tier == SignalFormatTier.PARTIAL_SIGNAL:
                report.partial_signal += 1
                if is_explicit:
                    report.overlap_partial += 1
            if is_explicit:
                report.classified_explicit += 1
            if structural and is_explicit:
                report.true_positives += 1
            elif structural and not is_explicit:
                if len(report.missed_explicit) < 50:
                    report.missed_explicit.append({
                        "channel": row["channel_name"],
                        "classified": cls.content_type.value,
                        "tier": parsed.tier.value,
                        "preview": text[:240].replace("\n", " "),
                    })
                if len(report.recall_review) < 25:
                    report.recall_review.append({
                        "channel": row["channel_name"],
                        "classified": cls.content_type.value,
                        "tier": parsed.tier.value,
                        "preview": text[:240].replace("\n", " "),
                    })
            elif is_explicit and not structural:
                if len(report.precision_review) < 25:
                    report.precision_review.append({
                        "channel": row["channel_name"],
                        "preview": text[:240].replace("\n", " "),
                    })

            if progress_every > 0 and i % progress_every == 0:
                elapsed = max(time.monotonic() - t0, 0.001)
                rate = i / elapsed
                print(
                    f"Processed: {i:,} | candidates={report.structural_candidates:,} "
                    f"(FULL={report.full_signal:,} DEF={report.deferred_stop_signal:,}) "
                    f"| explicit={report.classified_explicit:,} "
                    f"| tp={report.true_positives:,} | {rate:.0f} msg/s",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        reader.close()
    return report


def render_explicit_recall_audit(report: ExplicitRecallAuditReport) -> str:
    missed = report.structural_candidates - report.true_positives
    false_explicit = report.classified_explicit - report.true_positives
    recall = (
        report.true_positives / report.structural_candidates
        if report.structural_candidates
        else 0.0
    )
    precision = (
        report.true_positives / report.classified_explicit
        if report.classified_explicit
        else 0.0
    )
    lines = [
        "EXPLICIT SIGNAL RECALL AUDIT (independent format detector vs taxonomy)",
        f"channel: {report.channel or 'all'}",
        f"scanned: {report.scanned:,}",
        "",
        "Audit candidate tiers:",
        f"  FULL_SIGNAL:          {report.full_signal:,}",
        f"  DEFERRED_STOP_SIGNAL: {report.deferred_stop_signal:,}",
        f"  PARTIAL_SIGNAL:       {report.partial_signal:,}",
        f"  total candidates:     {report.structural_candidates:,}",
        f"  classified EXPLICIT_SIGNAL: {report.classified_explicit:,}",
        "",
        "Classifier overlap by tier:",
        f"  FULL overlap:     {report.overlap_full:,} / {report.full_signal:,}"
        f" ({report.overlap_full / max(report.full_signal, 1):.1%})",
        f"  DEFERRED overlap: {report.overlap_deferred:,} / {report.deferred_stop_signal:,}"
        f" ({report.overlap_deferred / max(report.deferred_stop_signal, 1):.1%})",
        f"  PARTIAL overlap:  {report.overlap_partial:,} / {report.partial_signal:,}"
        f" ({report.overlap_partial / max(report.partial_signal, 1):.1%})",
        "",
        "Confusion summary (all tiers):",
        f"  true positives (candidate + explicit): {report.true_positives:,}",
        f"  missed (candidate, not explicit):    {missed:,}",
        f"  precision review (explicit, no tier):  {false_explicit:,}",
        f"  recall (tp / candidates):              {recall:.1%}",
        f"  precision (tp / classified):           {precision:.1%}",
    ]
    if report.missed_explicit:
        lines.append("")
        lines.append("Missed explicit examples (structural candidate, not EXPLICIT_SIGNAL):")
        for ex in report.missed_explicit[:25]:
            lines.append(
                f"  [{ex.get('tier', '?')}/{ex['classified']}] {ex['channel']}: {ex['preview']}",
            )
    if report.precision_review:
        lines.append("")
        lines.append("Precision review sample (EXPLICIT_SIGNAL without full structure):")
        for ex in report.precision_review:
            lines.append(f"  {ex['channel']}: {ex['preview']}")
    if report.recall_review:
        lines.append("")
        lines.append("Recall review sample (structural candidate missed):")
        for ex in report.recall_review:
            lines.append(
                f"  [{ex['classified']}] {ex['channel']}: {ex['preview']}",
            )
    return "\n".join(lines)


def render_classify_audit(
    report: ClassifyAuditReport,
    *,
    suspicious_class: str | None = None,
) -> str:
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
    _render_suspicious("Suspicious TRADE_UPDATE examples:", report.suspicious_trade_update)
    _render_suspicious("Suspicious TECHNICAL_LEVELS examples:", report.suspicious_technical_levels)

    if suspicious_class:
        key = _SUSPICIOUS_CLASS_MAP.get(suspicious_class.upper())
        if key:
            items = getattr(report, key, [])
            lines.append("")
            lines.append(f"Filtered suspicious audit for {suspicious_class.upper()}:")
            lines.append(f"  count: {len(items)}")
            for ex in items:
                lines.append(f"  {ex['channel']}: {ex['preview']}")

    return "\n".join(lines)
