"""S51/S52 — Concrete evidence reasons only (ban vague phrases; aggregate + rank)."""

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
        r"^market looks (bullish|bearish)$",
        r"^looks (bullish|bearish)$",
        r"^bullish structure$",
        r"^bearish structure$",
        r"^ai narrative",
        r"^derived from available",
        r"^btc trend/bias",
        r"^see internal",
        r"^insufficient (data|bias)",
        r"^general (bullish|bearish|market)",
        r"^momentum$",
        r"^technicals?$",
        r"^risk[- ]?on$",
        r"^risk[- ]?off$",
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

# Higher = shown first in trader report.
_REASON_PRIORITY: tuple[tuple[str, int], ...] = (
    ("etf", 100),
    ("netflow", 95),
    ("funding", 80),
    ("oi ", 70),
    ("open interest", 70),
    ("whale", 60),
    ("dxy", 50),
    ("cpi", 50),
    ("fed", 50),
    ("btc 24h", 40),
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


def _reason_priority(text: str) -> int:
    low = text.lower()
    for needle, score in _REASON_PRIORITY:
        if needle in low:
            return score
    if re.search(r"\d", text):
        return 30
    return 10


def _etf_magnitude(text: str) -> float:
    """Absolute ETF flow magnitude for picking the strongest ETF reason."""
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*m", text.lower())
    if not m:
        return 0.0
    try:
        return abs(float(m.group(1)))
    except ValueError:
        return 0.0


def aggregate_and_rank_reasons(
    reasons: list[str] | None,
    *,
    limit: int = 6,
) -> list[str]:
    """
    S52: collapse duplicate ETF/funding/OI lines, then sort by importance.
    Example: keep strongest 'ETF +420M' among several ETF variants.
    """
    cleaned = sanitize_reasons(reasons, limit=50)
    buckets: dict[str, list[str]] = {
        "etf": [],
        "funding": [],
        "oi": [],
        "other": [],
    }
    for text in cleaned:
        low = text.lower()
        if "etf" in low or "netflow" in low:
            buckets["etf"].append(text)
        elif "funding" in low:
            buckets["funding"].append(text)
        elif "oi " in low or "open interest" in low or low.startswith("oi"):
            buckets["oi"].append(text)
        else:
            buckets["other"].append(text)

    picked: list[str] = []
    if buckets["etf"]:
        best_etf = max(buckets["etf"], key=lambda t: (_etf_magnitude(t), _reason_priority(t), len(t)))
        # Prefer compact form when possible
        mag = _etf_magnitude(best_etf)
        if mag > 0 and re.search(r"[+-]?\d", best_etf):
            sign = "+" if ("+" in best_etf or re.search(r"\+\d", best_etf)) else (
                "-" if re.search(r"-\d", best_etf) or "negative" in best_etf.lower() else "+"
            )
            # Keep original if already short & clear
            if len(best_etf) <= 40:
                picked.append(best_etf)
            else:
                picked.append(f"ETF {sign}{mag:.0f}M")
        else:
            picked.append(best_etf)

    if buckets["funding"]:
        picked.append(max(buckets["funding"], key=_reason_priority))
    if buckets["oi"]:
        picked.append(max(buckets["oi"], key=_reason_priority))

    picked.extend(buckets["other"])
    # Final sort by priority, stable for ties
    ranked = sorted(picked, key=lambda t: (-_reason_priority(t), t.lower()))
    # Dedupe again after aggregation
    out: list[str] = []
    seen: set[str] = set()
    for text in ranked:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:120])
        if len(out) >= limit:
            break
    return out


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
    return aggregate_and_rank_reasons(factors, limit=limit)


def merge_concrete_reasons(
    *groups: list[str] | None,
    limit: int = 6,
) -> list[str]:
    merged: list[str] = []
    for g in groups:
        merged.extend(g or [])
    return aggregate_and_rank_reasons(merged, limit=limit)


def evidence_reasons_from_context(ctx: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Build concrete reasons directly from context numbers / top events."""
    from bot.research.ai_analyst.signal_consistency.direction import score_direction_from_context

    decision = score_direction_from_context(ctx)
    return reasons_from_direction_factors(decision.factors, limit=limit)
