"""Audit-only explicit signal format detector — independent from taxonomy classifier.

Uses its own regex vocabulary so corpus audits are not circular with
classify_research_content() rules.
"""

from __future__ import annotations

import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bot.research.futures_agent.research_ingest import iter_source_messages
from bot.research.futures_agent.research_taxonomy import (
    ResearchContentType,
    classify_research_content,
)
from bot.research.futures_agent.source_requirements import open_stage3_source_reader

_RE_URL = re.compile(r"(?i)https?://\S+")

# --- Independent audit vocabulary (not shared with taxonomy) ---

_RE_AUDIT_SYMBOL = re.compile(
    r"(?im)(?:"
    r"^([a-z]{2,12})\s+(?:long|short|лонг|шорт)\b|"
    r"^#?([A-Z]{2,12})\s+(?:LONG|SHORT|лонг|шорт)\b|"
    r"\$([A-Z]{2,12})\b|"
    r"#([A-Z]{2,12})\b|"
    r"\b([A-Z]{2,12})(?:USDT|/USDT)\b|"
    r"(?:сетап|setup)\s+([A-Z]{2,12})(?:/USDT)?\b|"
    r"^([A-Z]{2,12})\s+LONG\b"
    r")",
)

_RE_AUDIT_DIRECTION = re.compile(
    r"(?i)\b(long|short|лонг|шорт)\b",
)

_RE_AUDIT_ENTRY = re.compile(
    r"(?i)(?:"
    r"(?:^|\n)\s*(?:вход|entry|enter|точка\s+входа|диапазон\s+входа)\s*[:：]?\s*\d|"
    r"вход\s+по\s+рынку|"
    r"рыночн(?:ый|ого)\s+вход\s*[:：]?\s*\d|"
    r"по\s+рынку|"
    r"market\s+entry\s*[:：]?\s*\d|"
    r"entry\s*[:：]?\s*\d"
    r")",
)

_RE_AUDIT_TARGETS = re.compile(
    r"(?i)(?:"
    r"(?:тейк(?:и|-профит)?|tp\d*|target|цел[ьи])\s*[:：]?\s*[\d.,\s]+|"
    r"(?:^|\n)\s*цели\s+[\d.,\s]+"
    r")",
)

_RE_AUDIT_NUMERIC_STOP = re.compile(
    r"(?i)(?:"
    r"(?:стоп(?:-лосс)?|sl|stop)\s*[:：]?\s*\d|"
    r"(?:^|\n)\s*стоп\s+\d"
    r")",
)

_RE_AUDIT_DEFERRED_STOP = re.compile(
    r"(?i)(?:"
    r"стоп\s*[:：]?\s*(?:пока\s+не\s+ставлю|не\s+ставлю|later|позже)|"
    r"stop\s*[:：]?\s*(?:later|not\s+set|pending)"
    r")",
)

_AUDIT_TICKER_STOP = frozenset({
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAD", "HER",
    "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW",
    "OLD", "SEE", "WAY", "WHO", "DID", "GET", "LET", "PUT", "SAY", "SHE", "TOO",
    "USE", "WHY", "YES", "YET", "ANY", "DAY", "FEW", "MAN", "MEN", "RUN", "SET",
    "TRY", "ASK", "OWN", "OFF", "PER", "TOP", "VIA", "WAR", "WIN", "WON", "LONG",
    "SHORT", "BUY", "SELL",
})


class SignalFormatTier(StrEnum):
    FULL_SIGNAL = "FULL_SIGNAL"
    DEFERRED_STOP_SIGNAL = "DEFERRED_STOP_SIGNAL"
    PARTIAL_SIGNAL = "PARTIAL_SIGNAL"
    NONE = "NONE"


@dataclass
class SignalFormatParse:
    symbol: str | None = None
    direction: str | None = None
    has_entry: bool = False
    has_numeric_targets: bool = False
    has_numeric_stop: bool = False
    has_deferred_stop: bool = False
    tier: SignalFormatTier = SignalFormatTier.NONE
    format_pattern: str | None = None


@dataclass
class SignalFormatAuditReport:
    scanned: int = 0
    channel: str | None = None
    full_signal: int = 0
    deferred_stop_signal: int = 0
    partial_signal: int = 0
    classified_explicit: int = 0
    overlap_full: int = 0
    overlap_deferred: int = 0
    overlap_partial: int = 0
    symbol_ok: int = 0
    direction_ok: int = 0
    entry_ok: int = 0
    target_ok: int = 0
    stop_numeric_ok: int = 0
    deferred_stop_count: int = 0
    format_patterns: Counter = field(default_factory=Counter)
    missed_full: list[dict] = field(default_factory=list)
    missed_deferred: list[dict] = field(default_factory=list)


def _normalize_audit_symbol(raw: str) -> str | None:
    token = raw.upper().lstrip("#$")
    token = token.replace("/USDT", "").replace("USDT", "")
    if len(token) < 2 or len(token) > 12:
        return None
    if token in _AUDIT_TICKER_STOP:
        return None
    if not token.isalpha():
        return None
    return token


def _audit_extract_symbol(text: str) -> str | None:
    for m in _RE_AUDIT_SYMBOL.finditer(text[:600]):
        for g in m.groups():
            if g:
                sym = _normalize_audit_symbol(g)
                if sym:
                    return sym
    return None


def _audit_infer_direction(text: str) -> str | None:
    m = _RE_AUDIT_DIRECTION.search(text[:500])
    if not m:
        return None
    s = m.group(1).lower()
    if s in ("long", "лонг"):
        return "LONG"
    if s in ("short", "шорт"):
        return "SHORT"
    return None


def _audit_format_pattern(parse: SignalFormatParse) -> str:
    parts: list[str] = []
    if parse.symbol:
        parts.append("sym")
    if parse.direction:
        parts.append(parse.direction.lower())
    if parse.has_entry:
        parts.append("entry")
    if parse.has_numeric_targets:
        parts.append("targets")
    if parse.has_numeric_stop:
        parts.append("stop")
    elif parse.has_deferred_stop:
        parts.append("stop_deferred")
    return "+".join(parts) if parts else "none"


def parse_signal_format_audit(text: str) -> SignalFormatParse:
    """Independent audit parser — does not call taxonomy."""
    if not text or not text.strip():
        return SignalFormatParse()

    t = _RE_URL.sub(" ", text.strip())
    out = SignalFormatParse(
        symbol=_audit_extract_symbol(t),
        direction=_audit_infer_direction(t),
        has_entry=bool(_RE_AUDIT_ENTRY.search(t)),
        has_numeric_targets=bool(_RE_AUDIT_TARGETS.search(t)),
        has_numeric_stop=bool(_RE_AUDIT_NUMERIC_STOP.search(t)),
        has_deferred_stop=bool(_RE_AUDIT_DEFERRED_STOP.search(t)),
    )

    if not out.symbol or out.direction not in ("LONG", "SHORT"):
        out.tier = SignalFormatTier.NONE
        out.format_pattern = _audit_format_pattern(out)
        return out

    components = sum([
        out.has_entry,
        out.has_numeric_targets,
        out.has_numeric_stop or out.has_deferred_stop,
    ])

    if out.has_entry and out.has_numeric_targets and out.has_numeric_stop:
        out.tier = SignalFormatTier.FULL_SIGNAL
    elif out.has_entry and out.has_numeric_targets and out.has_deferred_stop:
        out.tier = SignalFormatTier.DEFERRED_STOP_SIGNAL
    elif components >= 2:
        out.tier = SignalFormatTier.PARTIAL_SIGNAL
    else:
        out.tier = SignalFormatTier.NONE

    out.format_pattern = _audit_format_pattern(out)
    return out


def is_audit_signal_candidate(text: str) -> bool:
    return parse_signal_format_audit(text).tier != SignalFormatTier.NONE


def run_signal_format_audit(
    *,
    channel: str | None = "signalyp",
    limit: int | None = None,
    progress_every: int = 500,
) -> SignalFormatAuditReport:
    reader = open_stage3_source_reader()
    report = SignalFormatAuditReport(channel=channel)
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
            cls = classify_research_content(text)
            is_explicit = cls.content_type == ResearchContentType.EXPLICIT_SIGNAL

            if parsed.symbol:
                report.symbol_ok += 1
            if parsed.direction:
                report.direction_ok += 1
            if parsed.has_entry:
                report.entry_ok += 1
            if parsed.has_numeric_targets:
                report.target_ok += 1
            if parsed.has_numeric_stop:
                report.stop_numeric_ok += 1
            if parsed.has_deferred_stop:
                report.deferred_stop_count += 1

            if parsed.format_pattern:
                report.format_patterns[parsed.format_pattern] += 1

            if parsed.tier == SignalFormatTier.FULL_SIGNAL:
                report.full_signal += 1
                if is_explicit:
                    report.overlap_full += 1
                elif len(report.missed_full) < 20:
                    report.missed_full.append({
                        "channel": row["channel_name"],
                        "classified": cls.content_type.value,
                        "preview": text[:240].replace("\n", " "),
                    })
            elif parsed.tier == SignalFormatTier.DEFERRED_STOP_SIGNAL:
                report.deferred_stop_signal += 1
                if is_explicit:
                    report.overlap_deferred += 1
                elif len(report.missed_deferred) < 20:
                    report.missed_deferred.append({
                        "channel": row["channel_name"],
                        "classified": cls.content_type.value,
                        "preview": text[:240].replace("\n", " "),
                    })
            elif parsed.tier == SignalFormatTier.PARTIAL_SIGNAL:
                report.partial_signal += 1
                if is_explicit:
                    report.overlap_partial += 1

            if is_explicit:
                report.classified_explicit += 1

            if progress_every > 0 and i % progress_every == 0:
                elapsed = max(time.monotonic() - t0, 0.001)
                rate = i / elapsed
                print(
                    f"Processed: {i:,} | FULL={report.full_signal:,} "
                    f"DEFERRED={report.deferred_stop_signal:,} "
                    f"PARTIAL={report.partial_signal:,} "
                    f"explicit={report.classified_explicit:,} | {rate:.0f} msg/s",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        reader.close()
    return report


def render_signal_format_audit(report: SignalFormatAuditReport) -> str:
    n = max(report.scanned, 1)
    lines = [
        "SIGNALYP FORMAT AUDIT (independent detector vs classifier)",
        f"channel: {report.channel or 'all'}",
        f"total scanned: {report.scanned:,}",
        "",
        "Audit candidate tiers:",
        f"  FULL_SIGNAL:           {report.full_signal:,}",
        f"  DEFERRED_STOP_SIGNAL:  {report.deferred_stop_signal:,}",
        f"  PARTIAL_SIGNAL:        {report.partial_signal:,}",
        f"  classifier EXPLICIT_SIGNAL: {report.classified_explicit:,}",
        "",
        "Classifier overlap by tier:",
        f"  FULL_SIGNAL overlap:    {report.overlap_full:,} / {report.full_signal:,}"
        f" ({report.overlap_full / max(report.full_signal, 1):.1%})",
        f"  DEFERRED_STOP overlap:  {report.overlap_deferred:,} / {report.deferred_stop_signal:,}"
        f" ({report.overlap_deferred / max(report.deferred_stop_signal, 1):.1%})",
        f"  PARTIAL overlap:        {report.overlap_partial:,} / {report.partial_signal:,}"
        f" ({report.overlap_partial / max(report.partial_signal, 1):.1%})",
        "",
        "Extraction success rates (audit detector):",
        f"  symbol:    {report.symbol_ok / n:.1%}",
        f"  direction: {report.direction_ok / n:.1%}",
        f"  entry:     {report.entry_ok / n:.1%}",
        f"  targets:   {report.target_ok / n:.1%}",
        f"  stop (numeric): {report.stop_numeric_ok / n:.1%}",
        f"  deferred stop phrases: {report.deferred_stop_count:,}",
        "",
        "Top format patterns:",
    ]
    for pat, cnt in report.format_patterns.most_common(15):
        lines.append(f"  {pat}: {cnt}")

    if report.missed_full:
        lines.append("")
        lines.append("Missed FULL_SIGNAL examples (audit tier, not EXPLICIT_SIGNAL):")
        for ex in report.missed_full[:20]:
            lines.append(f"  [{ex['classified']}] {ex['preview']}")

    if report.missed_deferred:
        lines.append("")
        lines.append("Missed DEFERRED_STOP_SIGNAL examples (audit tier, not EXPLICIT_SIGNAL):")
        for ex in report.missed_deferred[:20]:
            lines.append(f"  [{ex['classified']}] {ex['preview']}")

    return "\n".join(lines)
