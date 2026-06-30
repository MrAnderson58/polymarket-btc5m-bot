"""Exit helpers for Early Reversion v2/v2.5/v3 trailing stop modes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from bot.config import (
    ENABLE_TRAILING_STOP,
    EXIT_MODE,
    TRAILING_ACTIVATION_PROFIT,
    TRAILING_OFFSET,
)

logger = logging.getLogger(__name__)

ExitReason = Literal["TRAILING_STOP", "STOP_LOSS", "TIME_STOP"]

# Backward-compatible aliases for older imports/tests.
TRAIL_ACTIVATION_DELTA = TRAILING_ACTIVATION_PROFIT
TRAIL_STOP_DELTA = TRAILING_OFFSET


@dataclass(frozen=True)
class TrailingSnapshot:
    """Computed trailing state for one open-trade tick."""

    highest_bid: float
    trailing_active: bool
    trailing_stop_price: float | None
    activation_price: float


@dataclass(frozen=True)
class TrailingTickResult:
    """Trailing state after one open-trade tick."""

    snapshot: TrailingSnapshot
    peak_bid: float
    trailing_activation_price: float | None
    highest_price: float | None
    max_profit_pct: float | None


@dataclass(frozen=True)
class TrailingCloseStats:
    """Trailing statistics persisted when a trade closes."""

    activation_price: float | None
    highest_price: float | None
    exit_price: float
    max_profit_pct: float
    realized_profit_pct: float
    profit_left_on_table_pct: float


def is_trailing_exit_mode() -> bool:
    """Dollar-based trailing when EXIT_MODE=trailing and feature is enabled."""
    return EXIT_MODE == "trailing" and ENABLE_TRAILING_STOP


def trailing_enabled_at_entry() -> bool:
    return is_trailing_exit_mode()


def activation_price(entry_price: float) -> float:
    return entry_price + TRAILING_ACTIVATION_PROFIT


def activation_reached(entry_price: float, bid: float) -> bool:
    return bid + 1e-9 >= activation_price(entry_price)


def compute_trailing_stop_price(highest_bid: float) -> float:
    return highest_bid - TRAILING_OFFSET


def build_trailing_snapshot(
    entry_price: float,
    bid: float,
    *,
    prev_active: bool,
    prev_highest_after_activation: float | None,
    peak_bid: float,
) -> TrailingSnapshot:
    act_price = activation_price(entry_price)

    if not is_trailing_exit_mode():
        return TrailingSnapshot(
            highest_bid=peak_bid,
            trailing_active=False,
            trailing_stop_price=None,
            activation_price=act_price,
        )

    if prev_active:
        highest = max(prev_highest_after_activation or bid, bid)
        return TrailingSnapshot(
            highest_bid=highest,
            trailing_active=True,
            trailing_stop_price=compute_trailing_stop_price(highest),
            activation_price=act_price,
        )

    if activation_reached(entry_price, peak_bid):
        return TrailingSnapshot(
            highest_bid=bid,
            trailing_active=True,
            trailing_stop_price=compute_trailing_stop_price(bid),
            activation_price=act_price,
        )

    return TrailingSnapshot(
        highest_bid=peak_bid,
        trailing_active=False,
        trailing_stop_price=None,
        activation_price=act_price,
    )


def process_trailing_tick(trade, entry_price: float, bid: float) -> TrailingTickResult:
    """Update trailing state for one open-trade tick."""
    prev_max = float(trade["max_price_seen"] or entry_price)
    peak_bid = max(prev_max, bid)
    prev_active = trade_trailing_active(trade)
    prev_highest = _row_float(trade, "highest_price")
    prev_activation = _row_float(trade, "trailing_activation_price")

    snapshot = build_trailing_snapshot(
        entry_price,
        bid,
        prev_active=prev_active,
        prev_highest_after_activation=prev_highest,
        peak_bid=peak_bid,
    )

    trailing_activation_price = prev_activation
    if snapshot.trailing_active and trailing_activation_price is None:
        trailing_activation_price = bid

    highest_price = prev_highest
    if snapshot.trailing_active:
        highest_price = snapshot.highest_bid

    max_profit_pct = None
    if snapshot.trailing_active and highest_price is not None:
        max_profit_pct = _pnl_percent(entry_price, highest_price)

    return TrailingTickResult(
        snapshot=snapshot,
        peak_bid=peak_bid,
        trailing_activation_price=trailing_activation_price,
        highest_price=highest_price,
        max_profit_pct=max_profit_pct,
    )


def _pnl_percent(entry_price: float, bid: float) -> float:
    return (bid - entry_price) / entry_price * 100


def compute_trailing_close_stats(
    entry_price: float,
    exit_price: float,
    *,
    activation_price_value: float | None,
    highest_price: float | None,
) -> TrailingCloseStats:
    highest = highest_price if highest_price is not None else exit_price
    max_profit_pct = _pnl_percent(entry_price, highest)
    realized_profit_pct = _pnl_percent(entry_price, exit_price)
    return TrailingCloseStats(
        activation_price=activation_price_value,
        highest_price=highest,
        exit_price=exit_price,
        max_profit_pct=max_profit_pct,
        realized_profit_pct=realized_profit_pct,
        profit_left_on_table_pct=max_profit_pct - realized_profit_pct,
    )


def resolve_er_exit_reason(
    *,
    entry_price: float,
    bid: float,
    max_price_seen: float,
    seconds_in_trade: float,
    grace_sec: float,
    stop_loss_pct: float,
    time_stop_sec: float,
    legacy_trailing_pct: float,
    legacy_trailing_grace_sec: float | None = None,
    trailing_active: bool = False,
    highest_after_activation: float | None = None,
) -> ExitReason | None:
    """
    Resolve exit reason for ER v2/v2.5/v3 open trade management.

    EXIT_MODE=trailing: dollar activation/offset after grace; no fixed TP once active.
    EXIT_MODE=fixed: legacy percent trailing (ER_V*_TRAILING_STOP_PCT).
    """
    if seconds_in_trade < grace_sec:
        return None

    if _pnl_percent(entry_price, bid) <= stop_loss_pct:
        return "STOP_LOSS"

    if seconds_in_trade >= time_stop_sec:
        return "TIME_STOP"

    if is_trailing_exit_mode():
        if not trailing_active:
            return None
        highest = highest_after_activation or max_price_seen
        if bid <= highest - TRAILING_OFFSET:
            return "TRAILING_STOP"
        return None

    if legacy_trailing_grace_sec is not None and seconds_in_trade < legacy_trailing_grace_sec:
        return None

    trailing_floor = max_price_seen * (1 - legacy_trailing_pct / 100)
    if bid <= trailing_floor:
        return "TRAILING_STOP"

    return None


def log_trailing_updates(
    *,
    entry_price: float,
    snapshot: TrailingSnapshot,
    prev_highest_bid: float | None,
    prev_active: bool,
) -> None:
    if not is_trailing_exit_mode():
        return

    if not prev_active and snapshot.trailing_active:
        logger.info(
            "TRAILING ACTIVATED\n"
            "entry=%.2f\n"
            "activation=%.2f\n"
            "highest=%.2f\n"
            "stop=%.2f",
            entry_price,
            snapshot.activation_price,
            snapshot.highest_bid,
            snapshot.trailing_stop_price or 0.0,
        )
        return

    if (
        snapshot.trailing_active
        and prev_highest_bid is not None
        and snapshot.highest_bid > prev_highest_bid
        and snapshot.trailing_stop_price is not None
    ):
        profit_pct = _pnl_percent(entry_price, snapshot.highest_bid)
        logger.info(
            "TRAILING UPDATE\n"
            "highest=%.2f\n"
            "stop=%.2f\n"
            "profit=%+.1f%%",
            snapshot.highest_bid,
            snapshot.trailing_stop_price,
            profit_pct,
        )


def log_trail_exit(
    *,
    entry_price: float,
    stats: TrailingCloseStats,
) -> None:
    if not is_trailing_exit_mode():
        return
    logger.info(
        "TRAILING EXIT\n"
        "entry=%.2f\n"
        "highest=%.2f\n"
        "exit=%.2f\n\n"
        "max_profit=%+.1f%%\n"
        "realized=%+.1f%%\n"
        "left_on_table=%.1f%%",
        entry_price,
        stats.highest_price or stats.exit_price,
        stats.exit_price,
        stats.max_profit_pct,
        stats.realized_profit_pct,
        stats.profit_left_on_table_pct,
    )


def log_trailing_summary(
    *,
    strategy_name: str,
    stats: TrailingCloseStats,
    holding_time_seconds: float,
    exit_reason: str,
) -> None:
    logger.info(
        "Trailing Summary\n\n"
        "Strategy: %s\n"
        "Activation: +%.2f\n"
        "Offset: %.2f\n\n"
        "Max Profit:          %+.1f%%\n"
        "Realized Profit:     %+.1f%%\n"
        "Left On Table:        %.1f%%\n"
        "Holding Time:         %.0f sec\n"
        "Exit Reason:          %s",
        strategy_name,
        TRAILING_ACTIVATION_PROFIT,
        TRAILING_OFFSET,
        stats.max_profit_pct,
        stats.realized_profit_pct,
        stats.profit_left_on_table_pct,
        holding_time_seconds,
        exit_reason.lower().replace("_", " "),
    )


def build_trailing_close_stats(trade, entry_price: float, exit_price: float) -> TrailingCloseStats | None:
    if not _row_bool(trade, "trailing_enabled"):
        return None
    activation = _row_float(trade, "trailing_activation_price")
    highest = _row_float(trade, "highest_price")
    if activation is None and highest is None and not trade_trailing_active(trade):
        return None
    return compute_trailing_close_stats(
        entry_price,
        exit_price,
        activation_price_value=activation,
        highest_price=highest,
    )


def trade_trailing_active(trade) -> bool:
    return _row_bool(trade, "trailing_active")


def _row_bool(trade, column: str) -> bool:
    if column not in trade.keys():
        return False
    return bool(trade[column])


def _row_float(trade, column: str) -> float | None:
    if column not in trade.keys():
        return None
    value = trade[column]
    if value is None:
        return None
    return float(value)
