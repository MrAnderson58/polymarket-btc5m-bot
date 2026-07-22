"""S51 — Concrete evidence reasons only (ban vague phrases)."""

from __future__ import annotations

import re
from typing import Any

# Phrases that must never appear as standalone reasons.
BANNED_REASON_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"^current trend$",
        r"^trend$",
        r"^market bias$",
        r"^bullish structure$",
        r"^bearish structure$",
        r"^ai narrative",
        r"^derived from available",
        r"^btc trend/bias",
        r"^see internal",
        r"^insufficient data",
        r"^general (bullish|bearish|market)",
        r"^momentum$",
        r"^technicals?$",
    )
)

_CONCRETE_HINTS = (
    "etf",
    "funding",
    "oi ",
    "open interest",
    "dxy",
    "cpi",
    "fed",
    "whale",
    "netflow",
    "%",
    "m ",
    "+",
    "-",
)


def is_banned_reason(text: str) -> bool:
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return True
    if len(t) < 4:
        return True
    return any(p.search(t) for p in BANNED_REASON_PATTERNS)


def is_concrete_reason(text: str) -> bool:
    if is_banned_reason(text):
        return False
    low = text.lower()
    # Must contain a number OR a known market token
    if re.search(r"\d", text):
        return True
    return any(h in low for h in _CONCRETE_HINTS)


def sanitize_reasons(reasons: list[str] | None, *, limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in reasons or []:
        text = " ".join(str(raw).split()).strip()
        if not text or is_banned_reason(text) or not is_concrete_reason(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:120])
        if len(out) >= limit:
            break
    return out


def reasons_from_direction_factors(
    factors: list[str],
    *,
    limit: int = 6,
) -> list[str]:
    return sanitize_reasons(factors, limit=limit)


def merge_concrete_reasons(
    *groups: list[str] | None,
    limit: int = 6,
) -> list[str]:
    merged: list[str] = []
    for g in groups:
        merged.extend(g or [])
    return sanitize_reasons(merged, limit=limit)


def evidence_reasons_from_context(ctx: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Build concrete reasons directly from context numbers / top events."""
    from bot.research.ai_analyst.signal_consistency.direction import score_direction_from_context

    decision = score_direction_from_context(ctx)
    return reasons_from_direction_factors(decision.factors, limit=limit)
