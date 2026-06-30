"""Logging and statistics for V4 Shadow."""

from __future__ import annotations

import logging

from bot.er_trailing_stop import TrailingCloseStats
from bot.v4.trend_detector import TrendSignal

logger = logging.getLogger(__name__)


def log_trend_found(signal: TrendSignal, score: float, probability: float) -> None:
    logger.info(
        "V4 TREND FOUND | side=%s score=%.1f prob=%.0f%% delta_chg=%.1f consistency=%.0f%%",
        signal.side,
        score,
        probability * 100,
        signal.delta_change,
        signal.consistency * 100,
    )


def log_shadow_buy(
    *,
    side: str,
    entry_price: float,
    score: float,
    probability: float,
    reason: str,
) -> None:
    logger.info(
        "V4 SHADOW BUY | side=%s entry=%.3f score=%.1f prob=%.0f%% reason=%s",
        side,
        entry_price,
        score,
        probability * 100,
        reason,
    )


def log_trailing(
    *,
    entry_price: float,
    bid: float,
    highest: float,
    stop: float | None,
    active: bool,
) -> None:
    profit_pct = (bid - entry_price) / entry_price * 100 if entry_price else 0.0
    logger.info(
        "V4 TRAILING | active=%s bid=%.3f highest=%.3f stop=%s profit=%+.1f%%",
        active,
        bid,
        highest,
        f"{stop:.3f}" if stop is not None else "-",
        profit_pct,
    )


def log_shadow_exit(
    *,
    entry_price: float,
    stats: TrailingCloseStats,
    exit_reason: str,
) -> None:
    logger.info(
        "V4 SHADOW EXIT | entry=%.3f highest=%.3f exit=%.3f reason=%s "
        "max=%+.1f%% realized=%+.1f%% left=%.1f%%",
        entry_price,
        stats.highest_price or stats.exit_price,
        stats.exit_price,
        exit_reason,
        stats.max_profit_pct,
        stats.realized_profit_pct,
        stats.profit_left_on_table_pct,
    )


def log_v4_summary(
    *,
    side: str,
    stats: TrailingCloseStats,
    holding_time_seconds: float,
    exit_reason: str,
    entry_score: float,
    entry_probability: float,
) -> None:
    logger.info(
        "V4 SUMMARY\n\n"
        "Side: %s\n"
        "Entry Score: %.1f\n"
        "Entry Probability: %.0f%%\n\n"
        "Max Profit:          %+.1f%%\n"
        "Realized Profit:     %+.1f%%\n"
        "Left On Table:        %.1f%%\n"
        "Holding Time:         %.0f sec\n"
        "Exit Reason:          %s",
        side,
        entry_score,
        entry_probability * 100,
        stats.max_profit_pct,
        stats.realized_profit_pct,
        stats.profit_left_on_table_pct,
        holding_time_seconds,
        exit_reason.lower().replace("_", " "),
    )
