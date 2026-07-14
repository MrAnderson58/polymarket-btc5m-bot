"""Deterministic message taxonomy for futures telegram sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from bot.research.futures.parser import ENTRY_RE, SIDE_TOKEN, SL_RE, TP_LINE_RE


class MessageType(StrEnum):
    EXPLICIT_SIGNAL = "EXPLICIT_SIGNAL"
    TRADE_UPDATE = "TRADE_UPDATE"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    POSITION_CLOSE = "POSITION_CLOSE"
    MARKET_REVIEW = "MARKET_REVIEW"
    MARKET_COMMENTARY = "MARKET_COMMENTARY"
    NEWS = "NEWS"
    PROMO = "PROMO"
    OTHER = "OTHER"


NON_SIGNAL_TYPES = frozenset({
    MessageType.MARKET_REVIEW,
    MessageType.MARKET_COMMENTARY,
    MessageType.NEWS,
    MessageType.PROMO,
    MessageType.OTHER,
})

UPDATE_TYPES = frozenset({
    MessageType.TRADE_UPDATE,
    MessageType.TP_HIT,
    MessageType.SL_HIT,
    MessageType.POSITION_CLOSE,
})

_RE_PROMO = re.compile(
    r"(?i)(referral|promo(?:tion)?|discount|скидк|подпис|subscribe|"
    r"join\s+(?:us|channel|vip)|t\.me/|bit\.ly/|giveaway|розыгрыш)",
)
_RE_NEWS = re.compile(
    r"(?i)(breaking|just in|news|новост|announcement|листинг|listed on|"
    r"sec approves|etf approv)",
)
_RE_REVIEW = re.compile(
    r"(?i)(обзор|review|analysis|разбор|outlook|forecast|прогноз|"
    r"technical analysis|тех(?:нич)?\.?\s*анализ|\bta\b|fundamental|"
    r"перспектив|outlook for|deep dive|tokenomics|roadmap)",
)
_RE_COMMENTARY = re.compile(
    r"(?i)(market (?:update|outlook)|рынок|btc (?:is|at|holds)|"
    r"correlat|коррел|dominance|altseason|macro|fed |cpi |fomc)",
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
    r"закрыли|закрыт(?:ие|a)?\s+поз|выход\s+из|фиксац|fixed profit|"
    r"manual close|early exit)",
)
_RE_UPDATE = re.compile(
    r"(?i)(update|обновлен|перенос\s+sl|move sl|sl to breakeven|"
    r"breakeven|безубыт|добавил|add(?:ed)? to|partial|частич)",
)
_RE_EXPLICIT = re.compile(
    rf"(?i)(?:^|\n)\s*(?:#?\$?\s*[A-Za-z]{{2,10}}\s+({SIDE_TOKEN})|"
    rf"({SIDE_TOKEN})\s+#?\$?\s*[A-Za-z]{{2,10}})",
    re.MULTILINE,
)
_RE_DOLLAR_SIDE = re.compile(
    rf"(?im)^\s*\$\s*[A-Za-z]{{2,10}}\s*$\n\s*({SIDE_TOKEN})\b|"
    rf"^\s*\$\s*[A-Za-z]{{2,10}}\s+({SIDE_TOKEN})\b",
)
_RE_HAS_ENTRY = ENTRY_RE
_RE_HAS_SL = SL_RE
_RE_HAS_TP = TP_LINE_RE
_RE_HAS_SIDE = re.compile(rf"(?i)\b({SIDE_TOKEN})\b")


@dataclass
class TaxonomyResult:
    message_type: MessageType
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0


def classify_message(text: str) -> TaxonomyResult:
    if not text or not text.strip():
        return TaxonomyResult(MessageType.OTHER, ["empty"], 0.0)

    t = text.strip()
    reasons: list[str] = []

    if _RE_PROMO.search(t):
        return TaxonomyResult(MessageType.PROMO, ["promo_pattern"], 0.9)

    if _RE_TP_HIT.search(t):
        return TaxonomyResult(MessageType.TP_HIT, ["tp_hit_pattern"], 0.85)
    if _RE_SL_HIT.search(t):
        return TaxonomyResult(MessageType.SL_HIT, ["sl_hit_pattern"], 0.85)
    if _RE_CLOSE.search(t):
        return TaxonomyResult(MessageType.POSITION_CLOSE, ["close_pattern"], 0.85)

    if _RE_NEWS.search(t):
        return TaxonomyResult(MessageType.NEWS, ["news_pattern"], 0.85)

    review_hit = _RE_REVIEW.search(t)
    commentary_hit = _RE_COMMENTARY.search(t)
    has_side = bool(_RE_HAS_SIDE.search(t))
    has_levels = bool(_RE_HAS_ENTRY.search(t) or _RE_HAS_SL.search(t) or _RE_HAS_TP.search(t))
    explicit_header = bool(_RE_EXPLICIT.search(t[:400]) or _RE_DOLLAR_SIDE.search(t[:400]))

    if review_hit and not (explicit_header and has_levels):
        reasons.append("review_pattern")
        return TaxonomyResult(MessageType.MARKET_REVIEW, reasons, 0.88)

    if _RE_UPDATE.search(t) and not explicit_header:
        return TaxonomyResult(MessageType.TRADE_UPDATE, ["update_pattern"], 0.75)

    if commentary_hit and not (has_side and has_levels):
        return TaxonomyResult(MessageType.MARKET_COMMENTARY, ["commentary_pattern"], 0.7)

    if explicit_header and has_side and has_levels:
        return TaxonomyResult(
            MessageType.EXPLICIT_SIGNAL,
            ["explicit_header", "side", "levels"],
            0.92,
        )

    if has_side and has_levels and not review_hit:
        return TaxonomyResult(
            MessageType.EXPLICIT_SIGNAL,
            ["side_and_levels"],
            0.8,
        )

    # Dollar ticker + direction + levels (e.g. "$SXT шорт" + Entry/TP/SL) — never OTHER.
    if has_side and has_levels and ("$" in t[:200] or "#" in t[:200]):
        return TaxonomyResult(
            MessageType.EXPLICIT_SIGNAL,
            ["dollar_ticker_side_levels"],
            0.85,
        )

    if has_side and not has_levels:
        if review_hit or len(t) > 600:
            return TaxonomyResult(MessageType.MARKET_REVIEW, ["side_in_long_review"], 0.75)
        return TaxonomyResult(MessageType.MARKET_COMMENTARY, ["side_only"], 0.55)

    if review_hit or (len(t) > 500 and not has_levels):
        return TaxonomyResult(MessageType.MARKET_REVIEW, ["long_form_no_levels"], 0.7)

    return TaxonomyResult(MessageType.OTHER, ["unclassified"], 0.3)


def is_trade_signal_type(message_type: MessageType) -> bool:
    return message_type == MessageType.EXPLICIT_SIGNAL
