"""Phase F.4 — extract trading fields from Telegram chart captions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedChartText:
    ticker: str | None = None
    timeframe: str | None = None
    entry: float | None = None
    tp: float | None = None
    sl: float | None = None
    direction: str | None = None
    comments: list[str] = field(default_factory=list)
    raw_snippets: list[str] = field(default_factory=list)


_TICKER = re.compile(
    r"\b([A-Z]{2,12})(?:USDT|USD|PERP|/USDT)?\b",
)
_TF = re.compile(r"\b(\d+[mhdw]|1[HDWM]|4[HDWM]|15[mM]|30[mM]|1[hH]|4[hH])\b")
_ENTRY = re.compile(r"(?:entry|вход|enter)\s*[:=@]?\s*([\d.,]+)", re.I)
_TP = re.compile(r"(?:tp|target|цель|take profit)\s*[:=@]?\s*([\d.,]+)", re.I)
_SL = re.compile(r"(?:sl|stop|стоп|stop loss)\s*[:=@]?\s*([\d.,]+)", re.I)
_LONG = re.compile(r"\b(long|buy|bull|лонг|покупка)\b", re.I)
_SHORT = re.compile(r"\b(short|sell|bear|шорт|продажа)\b", re.I)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def extract_chart_text(text: str) -> ExtractedChartText:
    out = ExtractedChartText()
    if not text or not text.strip():
        return out

    tickers = _TICKER.findall(text.upper())
    skip = {"USDT", "USD", "PERP", "TP", "SL", "OI", "ATR", "BTC", "ETH"}
    for t in tickers:
        if t not in skip and len(t) >= 2:
            out.ticker = t.replace("USDT", "").replace("USD", "")
            break

    tf = _TF.search(text)
    if tf:
        out.timeframe = tf.group(1).lower()

    for pat, attr in ((_ENTRY, "entry"), (_TP, "tp"), (_SL, "sl")):
        m = pat.search(text)
        if m:
            setattr(out, attr, _num(m.group(1)))

    if _LONG.search(text):
        out.direction = "UP"
    elif _SHORT.search(text):
        out.direction = "DOWN"

    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 12:
            out.comments.append(line[:200])
    out.raw_snippets = out.comments[:5]
    return out
