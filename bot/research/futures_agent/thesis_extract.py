"""Deterministic thesis extraction from trader posts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.research.futures.parser import (
    SIDE_TOKEN,
    TF_RE,
    _normalize_side,
)
from bot.research.futures_agent.signal_level_extract import (
    ParsedSignalLevels,
    entry_status_from_text,
    extract_levels_for_content_type,
    extract_signal_levels,
)
from bot.research.futures_agent.research_taxonomy import (
    RESEARCH_THESIS_ELIGIBLE,
    ResearchContentType,
)
from bot.research.futures_agent.research_utils import extract_symbols, is_market_wide_news

_RE_INVALIDATION = re.compile(
    r"(?i)(?:invalidat(?:e|ion)|if\s+.+\s+(?:fails?|breaks?|loses?)|"
    r"below\s+\d|above\s+\d|unless|stop\s+if|lose\s+support|lose\s+resistance)",
)
_RE_CONDITION = re.compile(
    r"(?i)(?:if\s+|when\s+|after\s+|on\s+retest|waiting\s+for|interested\s+after|"
    r"provided\s+that|once\s+)",
)
_RE_SUPPORT_LOSS = re.compile(r"(?i)(support\s+loss|lost\s+support|break(?:down)?\s+below)")
_RE_BEARISH = re.compile(
    r"(?i)(lower|short|bearish|breakdown|sell\s+off|weaker|dump|decline|drop)",
)
_RE_BULLISH = re.compile(
    r"(?i)(higher|long|bullish|breakout|reclaim|rally|stronger|pump|bounce)",
)
_SIDE_RE = re.compile(rf"\b({SIDE_TOKEN})\b", re.IGNORECASE)
_LEVEL_PRICE = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass
class ExtractedLevel:
    level_type: str
    price: float
    ordinal: int
    confidence: float


@dataclass
class ExtractedThesis:
    symbol: str | None
    direction: str
    thesis_text: str
    horizon: str | None
    condition_text: str | None
    invalidation_text: str | None
    confidence: float
    levels: list[ExtractedLevel] = field(default_factory=list)
    unresolved: bool = False


def _infer_direction(text: str, content_type: ResearchContentType) -> str:
    m = _SIDE_RE.search(text[:600])
    if m:
        return _normalize_side(m.group(1))
    if _RE_SUPPORT_LOSS.search(text) or (
        _RE_BEARISH.search(text) and not _RE_BULLISH.search(text[:200])
    ):
        return "SHORT"
    if _RE_BULLISH.search(text) and not _RE_BEARISH.search(text[:200]):
        return "LONG"
    if content_type in (ResearchContentType.NEWS_EVENT, ResearchContentType.TECHNICAL_LEVELS):
        return "NEUTRAL"
    if content_type in (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT):
        if re.search(r"(?i)borrowed\s+\w+\s+to\s+sell|sold|short|dump", text):
            return "SHORT"
        if re.search(r"(?i)bought|long|accumulat", text):
            return "LONG"
    return "NEUTRAL"


def _extract_horizon(text: str) -> str | None:
    m = TF_RE.search(text[:400])
    return m.group(1) if m else None


def _extract_condition(text: str) -> str | None:
    parts: list[str] = []
    for m in _RE_CONDITION.finditer(text):
        snippet = text[m.start(): min(len(text), m.end() + 80)].split("\n")[0].strip()
        if snippet and snippet not in parts:
            parts.append(snippet[:200])
    if _RE_SUPPORT_LOSS.search(text):
        parts.append("support loss context")
    return " | ".join(parts[:2]) if parts else None


def _extract_invalidation(text: str) -> str | None:
    m = _RE_INVALIDATION.search(text)
    if m:
        return text[m.start(): min(len(text), m.end() + 100)].split("\n")[0].strip()[:200]
    parsed = extract_signal_levels(text)
    if parsed.stop is not None:
        return f"stop at {parsed.stop}"
    return None


def _levels_from_parsed(parsed: ParsedSignalLevels) -> list[ExtractedLevel]:
    levels: list[ExtractedLevel] = []
    if parsed.entry_low is not None:
        levels.append(ExtractedLevel("ENTRY_LOW", parsed.entry_low, 0, 0.80))
    if parsed.entry_high is not None:
        hi_conf = 0.80 if parsed.entry_high != parsed.entry_low else 0.75
        levels.append(ExtractedLevel("ENTRY_HIGH", parsed.entry_high, 0, hi_conf))
    if parsed.stop is not None:
        levels.append(ExtractedLevel("STOP", parsed.stop, 0, 0.78))
    for i, price in enumerate(parsed.targets):
        levels.append(ExtractedLevel("TARGET", price, i + 1, 0.75))
    for i, price in enumerate(parsed.support):
        levels.append(ExtractedLevel("SUPPORT", price, i + 1, 0.65))
    for i, price in enumerate(parsed.resistance):
        levels.append(ExtractedLevel("RESISTANCE", price, i + 1, 0.65))
    return levels


def _extract_levels(text: str, content_type: str = "EXPLICIT_SIGNAL") -> list[ExtractedLevel]:
    return _levels_from_parsed(extract_levels_for_content_type(text, content_type))


def _thesis_summary(text: str, *, max_len: int = 280) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    summary = " ".join(lines[:3])
    return summary[:max_len]


def _confidence(
    *,
    content_type: ResearchContentType,
    direction: str,
    symbol: str | None,
    levels: list[ExtractedLevel],
    thesis_text: str,
) -> float:
    score = 0.35
    if symbol:
        score += 0.20
    if direction != "NEUTRAL":
        score += 0.15
    if levels:
        score += 0.15
    if len(thesis_text) > 40:
        score += 0.10
    if content_type == ResearchContentType.EXPLICIT_SIGNAL:
        score += 0.10
    if content_type == ResearchContentType.NEWS_EVENT and (symbol or is_market_wide_news([], thesis_text)):
        score += 0.15
    return min(1.0, score)


def extract_theses_from_post(
    raw_text: str,
    content_type: str,
    *,
    symbols: list[str] | None = None,
) -> list[ExtractedThesis]:
    """Extract 0..N theses from a classified post."""
    try:
        ctype = ResearchContentType(content_type)
    except ValueError:
        return []

    if ctype not in RESEARCH_THESIS_ELIGIBLE:
        return []

    text = raw_text.strip()
    if not text:
        return []

    syms = symbols or extract_symbols(text)
    levels = _extract_levels(text, ctype.value)
    direction = _infer_direction(text, ctype)
    horizon = _extract_horizon(text)
    condition = _extract_condition(text)
    invalidation = _extract_invalidation(text)
    summary = _thesis_summary(text)

    if ctype == ResearchContentType.NEWS_EVENT:
        sym = syms[0] if syms else None
        conf = _confidence(
            content_type=ctype,
            direction="NEUTRAL",
            symbol=sym,
            levels=levels,
            thesis_text=summary,
        )
        if conf < 0.45 and not is_market_wide_news(syms, text):
            return [ExtractedThesis(
                symbol=sym,
                direction="NEUTRAL",
                thesis_text=summary,
                horizon=horizon,
                condition_text=condition,
                invalidation_text=invalidation,
                confidence=conf,
                levels=levels,
                unresolved=True,
            )]
        return [ExtractedThesis(
            symbol=sym,
            direction="NEUTRAL",
            thesis_text=summary,
            horizon=horizon,
            condition_text=condition,
            invalidation_text=invalidation,
            confidence=conf,
            levels=levels,
        )]

    if not syms:
        conf = _confidence(
            content_type=ctype,
            direction=direction,
            symbol=None,
            levels=levels,
            thesis_text=summary,
        )
        if conf < 0.50 and ctype not in (
            ResearchContentType.MARKET_COMMENTARY,
            ResearchContentType.ONCHAIN_EVENT,
        ):
            return [ExtractedThesis(
                symbol=None,
                direction=direction,
                thesis_text=summary,
                horizon=horizon,
                condition_text=condition,
                invalidation_text=invalidation,
                confidence=conf,
                levels=levels,
                unresolved=True,
            )]
        syms = [None]  # type: ignore[list-item]

    theses: list[ExtractedThesis] = []
    target_syms = syms if syms else [None]
    for sym in target_syms[:3]:
        conf = _confidence(
            content_type=ctype,
            direction=direction,
            symbol=sym,
            levels=levels,
            thesis_text=summary,
        )
        unresolved = conf < 0.55 and not levels and direction == "NEUTRAL"
        if ctype == ResearchContentType.TRADER_THESIS and direction == "NEUTRAL" and not condition:
            unresolved = True
        theses.append(ExtractedThesis(
            symbol=sym,
            direction=direction,
            thesis_text=summary,
            horizon=horizon,
            condition_text=condition,
            invalidation_text=invalidation,
            confidence=conf,
            levels=levels,
            unresolved=unresolved,
        ))
    return theses
