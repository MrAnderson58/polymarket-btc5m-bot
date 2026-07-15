"""Phase S3.1 — unified pattern keys + legacy compatibility (read-only helpers)."""

from __future__ import annotations

import re
from typing import Iterable

# Canonical families used in new keys: SYMBOL|family|timeframe
CANONICAL_FAMILIES = frozenset({
    "trend_reversal",
    "trend_continuation",
    "breakout",
    "capitulation",
    "accumulation",
    "distribution",
    "compression",
})

# Map historical / detector labels → canonical family.
_FAMILY_ALIASES: dict[str, str] = {
    "unknown": "trend_reversal",
    "unk": "trend_reversal",
    "consecutive": "trend_reversal",
    "stair_step": "trend_continuation",
    "stairstep": "trend_continuation",
    "slow_bleed": "trend_reversal",
    "slowbleed": "trend_reversal",
    "capitulation": "capitulation",
    "accumulation": "accumulation",
    "distribution": "distribution",
    "compression": "compression",
    "breakout": "breakout",
    "trend_reversal": "trend_reversal",
    "trend_continuation": "trend_continuation",
    "reversal": "trend_reversal",
    "continuation": "trend_continuation",
}

_TF_RE = re.compile(r"^(\d+)(m|h|d)?$", re.IGNORECASE)
_KEY_SPLIT = re.compile(r"\|")


def normalize_timeframe_s31(raw: str | int | None, *, default: str = "60m") -> str:
    """Normalize to compact label: 15m, 60m, 4h, …"""
    if raw is None or raw == "":
        return default
    if isinstance(raw, int):
        minutes = raw
    else:
        s = str(raw).strip().lower().replace(" ", "")
        if s.endswith("h") and s[:-1].isdigit():
            return f"{int(s[:-1])}h"
        if s.endswith("m") and s[:-1].isdigit():
            return f"{int(s[:-1])}m"
        if s.isdigit():
            minutes = int(s)
        else:
            m = _TF_RE.match(s)
            if not m:
                return default
            n = int(m.group(1))
            unit = (m.group(2) or "m").lower()
            if unit == "h":
                return f"{n}h"
            if unit == "d":
                return f"{n * 24}h"
            minutes = n
    if minutes >= 240 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def timeframe_to_minutes_s31(tf: str | int | None) -> int:
    label = normalize_timeframe_s31(tf)
    if label.endswith("h"):
        return int(label[:-1]) * 60
    if label.endswith("m"):
        return int(label[:-1])
    return 60


def normalize_family_s31(raw: str | None) -> str:
    token = (raw or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    return _FAMILY_ALIASES.get(token, token if token in CANONICAL_FAMILIES else "trend_reversal")


def canonical_pattern_key_s31(
    *,
    symbol: str,
    family: str | None,
    timeframe: str | int | None,
) -> str:
    sym = (symbol or "BTC").upper().replace("USDT", "").strip()
    fam = normalize_family_s31(family)
    tf = normalize_timeframe_s31(timeframe)
    return f"{sym}|{fam}|{tf}"


def parse_pattern_key_s31(key: str) -> dict[str, str]:
    """Parse legacy or canonical keys into parts (best-effort)."""
    parts = _KEY_SPLIT.split((key or "").strip())
    symbol = (parts[0] if parts else "BTC").upper()
    family_raw = parts[1] if len(parts) > 1 else "unknown"
    # Legacy G1: SYM|type|60m|5_DOWN
    tf_raw = parts[2] if len(parts) > 2 else "60m"
    # If part2 looks like streak direction, tf was part[2] wrongly — still ok
    if re.match(r"^\d+_(UP|DOWN)$", tf_raw, re.I) and len(parts) > 2:
        # format SYM|type|15m|5_DOWN → tf is parts[2]
        pass
    family = normalize_family_s31(family_raw)
    tf = normalize_timeframe_s31(tf_raw)
    return {"symbol": symbol, "family": family, "timeframe": tf, "raw": key}


def expand_pattern_key_aliases_s31(canonical_key: str) -> list[str]:
    """All keys that should match the same pattern bucket (compatibility)."""
    parsed = parse_pattern_key_s31(canonical_key)
    sym = parsed["symbol"]
    fam = parsed["family"]
    tf = parsed["timeframe"]
    minutes = timeframe_to_minutes_s31(tf)
    tf_variants = {tf, f"{minutes}m"}
    if minutes >= 60 and minutes % 60 == 0:
        tf_variants.add(f"{minutes // 60}h")

    # Reverse map: which raw labels map to this family
    raw_labels = {fam, "unknown"}
    for alias, target in _FAMILY_ALIASES.items():
        if target == fam:
            raw_labels.add(alias)

    keys: list[str] = [canonical_key]
    for lab in raw_labels:
        for tfv in tf_variants:
            keys.append(f"{sym}|{lab}|{tfv}")
            # Legacy G1 suffix wildcards handled by LIKE in SQL; exact prefix forms:
            keys.append(f"{sym}|{lab.upper()}|{tfv}")
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def pattern_key_match_s31(stored_key: str, *, want_symbol: str, want_family: str, want_tf: str) -> bool:
    """True if a stored (possibly legacy) key belongs to the requested bucket."""
    p = parse_pattern_key_s31(stored_key)
    if p["symbol"] != want_symbol.upper():
        return False
    if p["family"] != normalize_family_s31(want_family):
        return False
    # timeframe soft-match: 60m == 1h
    if timeframe_to_minutes_s31(p["timeframe"]) != timeframe_to_minutes_s31(want_tf):
        return False
    return True


def infer_family_from_market_s31(market_snapshot: dict | None, direction: str | None) -> str:
    """Infer pattern family from market agent meta without LLM."""
    meta = (market_snapshot or {}).get("meta") or {}
    trend = str(meta.get("trend") or "").upper()
    reasons = " ".join(str(r) for r in (market_snapshot or {}).get("reasons") or []).lower()
    if "capitulation" in reasons:
        return "capitulation"
    if "accumulation" in reasons:
        return "accumulation"
    if "distribution" in reasons:
        return "distribution"
    if "breakout" in reasons or "compression" in reasons:
        return "breakout"
    d = (direction or "").upper()
    if trend == "FLAT":
        return "compression"
    if d in ("LONG", "SHORT") and trend in ("UP", "DOWN"):
        # Counter to trend → reversal lean; with trend → continuation
        if (d == "LONG" and trend == "DOWN") or (d == "SHORT" and trend == "UP"):
            return "trend_reversal"
        return "trend_continuation"
    return "trend_reversal"


def display_family_s31(family: str) -> str:
    return normalize_family_s31(family).replace("_", " ").title()
