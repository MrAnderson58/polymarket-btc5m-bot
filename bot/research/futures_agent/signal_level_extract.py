"""Section-aware trade level extraction for Stage 3 thesis pipeline.

Handles Russian/English signal formats, URL stripping, and bounded field spans.
Does not modify bot.research.futures.parser (execution path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.parser import ENTRY_RE, SL_RE, _parse_entry_range

# Latin capital C + Cyrillic "топ" -> Cyrillic "Стоп"
_RE_LATIN_C_STOP = re.compile(r"\bC([\u0400-\u04FF]+)")

_RE_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_URL = re.compile(
    r"(?:https?://|www\.|t\.me/|partner\.|invite/|bybit\.|bingx\.|binance\.)[^\s]*",
    re.IGNORECASE,
)
_RE_URL_PATH_ID = re.compile(r"/(\d{4,})(?:/|$|\s)")

_RE_SECTION_BOUNDARY = re.compile(
    r"(?i)(?:"
    r"тейк|тейки|тейк-профит|цел[ьи]|targets?|take\s*profits?|tp\d*|"
    r"стоп|stop(?:\s*loss)?|\bsl\b|"
    r"плечо|leverage|\blev\b|"
    r"марж|margin|депозит|deposit|банк|bank|бонус|bonus|referral|"
    r"выделяю|allocat|dominance|доминирован"
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
_RE_PCT = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_RE_PCT_PAREN = re.compile(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)")
_RE_LEVERAGE_INLINE = re.compile(r"\b\d+(?:\.\d+)?\s*x\b", re.IGNORECASE)
_RE_TIMEFRAME_NUM = re.compile(r"\b\d+(?:\.\d+)?\s*(?:m|h|d|w|min|hour|day|week)\b", re.IGNORECASE)

# Technical support/resistance — explicit context only
_RE_SUPPORT_PRICE = re.compile(
    r"(?i)(?:"
    r"(?:support|поддержк\w*)\s*(?:at|@|zone|level|у)?\s*[:：]?\s*(\d+(?:\.\d+)?)|"
    r"(?:at|@|near|above|below)\s+support\s*[:：]?\s*(\d+(?:\.\d+)?)|"
    r"(\d+(?:\.\d+)?)\s+(?:support|поддержк)|"
    r"у\s+поддержк\w*\s+(\d+(?:\.\d+)?)|"
    r"цен\w+\s+у\s+поддержк\w*\s+(\d+(?:\.\d+)?)|"
    r"поддержк\w*\s+(?:на|в|у)\s+(\d+(?:\.\d+)?)"
    r")",
)
_RE_RESISTANCE_PRICE = re.compile(
    r"(?i)(?:"
    r"(?:resistance|сопротивлен\w*)\s*(?:at|@|zone|level)?\s*[:：]?\s*(\d+(?:\.\d+)?)|"
    r"(?:at|@|near|above|below)\s+resistance\s*[:：]?\s*(\d+(?:\.\d+)?)|"
    r"(\d+(?:\.\d+)?)\s+(?:resistance|сопротивлен)|"
    r"сопротивлен\w*\s+(?:на|в|у)\s+(\d+(?:\.\d+)?)"
    r")",
)

_RE_NOISE_AMOUNT = re.compile(
    r"(?i)(?:"
    r"\$\s*\d|"
    r"\d+\s*(?:usd|usdt|btc)\b|"
    r"выделяю\s+\d|"
    r"dominance|доминирован"
    r")",
)

_ALLOCATION_PCTS = frozenset({25.0, 50.0, 75.0, 100.0})


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


def extract_url_number_blacklist(text: str) -> set[float]:
    """Numbers appearing in URLs/referral paths — never valid trade targets."""
    blacklist: set[float] = set()
    for m in _RE_URL.finditer(text):
        for nm in _RE_NUM_SINGLE.finditer(m.group(0)):
            try:
                blacklist.add(float(nm.group(1)))
            except ValueError:
                pass
    for m in _RE_URL_PATH_ID.finditer(text):
        try:
            blacklist.add(float(m.group(1)))
        except ValueError:
            pass
    return blacklist


def is_contaminated_target(
    price: float,
    *,
    entry: float | None,
    url_blacklist: set[float],
    raw_text: str,
) -> str | None:
    """Return contamination reason code or None if clean."""
    if price in url_blacklist:
        return "url_number_contamination"
    if price >= 1_000_000:
        return "url_number_contamination"
    if price >= 10_000 and entry is not None and entry < 1000:
        return "url_number_contamination"
    if price in _ALLOCATION_PCTS:
        return "percentage_contamination"
    if entry is not None and entry < 10 and price in _ALLOCATION_PCTS:
        return "percentage_contamination"
    if entry is not None and entry > 0:
        ratio = abs(price - entry) / entry
        if ratio > 5.0:
            return "suspicious_target_contamination"
        if entry < 10 and price == round(price) and price in _ALLOCATION_PCTS:
            return "percentage_contamination"
    # Bare round integers without decimals in sub-$10 context (allocation leak)
    if entry is not None and entry < 10 and price == int(price) and price <= 100:
        if _RE_PCT.search(raw_text) or _RE_PCT_PAREN.search(raw_text):
            return "percentage_contamination"
    return None


def normalize_signal_text(text: str) -> str:
    """Strip URLs/links and normalize Unicode before level extraction."""
    out = text.replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " ")
    out = _RE_MARKDOWN_LINK.sub(r"\1", out)
    out = _RE_URL.sub(" ", out)
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


def _clean_target_span(span: str) -> str:
    span = _truncate_at_boundary(span)
    span = _RE_PCT_PAREN.sub("", span)
    span = _RE_PCT.sub("", span)
    span = _RE_LEVERAGE_INLINE.sub("", span)
    span = _RE_TIMEFRAME_NUM.sub("", span)
    span = _RE_NOISE_AMOUNT.sub("", span)
    return span


def _is_plausible_target(
    price: float,
    *,
    entry: float | None,
    url_blacklist: set[float],
    raw_text: str,
) -> bool:
    return is_contaminated_target(
        price, entry=entry, url_blacklist=url_blacklist, raw_text=raw_text,
    ) is None


def _parse_target_prices(
    span: str,
    *,
    entry: float | None,
    url_blacklist: set[float],
    raw_text: str,
) -> list[float]:
    span = _clean_target_span(span)
    prices: list[float] = []
    seen: set[float] = set()
    for m in _RE_NUM_SINGLE.finditer(span):
        raw = m.group(1)
        # Skip bare long integers (URL/referral IDs) unless BTC-scale price
        if "." not in raw:
            try:
                ival = int(raw)
            except ValueError:
                continue
            if ival >= 10_000 and entry is not None and entry < 1000:
                continue
            if len(raw) >= 5 and entry is not None and entry < 1000:
                continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if not _is_plausible_target(
            val, entry=entry, url_blacklist=url_blacklist, raw_text=raw_text,
        ):
            continue
        if val not in seen:
            seen.add(val)
            prices.append(val)
    return prices


def _lines_with_continuations(text: str) -> list[str]:
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


def _extract_targets_section(
    text: str,
    *,
    entry: float | None,
    url_blacklist: set[float],
    raw_text: str,
) -> list[float]:
    targets: list[float] = []
    seen: set[float] = set()
    for line in _lines_with_continuations(text):
        m = _RE_TARGET_LABEL.search(line.strip())
        if not m:
            continue
        for price in _parse_target_prices(
            m.group(1), entry=entry, url_blacklist=url_blacklist, raw_text=raw_text,
        ):
            if price not in seen:
                seen.add(price)
                targets.append(price)
    if targets:
        return targets
    for line in _lines_with_continuations(text):
        lm = re.search(
            r"(?i)(?:tp\d*|take\s*profit)\s*[:@]?\s*(.+)$",
            line.strip(),
        )
        if lm:
            for price in _parse_target_prices(
                lm.group(1),
                entry=entry,
                url_blacklist=url_blacklist,
                raw_text=raw_text,
            ):
                if price not in seen:
                    seen.add(price)
                    targets.append(price)
    return targets


def _first_group_price(match: re.Match[str]) -> float | None:
    for g in match.groups():
        if g:
            try:
                return float(g)
            except ValueError:
                pass
    return None


def extract_technical_levels(text: str) -> ParsedSignalLevels:
    """Extract SUPPORT/RESISTANCE only — no trade targets from prose."""
    normalized = normalize_signal_text(text)
    result = ParsedSignalLevels()
    seen_s: set[float] = set()
    seen_r: set[float] = set()

    for m in _RE_SUPPORT_PRICE.finditer(normalized):
        price = _first_group_price(m)
        if price is not None and price not in seen_s:
            seen_s.add(price)
            result.support.append(price)

    for m in _RE_RESISTANCE_PRICE.finditer(normalized):
        price = _first_group_price(m)
        if price is not None and price not in seen_r:
            seen_r.add(price)
            result.resistance.append(price)

    return result


def extract_signal_levels(text: str) -> ParsedSignalLevels:
    """Extract entry/stop/target levels for explicit trade signals."""
    raw_text = text
    url_blacklist = extract_url_number_blacklist(raw_text)
    normalized = normalize_signal_text(raw_text)
    entry_part = _extract_entry_section(normalized)
    entry_ref = entry_part.entry_low or entry_part.entry_high
    stop, stop_status = _extract_stop_section(normalized)
    targets = _extract_targets_section(
        normalized,
        entry=entry_ref,
        url_blacklist=url_blacklist,
        raw_text=raw_text,
    )

    return ParsedSignalLevels(
        entry_low=entry_part.entry_low,
        entry_high=entry_part.entry_high,
        entry_status=entry_part.entry_status,
        stop=stop,
        stop_status=stop_status,
        targets=targets,
        support=[],
        resistance=[],
    )


def extract_levels_for_content_type(text: str, content_type: str) -> ParsedSignalLevels:
    """Route extraction by content type."""
    if content_type == "TECHNICAL_LEVELS":
        return extract_technical_levels(text)
    return extract_signal_levels(text)


def entry_status_from_text(text: str) -> str:
    normalized = normalize_signal_text(text)
    if _RE_MARKET_ENTRY.search(normalized):
        return "market"
    if re.search(r"(?i)вход\s+по\s+рынку|рыночный\s+вход", normalized):
        return "market"
    return "missing"
