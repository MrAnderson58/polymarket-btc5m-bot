"""S41 — tag news items with structured Intelligence fields."""

from __future__ import annotations

import re
from typing import Any

from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
    detect_symbols,
    source_type_for,
)

_BULLISH = (
    "surge", "rally", "soar", "bullish", "all-time high", "ath", "approval",
    "inflow", "etf approved", "partnership", "launch", "record high", "pump",
)
_BEARISH = (
    "hack", "exploit", "crash", "plunge", "bearish", "lawsuit", "sec charge",
    "ban", "outflow", "liquidation", "collapse", "fraud", "dump", "sec sues",
)
_IMPORTANT = (
    "fed", "fomc", "etf", "sec", "hack", "exploit", "whale", "blackrock",
    "grayscale", "cpi", "interest rate", "emergency", "bankruptcy",
)
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")


def detect_language(text: str) -> str:
    if _CYRILLIC.search(text or ""):
        return "ru"
    return "en"


def score_importance(title: str, body: str) -> float:
    blob = f"{title} {body}".lower()
    score = 0.35
    for kw in _IMPORTANT:
        if kw in blob:
            score += 0.12
    if any(k in blob for k in _BULLISH) or any(k in blob for k in _BEARISH):
        score += 0.08
    return round(min(1.0, score), 3)


def score_sentiment(title: str, body: str) -> tuple[float, float, float]:
    """Return (bullish, bearish, neutral) in 0..1 summing ~1."""
    blob = f"{title} {body}".lower()
    b = sum(1 for k in _BULLISH if k in blob)
    s = sum(1 for k in _BEARISH if k in blob)
    if b == 0 and s == 0:
        return 0.2, 0.2, 0.6
    total = b + s
    bull = b / total
    bear = s / total
    neu = max(0.0, 1.0 - bull - bear)
    # renormalize slightly toward neutral when weak signal
    if total == 1:
        neu = 0.3
        rem = 0.7
        bull = rem * bull / (bull + bear) if (bull + bear) else 0.35
        bear = rem - bull
    return round(bull, 3), round(bear, 3), round(neu, 3)


def tag_news_item(
    *,
    timestamp: int,
    source: str,
    title: str,
    body: str,
    url: str,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Build AI-ready tagged news dict."""
    text = f"{title}\n{body}"
    syms = symbols if symbols is not None else detect_symbols(text)
    importance = score_importance(title, body)
    language = detect_language(text)
    return {
        "timestamp": int(timestamp),
        "source": str(source or "unknown")[:80],
        "source_type": source_type_for(str(source or "")),
        "title": str(title or "")[:500],
        "body": str(body or "")[:4000],
        "url": str(url or "")[:500],
        "symbols": list(syms),
        "importance": importance,
        "language": language,
    }
