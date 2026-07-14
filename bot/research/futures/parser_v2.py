"""Deterministic parser v2 — signal gate, header-first symbol extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.research.futures.parser import (
    CONF_RE,
    SIDE_TOKEN,
    SL_RE,
    TF_RE,
    ParsedSignal,
    _normalize_side,
    _parse_entry_range,
    _parse_leverage,
    _parse_take_profits,
)
from bot.research.futures.taxonomy import MessageType, classify_message, is_trade_signal_type

PARSER_VERSION_V2 = "deterministic_v2"

KNOWN_TICKERS = frozenset({
    "BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "MATIC",
    "DOT", "LTC", "APT", "ARB", "OP", "SUI", "SEI", "WIF", "PEPE", "BONK",
    "ATOM", "ICP", "XLM", "OKB", "IMX", "NEAR", "FIL", "INJ", "TIA", "RUNE",
    "FTM", "AAVE", "UNI", "CRV", "MKR", "COMP", "SNX", "DYDX", "STX", "RNDR",
    "FET", "AGIX", "OCEAN", "HBAR", "VET", "ALGO", "EGLD", "SAND", "MANA",
    "AXS", "GALA", "ENJ", "CHZ", "TRX", "EOS", "XMR", "ZEC", "ETC", "BCH",
})

SYMBOL_ALIASES = {
    "BITCOIN": "BTC", "BTCUSDT": "BTC", "BTC/USDT": "BTC", "BTC-PERP": "BTC",
    "ETHEREUM": "ETH", "ETHUSDT": "ETH", "ETH/USDT": "ETH",
    "SOLANA": "SOL", "SOLUSDT": "SOL",
}

HEADER_LINES = 6
SIGNAL_WINDOW = 350

_PAIR_RE = re.compile(
    r"\b([A-Z]{2,10})(?:USDT|/USDT|-PERP)\b",
    re.IGNORECASE,
)
_DOLLAR_TICKER_RE = re.compile(r"\$\s*([A-Za-z]{2,10})\b")
_HASH_TICKER_RE = re.compile(r"#\s*([A-Za-z]{2,10})\b")
_HEADER_SIGNAL_RE = re.compile(
    rf"(?im)^[^\n]{{0,40}}?(?:#?\$?\s*([A-Za-z]{{2,10}})\s+({SIDE_TOKEN})|"
    rf"({SIDE_TOKEN})\s+#?\$?\s*([A-Za-z]{{2,10}}))",
)
_SIDE_NEAR_RE = re.compile(rf"(?i)\b({SIDE_TOKEN})\b")
_NEGATIVE_URL = re.compile(r"https?://|t\.me/|bit\.ly/", re.I)
_NEGATIVE_CORREL = re.compile(
    r"(?i)(correlat|коррел|(?:like|vs\.?|versus|против|compared to|как)\s+(?:btc|eth|bitcoin|ethereum)|"
    r"follows?\s+btc|движется\s+за\s+btc)",
)
_NEGATIVE_REVIEW = re.compile(
    r"(?i)(обзор|review|analysis|разбор|outlook|forecast|прогноз|tokenomics)",
)


@dataclass
class ParseResultV2:
    parsed: ParsedSignal
    message_type: MessageType
    passes_gate: bool
    gate_reason: str = ""
    taxonomy_reasons: list[str] = field(default_factory=list)


def _normalize_symbol(raw: str) -> str | None:
    token = raw.upper().lstrip("#$")
    token = token.replace("/USDT", "").replace("USDT", "").replace("-PERP", "")
    token = SYMBOL_ALIASES.get(token, token)
    if token in KNOWN_TICKERS:
        return token
    if len(token) >= 2 and token.isalpha():
        return token
    return None


def _is_negative_symbol_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 80): min(len(text), end + 80)]
    if _NEGATIVE_URL.search(window):
        return True
    if _NEGATIVE_CORREL.search(window):
        return True
    return False


def _extract_symbol_header(text: str) -> tuple[str | None, str]:
    header = "\n".join(text.strip().splitlines()[:HEADER_LINES])
    m = _HEADER_SIGNAL_RE.search(header)
    if not m:
        return None, ""
    sym_a, _side_a, _side_b, sym_b = m.group(1), m.group(2), m.group(3), m.group(4)
    for raw in (sym_a, sym_b):
        if raw:
            sym = _normalize_symbol(raw)
            if sym:
                return sym, "header_signal_line"
    return None, ""


def _extract_symbol_near_side(text: str) -> tuple[str | None, str]:
    for side_m in _SIDE_NEAR_RE.finditer(text[:SIGNAL_WINDOW]):
        region_start = max(0, side_m.start() - 40)
        region_end = min(len(text), side_m.end() + 40)
        region = text[region_start:region_end]
        for pat in (_DOLLAR_TICKER_RE, _HASH_TICKER_RE, _PAIR_RE):
            for tm in pat.finditer(region):
                abs_start = region_start + tm.start()
                abs_end = region_start + tm.end()
                if _is_negative_symbol_context(text, abs_start, abs_end):
                    continue
                raw = tm.group(1) if tm.lastindex else tm.group(0)
                sym = _normalize_symbol(raw)
                if sym:
                    return sym, "near_side_keyword"
    return None, ""


def _extract_symbol_pair(text: str) -> tuple[str | None, str]:
    header = text[:SIGNAL_WINDOW]
    for m in _PAIR_RE.finditer(header):
        if _is_negative_symbol_context(text, m.start(), m.end()):
            continue
        sym = _normalize_symbol(m.group(1))
        if sym:
            return sym, "pair_format"
    return None, ""


def _extract_symbol_entry_context(text: str) -> tuple[str | None, str]:
    entry_m = re.search(
        r"(?i)(?:entry|enter|вход)\s*[:@]?\s*\d",
        text[:SIGNAL_WINDOW],
    )
    if not entry_m:
        return None, ""
    region_start = max(0, entry_m.start() - 60)
    region = text[region_start:entry_m.end() + 20]
    for pat in (_DOLLAR_TICKER_RE, _HASH_TICKER_RE, _PAIR_RE):
        for tm in pat.finditer(region):
            sym = _normalize_symbol(tm.group(1) if tm.lastindex else tm.group(0))
            if sym:
                return sym, "entry_line_context"
    return None, ""


def extract_symbol_v2(text: str) -> tuple[str | None, str]:
    for fn in (
        _extract_symbol_header,
        _extract_symbol_near_side,
        _extract_symbol_pair,
        _extract_symbol_entry_context,
    ):
        sym, reason = fn(text)
        if sym:
            return sym, reason
    return None, ""


def _extract_side_v2(text: str) -> str | None:
    header = "\n".join(text.strip().splitlines()[:HEADER_LINES])
    m = _SIDE_NEAR_RE.search(header)
    if m:
        return _normalize_side(m.group(1))
    m = _SIDE_NEAR_RE.search(text[:SIGNAL_WINDOW])
    if m:
        return _normalize_side(m.group(1))
    return None


def passes_signal_gate(text: str, parsed: ParsedSignal, taxonomy: MessageType) -> tuple[bool, str]:
    if taxonomy in (
        MessageType.MARKET_REVIEW,
        MessageType.MARKET_COMMENTARY,
        MessageType.NEWS,
        MessageType.PROMO,
        MessageType.OTHER,
    ):
        return False, f"taxonomy_{taxonomy.value}"

    if taxonomy in (MessageType.TP_HIT, MessageType.SL_HIT, MessageType.POSITION_CLOSE, MessageType.TRADE_UPDATE):
        return False, f"lifecycle_{taxonomy.value}"

    if _NEGATIVE_REVIEW.search(text[:400]) and not (
        parsed.entry_min is not None and parsed.side and parsed.symbol
    ):
        return False, "review_context"

    has_dir = parsed.side is not None
    has_sym = parsed.symbol is not None
    has_entry = parsed.entry_min is not None
    has_sl = parsed.stop_loss is not None
    has_tp = bool(parsed.take_profits)

    if taxonomy == MessageType.EXPLICIT_SIGNAL and has_dir and has_sym and (has_entry or has_sl or has_tp):
        return True, "explicit_taxonomy"

    if has_dir and has_sym and (has_entry or has_sl or has_tp):
        return True, "structured_signal"

    if has_dir and has_sym and has_entry:
        return True, "direction_symbol_entry"

    return False, "insufficient_signal_evidence"


def parse_signal_v2(text: str) -> ParseResultV2:
    out = ParsedSignal()
    tax = classify_message(text)
    out.errors = list(tax.reasons)

    if not text or not text.strip():
        out.errors.append("empty_text")
        return ParseResultV2(out, MessageType.OTHER, False, "empty_text")

    sym, sym_reason = extract_symbol_v2(text)
    if sym:
        out.symbol = sym
        out.fields_found.append(f"symbol:{sym_reason}")

    side = _extract_side_v2(text)
    if side:
        out.side = side
        out.fields_found.append("side")

    emin, emax = _parse_entry_range(text)
    if emin is not None:
        out.entry_min, out.entry_max = emin, emax
        out.fields_found.append("entry")

    sl = SL_RE.search(text)
    if sl:
        try:
            out.stop_loss = float(sl.group(1))
            out.fields_found.append("stop_loss")
        except ValueError:
            out.errors.append("invalid_stop_loss")

    out.take_profits = _parse_take_profits(text)
    if out.take_profits:
        out.fields_found.append("take_profit")

    lev = _parse_leverage(text)
    if lev is not None:
        out.leverage = lev
        out.fields_found.append("leverage")

    tf = TF_RE.search(text)
    if tf:
        out.timeframe = tf.group(1).lower()
        out.fields_found.append("timeframe")

    conf = CONF_RE.search(text)
    if conf:
        try:
            val = float(conf.group(1))
            out.confidence = val / 100.0 if val > 1 else val
            out.fields_found.append("confidence")
        except ValueError:
            out.errors.append("invalid_confidence")

    score = 0.0
    if out.side:
        score += 0.35
    if out.symbol:
        score += 0.25
    if out.entry_min is not None:
        score += 0.15
    if out.stop_loss is not None:
        score += 0.10
    if out.take_profits:
        score += 0.10
    if out.leverage is not None:
        score += 0.05
    out.parser_confidence = min(score, 1.0)

    ok, gate_reason = passes_signal_gate(text, out, tax.message_type)
    return ParseResultV2(
        parsed=out,
        message_type=tax.message_type,
        passes_gate=ok,
        gate_reason=gate_reason,
        taxonomy_reasons=tax.reasons,
    )


class SignalParserV2:
    parser_version: str = PARSER_VERSION_V2

    def parse(self, text: str, *, use_llm: bool = False) -> ParseResultV2:
        return parse_signal_v2(text)
