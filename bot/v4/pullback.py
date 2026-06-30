"""Pullback detection for V4 Shadow entry."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def side_ask(quotes: dict[str, float | None], side: str) -> float | None:
    key = "yes_ask" if side == "YES" else "no_ask"
    return quotes.get(key)


def update_peak_ask(current_peak: float | None, ask: float | None) -> float | None:
    if ask is None:
        return current_peak
    if current_peak is None:
        return ask
    return max(current_peak, ask)


def pullback_reached(peak_ask: float | None, current_ask: float | None, pullback: float) -> bool:
    if peak_ask is None or current_ask is None:
        return False
    return current_ask <= peak_ask - pullback


def log_wait_pullback(
    *,
    side: str,
    peak_ask: float | None,
    current_ask: float | None,
    pullback: float,
) -> None:
    logger.info(
        "V4 WAIT PULLBACK | side=%s peak=%.3f ask=%.3f need<=%.3f",
        side,
        peak_ask or 0.0,
        current_ask or 0.0,
        (peak_ask or 0.0) - pullback,
    )
