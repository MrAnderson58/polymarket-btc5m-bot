"""Section-aware trade level extraction for Stage 3 thesis pipeline.

Handles Russian/English signal formats, URL stripping, and bounded field spans.
Does not modify bot.research.futures.parser (execution path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.research.futures.parser import ENTRY_RE, SL_RE, _parse_entry_range, _normalize_side

# Latin capital C + Cyrillic "топ" -> Cyrillic "Стоп"
_RE_LATIN_C_STOP = re.compile(r"\bC([\u0400-\u04FF]+)")

_RE_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_RE_TME = re.compile(r"\bt\.me/\S+", re.IGNORECASE)

_RE_SECTION_BOUNDARY = re.compile(
    r"(?i)(?:"
    r"тейк|тейки|тейк-профит|цел[ьи]|targets?|take\s*profits?|tp\d*|"
    r"стоп|stop(?:\s*loss)?|\bsl\b|"
    r"плечо|leverage|\blev\b|"
    r"марж|margin|депозит|deposit|банк|bank|бонус|bonus|referral|"
    r"выделяю|allocat"
    r")",
)

_RE_ENTRY_LABEL = re.compile(
    r"(?i)(?:"
    r"диапазон\s+входа|(?:моя\s+)?точк[аи]\s+входа|рыночный\s+вход|"
    r"вход|enter|entry|buy\s*zone|sell\s*zone|заходим"
    r")\s*[:：]?\s*(.*)$",
)

_RE_STOP_LABEL = re.compile(
    r"(?i)(?:стоп(?:\s*[-–]?\s*лосс)?|stop\s*loss|\bstop\b|\bsl\b)\s*[:：]?\s*(.*)$",
)

_RE_TARGET_LABEL = re.compile(
    r"(?i)(?:"
    r"тейк(?:и|-профит)?|цел[ьи]|targets?|take\s*profits?|"
    r"рекомендуемый\s+тейк|tp\d*"
    r")\s*[:：]?\s*(.*)$",
)

_RE_MARKET_ENTRY = re.compile(r"(?i)(?:по\s+рынку|at\s+market|market\s+entry|market\s+order)")
_RE_DEFERRED_STOP = re.compile(
    r"(?i)(?:"
    r"пока\s+не\s+ставлю|не\s+ставлю|дам\s+по\s+необходимости|"
    r"временно\s+снять\s+стоп|сниму\s+стоп(?:\s*лосс)?|"
    r"later|not\s+set|pending"
    r")",
)
_RE_NUM_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)")
_RE_NUM_SINGLE = re.compile(r"(\d+(?:\.\d+)?)")
_RE_PCT_PAREN = re.compile(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)")
_RE_LEVERAGE_INLINE = re.compile(r"\b\d+(?:\.\d+)?\s*x\b", re.IGNORECASE)
_RE_SUPPORT = re.compile(r"(?i)support\s*(?:at|zone)?\s*[:@]?\s*(\d+(?:\.\d+)?)")
_RE_RESISTANCE = re.compile(r"(?i)resistance\s*(?:at|zone)?\s*[:@]?\s*(\d+(?:\.\d+)?)")

# Allocation / margin / bank amounts — never trade targets
_RE_NOISE_AMOUNT = re.compile(
    r"(?i)(?:"
    r"\d+(?:\.\d+)?\s*%|"
    r"\$\s*\d|"
    r"\d+\s*(?:usd|usdt|btc)\b|"
    r"выделяю\s+\d"
    r")",
)


@dataclass
class ParsedSignalLevels:
    entry_low: float | None = None
    entry_high: float | None = None
    entry_status: str = "missing"  # numeric | market | missing
    stop: float | None = None
    stop_status: str = "missing"  # numeric | deferred | missing
    targets: list[float] = field(default_factory=list)
    support: list[float] = field(default_factory=list)
    resistance: list[float] = field(default_factory=list)


def normalize_signal_text(text: str) -> str:
    """Strip URLs/links and normalize Unicode before level extraction."""
    out = text.replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " ")
    out = _RE_MARKDOWN_LINK.sub(r"\1", out)
    out = _RE_URL.sub(" ", out)
    out = _RE_TME.sub(" ", out)
    out = _RE_LATIN_C_STOP.sub(r"С\1", out)
    return out


def _truncate_at_boundary(span: str) -> str:
    m = _RE_SECTION_BOUNDARY.search(span)
    if m and m.start() > 0:
        return span[: m.start()].strip()
    return span.strip()


def _parse_numeric_range(span: str) -> tuple[float | None, float | None]:
    span = _truncate_at_boundary(span)
    if _RE_MARKET_ENTRY.search(span):
        return None, None
    m = _RE_NUM_RANGE.search(span)
    if m:
        try:
            a, b = float(m.group(1)), float(m.group(2))
            return min(a, b), max(a, b)
        except ValueError:
            pass
    m = _RE_NUM_SINGLE.search(span)
    if m:
        try:
            v = float(m.group(1))
            return v, v
        except ValueError:
            pass
    return None, None


def _extract_entry_section(text: str) -> ParsedSignalLevels:
    result = ParsedSignalLevels()
    for line in text.splitlines():
        m = _RE_ENTRY_LABEL.search(line.strip())
        if not m:
            continue
        span = m.group(1).strip()
        if _RE_MARKET_ENTRY.search(span) or _RE_MARKET_ENTRY.search(line):
            result.entry_status = "market"
            return result
        lo, hi = _parse_numeric_range(span)
        if lo is not None:
            result.entry_low = lo
            result.entry_high = hi
            result.entry_status = "numeric"
            return result
    # Fallback: English entry regex on full text
    if ENTRY_RE.search(text):
        lo, hi = _parse_entry_range(text)
        if lo is not None:
            result.entry_low = lo
            result.entry_high = hi
            result.entry_status = "numeric"
    return result


def _extract_stop_section(text: str) -> tuple[float | None, str]:
    for line in text.splitlines():
        m = _RE_STOP_LABEL.search(line.strip())
        if not m:
            continue
        span = _truncate_at_boundary(m.group(1).strip())
        if _RE_DEFERRED_STOP.search(span) or _RE_DEFERRED_STOP.search(line):
            return None, "deferred"
        nums = _RE_NUM_SINGLE.findall(span)
        if nums:
            try:
                return float(nums[0]), "numeric"
            except ValueError:
                pass
    sl = SL_RE.search(text)
    if sl:
        try:
            return float(sl.group(1)), "numeric"
        except ValueError:
            pass
    if _RE_DEFERRED_STOP.search(text):
        return None, "deferred"
    return None, "missing"


def _is_plausible_target(price: float, *, entry: float | None) -> bool:
    if price <= 0:
        return False
    # URL / referral ID contamination: large integers without realistic scale
    if price >= 10_000 and entry is not None and entry < 1000:
        return False
    if price >= 100_000:
        return False
    # Common allocation percentages when entry is sub-dollar
    if entry is not None and entry < 10 and price in (25.0, 50.0, 75.0, 100.0):
        return False
    return True


def _parse_target_prices(span: str, *, entry: float | None) -> list[float]:
    span = _truncate_at_boundary(span)
    span = _RE_PCT_PAREN.sub("", span)
    span = _RE_LEVERAGE_INLINE.sub("", span)
    span = _RE_NOISE_AMOUNT.sub("", span)
    prices: list[float] = []
    seen: set[float] = set()
    for m in _RE_NUM_SINGLE.finditer(span):
        raw = m.group(1)
        # Prefer decimal trade prices; skip bare integers likely from IDs
        if "." not in raw and len(raw) >= 4:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if not _is_plausible_target(val, entry=entry):
            continue
        if val not in seen:
            seen.add(val)
            prices.append(val)
    return prices


def _lines_with_continuations(text: str) -> list[str]:
    """Expand lines where a label line is followed by value-only continuation lines."""
    raw_lines = [ln.rstrip() for ln in text.splitlines()]
    expanded: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        if not line:
            i += 1
            continue
        if _RE_TARGET_LABEL.search(line):
            m = _RE_TARGET_LABEL.search(line)
            assert m is not None
            span = m.group(1).strip()
            j = i + 1
            cont: list[str] = []
            while j < len(raw_lines):
                nxt = raw_lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if _RE_ENTRY_LABEL.search(nxt) or _RE_STOP_LABEL.search(nxt):
                    break
                if _RE_TARGET_LABEL.search(nxt):
                    break
                if _RE_SECTION_BOUNDARY.match(nxt):
                    break
                cont.append(nxt)
                j += 1
            if cont:
                line = f"{line} {' '.join(cont)}"
            expanded.append(line)
            i = j
            continue
        expanded.append(line)
        i += 1
    return expanded


def _extract_targets_section(text: str, *, entry: float | None) -> list[float]:
    targets: list[float] = []
    seen: set[float] = set()
    for line in _lines_with_continuations(text):
        m = _RE_TARGET_LABEL.search(line.strip())
        if not m:
            continue
        for price in _parse_target_prices(m.group(1), entry=entry):
            if price not in seen:
                seen.add(price)
                targets.append(price)
    if targets:
        return targets
    # Fallback: TP line regex on normalized text but section-bounded per line only
    for line in _lines_with_continuations(text):
        lm = re.search(
            r"(?i)(?:tp\d*|take\s*profit)\s*[:@]?\s*(.+)$",
            line.strip(),
        )
        if lm:
            for price in _parse_target_prices(lm.group(1), entry=entry):
                if price not in seen:
                    seen.add(price)
                    targets.append(price)
    return targets


def extract_signal_levels(text: str) -> ParsedSignalLevels:
    """Extract entry/stop/target levels with section-aware Russian/English parsing."""
    normalized = normalize_signal_text(text)
    entry_part = _extract_entry_section(normalized)
    entry_ref = entry_part.entry_low or entry_part.entry_high
    stop, stop_status = _extract_stop_section(normalized)
    targets = _extract_targets_section(normalized, entry=entry_ref)

    support: list[float] = []
    resistance: list[float] = []
    for m in _RE_SUPPORT.finditer(normalized):
        support.append(float(m.group(1)))
    for m in _RE_RESISTANCE.finditer(normalized):
        resistance.append(float(m.group(1)))

    return ParsedSignalLevels(
        entry_low=entry_part.entry_low,
        entry_high=entry_part.entry_high,
        entry_status=entry_part.entry_status,
        stop=stop,
        stop_status=stop_status,
        targets=targets,
        support=support,
        resistance=resistance,
    )


def entry_status_from_text(text: str) -> str:
    """Detect market entry from raw/normalized text when no numeric entry stored."""
    normalized = normalize_signal_text(text)
    if _RE_MARKET_ENTRY.search(normalized):
        return "market"
    if re.search(r"(?i)вход\s+по\s+рынку|рыночный\s+вход", normalized):
        return "market"
    return "missing"
