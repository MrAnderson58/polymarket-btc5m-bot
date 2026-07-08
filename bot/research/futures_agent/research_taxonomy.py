"""Stage 3 research content taxonomy — separate from production signal taxonomy.

Does NOT modify bot.research.futures.taxonomy or parser_v2 gate behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from bot.research.futures.parser import ENTRY_RE, SIDE_TOKEN, SL_RE, TF_RE, TP_LINE_RE, TP_RE
from bot.research.futures_agent.research_utils import extract_symbols


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
    r"(?i)(close(?:d)?\s+(?:the\s+)?(?:my\s+)?(?:position|trade|long|short)|"
    r"close\s+my\s+(?:long|short)|position closed|"
    r"закрыли|закрыт(?:ие|a)?\s+поз|manual close|early exit)",
)
_RE_POSITION_MGMT = re.compile(
    r"(?i)(?:"
    r"my\s+(?:long|short|position|trade)\b|"
    r"(?:waiting|wait)\s+(?:to\s+)?close\s+(?:my\s+)?(?:long|short|position)|"
    r"close\s+my\s+(?:long|short|position)|"
    r"exited?\s+(?:my\s+)?(?:long|short|position)|"
    r"took\s+(?:partial\s+)?profit|take\s+profit\s+on\s+my|"
    r"reduce[d]?\s+(?:my\s+)?(?:long|short|position)|"
    r"partial\s+(?:close|exit|profit)|"
    r"перенос\s+sl|move\s+sl|sl\s+to\s+breakeven|"
    r"breakeven|безубыт|"
    r"add(?:ed)?\s+to\s+(?:my\s+)?(?:long|short|position)|"
    r"adjust(?:ed)?\s+(?:my\s+)?(?:sl|stop|tp|target)|"
    r"частич"
    r")",
)
_RE_NON_TRADE_UPDATE = re.compile(
    r"(?i)(?:"
    r"files?\s+updated|updated\s+s-?1|application\s+for\s+spot|"
    r"price\s+updates?|market\s+update|news\s+update|"
    r"\betf\b|sec\s+files|filing\s+for|"
    r"bitcoin\s+.{0,40}price\s+update"
    r")",
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
    r"(?i)(breaking|just in|listed on|delist(?:ed|ing)?|\bhack(?:ed)?\b(?!ers?)|exploit|"
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
_RE_TECH_LEVELS_OTHER = re.compile(
    r"(?i)\b(?:liquidity\s+zone|supply\s+zone|demand\s+zone|"
    r"breakout\s+level|consolidation|range\s+bound|key\s+level|"
    r"поддержк|сопротивлен)",
)
_RE_TECH_SUPPORT = re.compile(
    r"(?i)(?:"
    r"support\s+(?:loss|break|hold|zone|level|retest|reclaim|flip|at|@|around|near)\b|"
    r"loss\s+of\s+support|"
    r"support\s+(?:and|/)\s*resistance|"
    r"\b(?:at|on|near|above|below)\s+support\b|"
    r"\d+(?:\.\d+)?\s+support\b|"
    r"support\s+@\s*\d|"
    r"support\s+level"
    r")",
)
_RE_TECH_RESISTANCE = re.compile(
    r"(?i)(?:"
    r"resistance\s+(?:zone|level|break|hold|at|@|around|near)\b|"
    r"\b(?:at|on|near|above|below)\s+resistance\b|"
    r"\d+(?:\.\d+)?\s+resistance\b|"
    r"resistance\s+level"
    r")",
)
_RE_COMMENTARY = re.compile(
    r"(?i)(market\s+(?:update|outlook)|рынок|btc\s+(?:is|at|holds)|"
    r"dominance|altseason|macro|fed\s|cpi\s|fomc|correlat|коррел)",
)
_RE_QUESTION_OR_META = re.compile(
    r"(?i)(\?|how to\b|training\b|learn\b|tutorial\b|course\b|guide\b|"
    r"what do you think|should i\b|кто\s+знает|как\s+шортить|как\s+лонговать|"
    r"^is\s+\w+|^are\s+\w+\s+buy)",
)
_RE_HAS_LEVELS = re.compile(
    r"(?i)(?:entry|enter|вход|sl|stop|стоп|tp|target|цел)\s*[:@]?\s*\d",
)

_RE_PLUS_PCT = re.compile(r"(?i)\b(?:\+|plus)\s*\d+(?:\.\d+)?\s*%")

_RE_URL = re.compile(r"(?i)https?://\S+")

_RE_WHALE_ECON_ACTION = re.compile(
    r"(?i)\b(?:opened|closed|bought|sold|deposited|withdrew|transferred|borrowed|repaid|opened|liquidat(?:ed|ion))\b",
)

_RE_MARKET_ENTRY_LANGUAGE = re.compile(
    r"(?i)\b(?:entry|enter|вход|market\s+(?:buy|sell)|buy\s+at|sell\s+at)\b",
)
_RE_BEARISH_THESIS = re.compile(
    r"(?i)\b(?:lower|bearish|drop|dump|fall|decline|sell.?off|continuation\s+lower)\b",
)
_RE_BULLISH_THESIS = re.compile(
    r"(?i)\b(?:higher|bullish|rise|rally|pump|continuation\s+higher)\b",
)
_BARE_TICKER_LINE_RE = re.compile(r"(?im)^([A-Z]{2,10})\b")
_TICKER_STOPWORDS = frozenset({
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HAD", "HER",
    "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW", "NOW",
    "OLD", "SEE", "WAY", "WHO", "DID", "GET", "LET", "PUT", "SAY", "SHE", "TOO",
    "USE", "WHY", "YES", "YET", "ANY", "DAY", "FEW", "MAN", "MEN", "RUN", "SET",
    "TRY", "ASK", "OWN", "OFF", "PER", "TOP", "VIA", "WAR", "WIN", "WON",
})
_RE_THIRD_PARTY_OBSERVED = re.compile(
    r"(?i)\b(?:whale|0x[a-f0-9]{8,}|wallet\s+0x|address\s+0x|"
    r"(?:trader|investor|fund)\s+\w+\s+opened|someone\s+opened|"
    r"fresh wallet|receive[s]?\s+\d|\bwallet\b)\b",
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


def _has_technical_levels_context(text: str) -> bool:
    """Support/resistance only when numeric or explicit technical-market context."""
    if _RE_TECH_LEVELS_OTHER.search(text):
        return True
    if _RE_TECH_SUPPORT.search(text) or _RE_TECH_RESISTANCE.search(text):
        return True
    return False


def _infer_direction(text: str) -> str | None:
    m = _SIDE_RE.search(text[:400])
    if not m:
        return None
    s = m.group(1).lower()
    if s in ("long", "buy", "лонг"):
        return "LONG"
    if s in ("short", "sell", "шорт"):
        return "SHORT"
    return None


def _thesis_symbols(text: str) -> list[str]:
    syms = extract_symbols(text)
    if syms:
        return syms
    m = _BARE_TICKER_LINE_RE.search(text)
    if m:
        token = m.group(1).upper()
        if len(token) >= 3 and token not in _TICKER_STOPWORDS:
            from bot.research.futures_agent.research_utils import normalize_symbol
            sym = normalize_symbol(token)
            if sym:
                return [sym]
    return []


def _infer_thesis_direction(text: str) -> str | None:
    d = _infer_direction(text)
    if d in ("LONG", "SHORT"):
        return d
    if _RE_BEARISH_THESIS.search(text):
        return "SHORT"
    if _RE_BULLISH_THESIS.search(text):
        return "LONG"
    return None


def _has_thesis_evidence(text: str) -> bool:
    """Require symbol + directional thesis evidence (precision-first)."""
    if not _thesis_symbols(text):
        return False
    return _infer_thesis_direction(text) is not None


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

    # Remove http(s) URLs so substrings like "tp" inside "https" don't trigger level regexes.
    t = _RE_URL.sub(" ", text.strip())
    reasons: list[str] = []

    if _RE_POSITION_MGMT.search(t) and not _RE_NON_TRADE_UPDATE.search(t):
        if not (_RE_TP_HIT.search(t) or _RE_SL_HIT.search(t)):
            return ResearchClassification(ResearchContentType.TRADE_UPDATE, ["position_mgmt"], 0.80)

    if _RE_TP_HIT.search(t) or _RE_SL_HIT.search(t) or _RE_CLOSE.search(t):
        return ResearchClassification(ResearchContentType.RESULT_UPDATE, ["result"], 0.88)

    is_onchain = bool(_RE_ONCHAIN.search(t) and not _has_author_intent(t))
    is_whale = bool(
        _RE_WHALE_FLOW.search(t)
        and _RE_WHALE_ECON_ACTION.search(t)
        and not _has_author_intent(t)
    )

    if is_onchain:
        return ResearchClassification(ResearchContentType.ONCHAIN_EVENT, ["onchain"], 0.86)

    if is_whale:
        return ResearchClassification(
            ResearchContentType.WHALE_FLOW,
            ["whale_flow_third_party"],
            0.84,
        )

    if _RE_NEWS.search(t):
        return ResearchClassification(ResearchContentType.NEWS_EVENT, ["news"], 0.82)

    # PROMO only when promotional content is primary and not a clear whale/onchain event.
    if _RE_PROMO.search(t) and not (is_whale or is_onchain):
        return ResearchClassification(ResearchContentType.PROMO, ["promo"], 0.92)

    has_levels = _has_trade_levels(t)
    author = _has_author_intent(t)
    third_party = _is_third_party_observed(t)

    def _is_precise_explicit_signal() -> bool:
        # Precision-first: require symbol + direction + actionable structure.
        syms = extract_symbols(t)
        direction = _infer_direction(t)
        if not syms or direction not in ("LONG", "SHORT"):
            return False

        has_entry = bool(ENTRY_RE.search(t))
        has_sl = bool(SL_RE.search(t))
        # TP precision: accept strict TP_RE or TP_LINE_RE with at least one numeric.
        tp_line_m = TP_LINE_RE.search(t)
        has_tp = bool(
            TP_RE.search(t)
            or (tp_line_m is not None and re.search(r"\d+(?:\.\d+)?", tp_line_m.group(1))),
        )

        actionable = (
            (has_entry and has_sl)
            or (has_entry and has_tp)
            or (has_sl and has_tp and _RE_MARKET_ENTRY_LANGUAGE.search(t))
        )
        if not actionable:
            return False

        # Clear intent / format: either author intent, or presence of labeled trade levels.
        signal_format = author or bool(
            re.search(r"(?i)\b(entry|sl|stop|tp|target)\b", t)
        )
        return bool(signal_format)

    if author and not third_party and has_levels and _is_precise_explicit_signal():
        return ResearchClassification(
            ResearchContentType.EXPLICIT_SIGNAL,
            ["author_intent_precise"],
            0.92,
        )

    if third_party and has_levels:
        return ResearchClassification(
            ResearchContentType.WHALE_FLOW,
            ["third_party_with_levels"],
            0.80,
            suspicious_explicit=True,
        )

    if (
        _RE_THESIS_LANGUAGE.search(t)
        and _has_thesis_evidence(t)
        and not _RE_QUESTION_OR_META.search(t)
        and not _RE_POSITION_MGMT.search(t)
        and not (is_whale or is_onchain)
    ):
        reasons.append("thesis_language")
        return ResearchClassification(
            ResearchContentType.TRADER_THESIS,
            reasons,
            0.76,
        )

    if _has_technical_levels_context(t) and not _SIDE_RE.search(t[:500]):
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

    if _SIDE_RE.search(t[:400]) and has_levels and not third_party and _is_precise_explicit_signal():
        return ResearchClassification(
            ResearchContentType.EXPLICIT_SIGNAL,
            ["side_and_levels_precise"],
            0.86,
            suspicious_explicit=False,
        )

    # SIDE tokens without full signal: require symbol + direction evidence.
    if (
        _SIDE_RE.search(t[:400])
        and _has_thesis_evidence(t)
        and not _RE_QUESTION_OR_META.search(t)
        and not _RE_POSITION_MGMT.search(t)
        and not third_party
    ):
        return ResearchClassification(
            ResearchContentType.TRADER_THESIS,
            ["side_with_symbol_direction"],
            0.55,
        )

    return ResearchClassification(ResearchContentType.OTHER, ["unclassified"], 0.30)
