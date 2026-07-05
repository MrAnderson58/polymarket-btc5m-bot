"""Deterministic futures signal parser — never invent missing values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SYMBOL_ALIASES = {
    "BITCOIN": "BTC", "BTCUSDT": "BTC", "BTC/USDT": "BTC", "BTC-PERP": "BTC",
    "ETHEREUM": "ETH", "ETHUSDT": "ETH", "ETH/USDT": "ETH",
    "SOLANA": "SOL", "SOLUSDT": "SOL",
    "XRPUSDT": "XRP", "DOGEUSDT": "DOGE", "BNBUSDT": "BNB",
}

SYMBOL_RE = re.compile(
    r"\b(#?(?:BTC|ETH|SOL|XRP|DOGE|BNB|ADA|AVAX|LINK|MATIC|DOT|LTC|APT|ARB|OP|SUI|SEI|WIF|PEPE|BONK)"
    r"(?:USDT|/USDT|-PERP)?)\b",
    re.IGNORECASE,
)
SIDE_RE = re.compile(r"\b(LONG|SHORT|BUY|SELL)\b", re.IGNORECASE)
ENTRY_RE = re.compile(
    r"(?:entry|enter|buy(?:\s*zone)?|sell(?:\s*zone)?)\s*[:@]?\s*"
    r"(\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+(?:\.\d+)?)?)",
    re.IGNORECASE,
)
SL_RE = re.compile(
    r"(?:sl|stop\s*loss|stop)\s*[:@]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
TP_RE = re.compile(
    r"(?:tp\d*|take\s*profit\s*\d*|target\s*\d*)\s*[:@]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
LEV_RE = re.compile(r"(?:lev(?:erage)?|x)\s*[:@]?\s*(\d+(?:\.\d+)?)\s*x?", re.IGNORECASE)
TF_RE = re.compile(r"\b(\d+\s*[mhdw]|scalp|intraday|swing)\b", re.IGNORECASE)
CONF_RE = re.compile(r"(?:confidence|conf)\s*[:@]?\s*(\d+(?:\.\d+)?)\s*%?", re.IGNORECASE)


@dataclass
class ParsedSignal:
    symbol: str | None = None
    side: str | None = None
    entry_min: float | None = None
    entry_max: float | None = None
    stop_loss: float | None = None
    take_profits: list[float] = field(default_factory=list)
    leverage: float | None = None
    timeframe: str | None = None
    confidence: float | None = None
    parser_confidence: float = 0.0
    fields_found: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalize_symbol(raw: str) -> str:
    token = raw.upper().lstrip("#")
    token = token.replace("/USDT", "").replace("USDT", "").replace("-PERP", "")
    return SYMBOL_ALIASES.get(token, token)


def _normalize_side(raw: str) -> str:
    s = raw.upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    return s


def _parse_entry_range(text: str) -> tuple[float | None, float | None]:
    m = ENTRY_RE.search(text)
    if not m:
        return None, None
    chunk = m.group(1).replace(" ", "")
    if "-" in chunk or "–" in chunk or "—" in chunk:
        parts = re.split(r"[-–—]", chunk)
        if len(parts) == 2:
            try:
                a, b = float(parts[0]), float(parts[1])
                return min(a, b), max(a, b)
            except ValueError:
                return None, None
    try:
        v = float(chunk)
        return v, v
    except ValueError:
        return None, None


def parse_signal_text(text: str) -> ParsedSignal:
    """Parse signal fields from message text. Missing fields stay None."""
    out = ParsedSignal()
    if not text or not text.strip():
        out.errors.append("empty_text")
        return out

    sym = SYMBOL_RE.search(text)
    if sym:
        out.symbol = _normalize_symbol(sym.group(1))
        out.fields_found.append("symbol")

    side_m = SIDE_RE.search(text)
    if side_m:
        out.side = _normalize_side(side_m.group(1))
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

    for tp in TP_RE.finditer(text):
        try:
            out.take_profits.append(float(tp.group(1)))
        except ValueError:
            out.errors.append("invalid_take_profit")
    if out.take_profits:
        out.fields_found.append("take_profit")

    lev = LEV_RE.search(text)
    if lev:
        try:
            out.leverage = float(lev.group(1))
            out.fields_found.append("leverage")
        except ValueError:
            out.errors.append("invalid_leverage")

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

    # Parser confidence heuristic
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
    return out


class SignalParser:
    """LLM-ready interface wrapper around deterministic parser."""

    parser_version: str = "deterministic_v1"

    def parse(self, text: str, *, use_llm: bool = False) -> ParsedSignal:
        if use_llm:
            # Placeholder for future LLM backend — deterministic only for now
            pass
        return parse_signal_text(text)
