"""Stage 3 research content taxonomy — separate from production signal taxonomy.

Does NOT modify bot.research.futures.taxonomy or parser_v2 gate behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from bot.research.futures.parser import ENTRY_RE, SIDE_TOKEN, SL_RE, TF_RE, TP_LINE_RE


class ResearchContentType(StrEnum):
    EXPLICIT_SIGNAL = "EXPLICIT_SIGNAL"
    TRADER_THESIS = "TRADER_THESIS"
    MARKET_COMMENTARY = "MARKET_COMMENTARY"
    TECHNICAL_LEVELS = "TECHNICAL_LEVELS"
    WHALE_FLOW = "WHALE_FLOW"
    ONCHAIN_EVENT = "ONCHAIN_EVENT"
    NEWS_EVENT = "NEWS_EVENT"
    TRADE_UPDATE = "TRADE_UPDATE"
    RESULT_UPDATE = "RESULT_UPDATE"
    PROMO = "PROMO"
    OTHER = "OTHER"


RESEARCH_THESIS_ELIGIBLE = frozenset({
    ResearchContentType.EXPLICIT_SIGNAL,
    ResearchContentType.TRADER_THESIS,
    ResearchContentType.MARKET_COMMENTARY,
    ResearchContentType.TECHNICAL_LEVELS,
    ResearchContentType.WHALE_FLOW,
    ResearchContentType.ONCHAIN_EVENT,
    ResearchContentType.NEWS_EVENT,
})

_RE_PROMO = re.compile(
    r"(?i)(referral|promo(?:tion)?|discount|скидк|подпис|subscribe|"
    r"join\s+(?:us|channel|vip)|t\.me/|giveaway|розыгрыш)",
)
_RE_TP_HIT = re.compile(
    r"(?i)(tp\s*(?:hit|reached|done|взят|достиг)|take profit|"
    r"цель\s*(?:\d+|достиг|взята)|target\s*(?:hit|reached)|\+[\d.]+\s*%)",
)
_RE_SL_HIT = re.compile(
    r"(?i)(sl\s*(?:hit|triggered|выбило)|stop(?:\s*loss)?\s*(?:hit|triggered)|"
    r"стоп\s*(?:выбит|сработал))",
)
_RE_CLOSE = re.compile(
    r"(?i)(close(?:d)?\s+(?:position|trade|long|short)|position closed|"
    r"закрыли|закрыт(?:ие|a)?\s+поз|manual close|early exit)",
)
_RE_TRADE_UPDATE = re.compile(
    r"(?i)(update|обновлен|перенос\s+sl|move sl|sl to breakeven|"
    r"breakeven|безубыт|добавил|add(?:ed)? to|partial|частич)",
)
_RE_WHALE_FLOW = re.compile(
    r"\b(?:whale|mega\s+whale|smart\s+money)\b.*?"
    r"(?:opened|closed|bought|sold|deposited|withdrew|transferred|borrowed|long|short)|"
    r"(?:opened|closed|bought|sold|deposited|withdrew|borrowed)\b.*?"
    r"\b(?:whale|wallet|address|0x[a-f0-9]{6,})\b|"
    r"\b0x[a-f0-9]{8,}\b.*?(?:bought|sold|deposited|withdrew|transferred|borrowed)|"
    r"\bwallet\b.*?(?:opened|closed|long|short)|"
    r"\b(?:trader|investor|fund|entity)\b.{0,40}\b(?:opened|closed)\b.{0,20}"
    r"(?:long|short|position)",
    re.IGNORECASE,
)
_RE_ONCHAIN = re.compile(
    r"(?i)(on[- ]?chain|blockchain|staking|unstake|token\s+unlock|unlock\s+schedule|"
    r"minted|burned|bridge[d]?|borrowed\s+\w+\s+to\s+sell|depegged|liquidat(?:ed|ion)|"
    r"flash\s+loan|defi\s+protocol)",
)
_RE_NEWS = re.compile(
    r"(?i)(breaking|just in|listed on|delist(?:ed|ing)?|hack(?:ed)?|exploit|"
    r"etf approv|sec\s+(?:sues|approves|files)|regulat(?:ion|ory)|lawsuit|"
    r"partnership|acquisition|merger|airdrop|token\s+launch|mainnet\s+launch|"
    r"новост|листинг|взлом|суд|регулятор)",
)
_RE_AUTHOR_INTENT = re.compile(
    r"(?:^|\n)\s*(?:i['']?m\s+|i am |we['']?re\s+|we are |my\s+(?:entry|trade|position)|"
    r"our\s+(?:entry|trade|position)|long\s+from|short\s+from|"
    r"going\s+long|going\s+short|taking\s+long|taking\s+short)|"
    r"\b(?:entry|enter|вход)\s*[:@]\s*\d|"
    r"\b(?:sl|stop|стоп)\s*[:@]\s*\d|"
    r"\b(?:tp\d*|targets?|цел[ьи])\s*[:@]",
    re.IGNORECASE | re.MULTILINE,
)
_RE_THESIS_LANGUAGE = re.compile(
    r"(?i)(expect(?:ing)?|anticipat(?:e|ing)|waiting\s+for|looking\s+for|"
    r"bias\s+(?:remains|is)|priority\s+remains|continuation|breakout|breakdown|"
    r"retest|reclaim|invalidat(?:e|ion)|if\s+.+\s+then|interested\s+(?:after|in)|"
    r"weaker\s+than|stronger\s+than|outlook|scenario|прогноз|ожида)",
)
_RE_TECH_LEVELS = re.compile(
    r"(?i)\b(?:support|resistance|liquidity\s+zone|supply\s+zone|demand\s+zone|"
    r"breakout\s+level|consolidation|range\s+bound|key\s+level|"
    r"поддержк|сопротивлен)",
)
_RE_COMMENTARY = re.compile(
    r"(?i)(market\s+(?:update|outlook)|рынок|btc\s+(?:is|at|holds)|"
    r"dominance|altseason|macro|fed\s|cpi\s|fomc|correlat|коррел)",
)
_RE_HAS_LEVELS = re.compile(
    r"(?i)(?:entry|enter|вход|sl|stop|стоп|tp|target|цел)\s*[:@]?\s*\d",
)
_RE_THIRD_PARTY_OBSERVED = re.compile(
    r"(?i)\b(?:whale|0x[a-f0-9]{8,}|wallet\s+0x|address\s+0x|"
    r"(?:trader|investor|fund)\s+\w+\s+opened|someone\s+opened)\b",
)
_SIDE_RE = re.compile(rf"\b({SIDE_TOKEN})\b", re.IGNORECASE)


@dataclass
class ResearchClassification:
    content_type: ResearchContentType
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
    suspicious_explicit: bool = False


def _has_trade_levels(text: str) -> bool:
    return bool(
        ENTRY_RE.search(text)
        or SL_RE.search(text)
        or TP_LINE_RE.search(text)
    )


def _has_author_intent(text: str) -> bool:
    return bool(_RE_AUTHOR_INTENT.search(text))


def _is_third_party_observed(text: str) -> bool:
    if _RE_THIRD_PARTY_OBSERVED.search(text):
        return True
    if _RE_WHALE_FLOW.search(text) and not _has_author_intent(text):
        return True
    return False


def classify_research_content(text: str) -> ResearchClassification:
    """Classify message for Stage 3 research ingestion."""
    if not text or not text.strip():
        return ResearchClassification(ResearchContentType.OTHER, ["empty"], 0.0)

    t = text.strip()
    reasons: list[str] = []

    if _RE_PROMO.search(t):
        return ResearchClassification(ResearchContentType.PROMO, ["promo"], 0.92)

    if _RE_TP_HIT.search(t) or _RE_SL_HIT.search(t) or _RE_CLOSE.search(t):
        return ResearchClassification(ResearchContentType.RESULT_UPDATE, ["result"], 0.88)

    if _RE_TRADE_UPDATE.search(t):
        return ResearchClassification(ResearchContentType.TRADE_UPDATE, ["update"], 0.78)

    if _RE_ONCHAIN.search(t) and not _has_author_intent(t):
        return ResearchClassification(ResearchContentType.ONCHAIN_EVENT, ["onchain"], 0.86)

    if _RE_WHALE_FLOW.search(t) and not _has_author_intent(t):
        return ResearchClassification(
            ResearchContentType.WHALE_FLOW,
            ["whale_flow_third_party"],
            0.84,
        )

    if _RE_NEWS.search(t):
        return ResearchClassification(ResearchContentType.NEWS_EVENT, ["news"], 0.82)

    has_levels = _has_trade_levels(t)
    author = _has_author_intent(t)
    third_party = _is_third_party_observed(t)

    if author and has_levels and not third_party:
        return ResearchClassification(
            ResearchContentType.EXPLICIT_SIGNAL,
            ["author_intent", "trade_levels"],
            0.90,
        )

    if third_party and has_levels:
        return ResearchClassification(
            ResearchContentType.WHALE_FLOW,
            ["third_party_with_levels"],
            0.80,
            suspicious_explicit=True,
        )

    if _RE_THESIS_LANGUAGE.search(t):
        reasons.append("thesis_language")
        return ResearchClassification(
            ResearchContentType.TRADER_THESIS,
            reasons,
            0.76,
        )

    if _RE_TECH_LEVELS.search(t) and not _SIDE_RE.search(t[:500]):
        return ResearchClassification(
            ResearchContentType.TECHNICAL_LEVELS,
            ["technical_levels"],
            0.72,
        )

    if _RE_COMMENTARY.search(t):
        return ResearchClassification(
            ResearchContentType.MARKET_COMMENTARY,
            ["commentary"],
            0.68,
        )

    if _SIDE_RE.search(t[:400]) and has_levels and not third_party:
        return ResearchClassification(
            ResearchContentType.EXPLICIT_SIGNAL,
            ["side_and_levels"],
            0.65,
            suspicious_explicit=not author,
        )

    if _SIDE_RE.search(t[:400]):
        return ResearchClassification(
            ResearchContentType.TRADER_THESIS,
            ["side_without_full_signal"],
            0.55,
        )

    return ResearchClassification(ResearchContentType.OTHER, ["unclassified"], 0.30)
