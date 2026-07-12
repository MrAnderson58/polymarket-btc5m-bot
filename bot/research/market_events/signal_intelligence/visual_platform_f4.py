"""Phase F.4 — chart platform detection from Telegram text/caption metadata."""

from __future__ import annotations

import re

PLATFORMS = (
    "TradingView",
    "Binance",
    "Bybit",
    "OKX",
    "Coinglass",
    "Hyperliquid",
    "Unknown",
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TradingView", re.compile(r"tradingview|tv\.chart|@?\w+\s*on\s*TradingView", re.I)),
    ("Binance", re.compile(r"binance\.com|binance futures|BINANCE", re.I)),
    ("Bybit", re.compile(r"bybit\.com|\bbybit\b", re.I)),
    ("OKX", re.compile(r"okx\.com|\bokx\b", re.I)),
    ("Coinglass", re.compile(r"coinglass", re.I)),
    ("Hyperliquid", re.compile(r"hyperliquid|\bHL\b", re.I)),
]


def detect_platform(text: str, *, filename: str = "") -> str:
    blob = f"{text}\n{filename}"
    for name, pat in _PATTERNS:
        if pat.search(blob):
            return name
    if re.search(r"\b\d+[mhd]\b.*\b(USDT|USD)\b", blob, re.I):
        return "TradingView"
    return "Unknown"
