"""Shared pure feature helpers (Phase 4A).

No side effects. No database access. No network access.
Outputs are deterministic and must match the pre-extract call-site copies.
"""

from __future__ import annotations

from typing import Any


def safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def session_from_hour(hour: int | None) -> str | None:
    """UTC session bucket for attribution (S55 / S66)."""
    if hour is None:
        return None
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if 0 <= h < 8:
        return "Asia"
    if 8 <= h < 13:
        return "London"
    if 13 <= h < 21:
        return "NewYork"
    if 0 <= h <= 23:
        return "Offhours"
    return None


def normalize_coin(raw: Any) -> str:
    """Strip quote suffix and whitespace; empty input → empty string."""
    return str(raw or "").upper().replace("USDT", "").strip()


def normalize_score_0_100(v: float | None) -> float | None:
    """Map unit-interval scores (0–1) to 0–100; leave other scales unchanged."""
    if v is None:
        return None
    if 0 <= v <= 1.0:
        return v * 100.0
    return v
