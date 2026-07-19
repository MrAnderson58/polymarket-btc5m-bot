"""Sentiment / theme scores from headline text."""

from __future__ import annotations

_BULLISH = (
    "surge", "rally", "soar", "bullish", "all-time high", "ath", "approval",
    "inflow", "etf approved", "partnership", "launch", "record high", "pump",
    "adoption", "upgrade",
)
_BEARISH = (
    "hack", "exploit", "crash", "plunge", "bearish", "lawsuit", "ban",
    "outflow", "liquidation", "collapse", "fraud", "dump", "sec sues",
    "breach", "stolen",
)
_MACRO = ("fed", "fomc", "cpi", "inflation", "interest rate", "treasury", "macro")
_WHALE = ("whale", "large transfer", "smart money", "accumulation")
_POLY = ("polymarket", "prediction market", "odds")


def _count(blob: str, keys: tuple[str, ...]) -> int:
    return sum(1 for k in keys if k in blob)


def sentiment_scores(title: str, body: str = "") -> tuple[float, float, float]:
    blob = f"{title} {body}".lower()
    b = _count(blob, _BULLISH)
    s = _count(blob, _BEARISH)
    if b == 0 and s == 0:
        return 0.25, 0.25, 0.5
    total = b + s
    bull = b / total
    bear = s / total
    neu = max(0.0, 1.0 - bull - bear)
    return round(bull, 3), round(bear, 3), round(neu, 3)


def importance_score(title: str, body: str = "") -> float:
    blob = f"{title} {body}".lower()
    score = 0.3
    for kw in (
        "fed", "etf", "hack", "sec", "whale", "war", "liquidation",
        "blackrock", "emergency", "ban",
    ):
        if kw in blob:
            score += 0.1
    return round(min(1.0, score), 3)


def theme_scores(title: str, body: str = "") -> dict[str, float]:
    blob = f"{title} {body}".lower()
    return {
        "macro_score": round(min(1.0, 0.2 + 0.2 * _count(blob, _MACRO)), 3),
        "whale_score": round(min(1.0, 0.15 + 0.25 * _count(blob, _WHALE)), 3),
        "polymarket_score": round(min(1.0, 0.1 + 0.3 * _count(blob, _POLY)), 3),
    }


def risk_level_from_scores(
    *,
    bearish: float,
    importance: float,
    narratives: list[str],
) -> str:
    risk = 0.4 * bearish + 0.4 * importance
    if any(n in narratives for n in ("Hack", "War", "Liquidation", "Regulation")):
        risk += 0.2
    if risk >= 0.7:
        return "HIGH"
    if risk >= 0.45:
        return "MEDIUM"
    return "LOW"
