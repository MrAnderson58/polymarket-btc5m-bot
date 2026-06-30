"""YES_C shadow trading — identical Early Reversion v2 logic without CLOB orders."""

from __future__ import annotations

import logging
import sqlite3
import time

from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    ER_V2_ENTRY_WINDOW_SEC,
    ER_V2_GRACE_PERIOD_SEC,
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    ER_V2_TRAILING_STOP_PCT,
    effective_entry_threshold,
)
from bot.database import (
    close_yes_c_shadow_trade,
    get_open_yes_c_shadow_trades,
    has_yes_c_shadow_trade,
    insert_yes_c_shadow_trade,
    update_yes_c_shadow_trade_tracking,
)
from bot.early_reversion import SIGNALS
from bot.er_entry_check import (
    SignalEntryDiagnostic,
    evaluate_signal_entry,
    format_cycle_signal_status,
    log_version_entry_check,
)
from bot.er_stats import record_entry_evaluation, record_entry_success, YES_C_SHADOW_VERSION
from bot.er_trailing_stop import (
    ExitReason,
    TrailingTickResult,
    build_trailing_close_stats,
    compute_trailing_close_stats,
    log_trail_exit,
    log_trailing_summary,
    log_trailing_updates,
    process_trailing_tick,
    resolve_er_exit_reason,
    trade_trailing_active,
)
from bot.market_scanner import Btc5mMarket

logger = logging.getLogger(__name__)

YES_C_SIGNAL = next(signal for signal in SIGNALS if signal.strategy_name == "YES_C")
SHADOW_STRATEGY_NAME = YES_C_SIGNAL.strategy_name


def _seconds_since_open(market: Btc5mMarket) -> float:
    return time.time() - market.window_start_ts


def _side_prices(quotes: dict[str, float | None], side: str) -> tuple[float | None, float | None]:
    prefix = side.lower()
    return quotes[f"{prefix}_bid"], quotes[f"{prefix}_ask"]


def _pnl_percent(entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) / entry_price * 100


def _pnl_usdc(entry_price: float, exit_price: float) -> float:
    shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
    return shares * (exit_price - entry_price)


def _resolve_exit_reason(
    *,
    entry_price: float,
    bid: float,
    max_price_seen: float,
    seconds_in_trade: float,
    trailing_active: bool = False,
    highest_after_activation: float | None = None,
) -> ExitReason | None:
    return resolve_er_exit_reason(
        entry_price=entry_price,
        bid=bid,
        max_price_seen=max_price_seen,
        seconds_in_trade=seconds_in_trade,
        grace_sec=ER_V2_GRACE_PERIOD_SEC,
        stop_loss_pct=ER_V2_STOP_LOSS_PCT,
        time_stop_sec=ER_V2_TIME_STOP_SEC,
        legacy_trailing_pct=ER_V2_TRAILING_STOP_PCT,
        trailing_active=trailing_active,
        highest_after_activation=highest_after_activation,
    )


def _persist_trailing_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    bid: float,
    tick: TrailingTickResult,
) -> None:
    update_yes_c_shadow_trade_tracking(
        conn,
        trade_id,
        max_price_seen=tick.peak_bid,
        last_bid=bid,
        trailing_active=tick.snapshot.trailing_active,
        trailing_stop_price=tick.snapshot.trailing_stop_price,
        trailing_activation_price=tick.trailing_activation_price,
        highest_price=tick.highest_price,
        max_profit_pct=tick.max_profit_pct,
    )


def _trailing_stats_for_close(
    trade: sqlite3.Row,
    entry_price: float,
    bid: float,
    *,
    trailing_tick: TrailingTickResult | None,
):
    if not trade["trailing_enabled"]:
        return None
    if trailing_tick is not None:
        if (
            trailing_tick.trailing_activation_price is None
            and not trailing_tick.snapshot.trailing_active
        ):
            return None
        return compute_trailing_close_stats(
            entry_price,
            bid,
            activation_price_value=trailing_tick.trailing_activation_price,
            highest_price=trailing_tick.highest_price,
        )
    return build_trailing_close_stats(trade, entry_price, bid)


def _close_shadow_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    exit_reason: ExitReason,
    now_ts: int,
    trailing_tick: TrailingTickResult | None = None,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time = now_ts - entry_ts

    stats = _trailing_stats_for_close(
        trade,
        entry_price,
        bid,
        trailing_tick=trailing_tick,
    )
    trailing_kwargs: dict = {}
    if stats is not None:
        if exit_reason == "TRAILING_STOP":
            log_trail_exit(entry_price=entry_price, stats=stats)
        log_trailing_summary(
            strategy_name=trade["strategy_name"],
            stats=stats,
            holding_time_seconds=holding_time,
            exit_reason=exit_reason,
        )
        trailing_kwargs = {
            "trailing_activation_price": stats.activation_price,
            "highest_price": stats.highest_price,
            "max_profit_pct": stats.max_profit_pct,
            "realized_profit_pct": stats.realized_profit_pct,
            "profit_left_on_table_pct": stats.profit_left_on_table_pct,
        }

    close_yes_c_shadow_trade(
        conn,
        trade["id"],
        exit_price=bid,
        exit_reason=exit_reason,
        pnl_percent=_pnl_percent(entry_price, bid),
        pnl_usdc=_pnl_usdc(entry_price, bid),
        holding_time_seconds=holding_time,
        **trailing_kwargs,
    )
    logger.info(
        "YES_C SHADOW | %s | %s @ %.3f | PnL %.2f%% | held %ss",
        trade["market_slug"],
        exit_reason,
        bid,
        _pnl_percent(entry_price, bid),
        holding_time,
    )


def _manage_open_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    now_ts: int,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    prev_active = trade_trailing_active(trade)
    prev_highest = trade["highest_price"]
    prev_highest_bid = float(prev_highest) if prev_highest is not None else None
    tick = process_trailing_tick(trade, entry_price, bid)
    snapshot = tick.snapshot
    seconds_in_trade = now_ts - entry_ts

    log_trailing_updates(
        entry_price=entry_price,
        snapshot=snapshot,
        prev_highest_bid=prev_highest_bid,
        prev_active=prev_active,
    )

    exit_reason = _resolve_exit_reason(
        entry_price=entry_price,
        bid=bid,
        max_price_seen=tick.peak_bid,
        seconds_in_trade=seconds_in_trade,
        trailing_active=snapshot.trailing_active,
        highest_after_activation=tick.highest_price,
    )

    if exit_reason:
        _persist_trailing_tracking(conn, trade["id"], bid=bid, tick=tick)
        _close_shadow_trade(
            conn,
            trade,
            bid=bid,
            exit_reason=exit_reason,
            now_ts=now_ts,
            trailing_tick=tick,
        )
        return

    _persist_trailing_tracking(conn, trade["id"], bid=bid, tick=tick)


def _shadow_entry_diagnostics(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    *,
    seconds_open: float,
) -> tuple[str | None, SignalEntryDiagnostic]:
    has_trade = has_yes_c_shadow_trade(conn, market.slug, SHADOW_STRATEGY_NAME)
    _, ask = _side_prices(quotes, YES_C_SIGNAL.side)
    threshold = effective_entry_threshold(YES_C_SIGNAL.entry_threshold)
    diagnostic = evaluate_signal_entry(
        YES_C_SIGNAL,
        seconds_open=seconds_open,
        entry_window_sec=ER_V2_ENTRY_WINDOW_SEC,
        quotes=quotes,
        has_trade=has_trade,
    )
    record_entry_evaluation(
        conn,
        strategy_version=YES_C_SHADOW_VERSION,
        strategy_name=SHADOW_STRATEGY_NAME,
        ask=ask,
        entry_threshold=threshold,
        seconds_open=seconds_open,
        entry_window_sec=ER_V2_ENTRY_WINDOW_SEC,
        has_trade=has_trade,
        risk=None,
        would_enter=diagnostic.would_enter,
        market_slug=market.slug,
    )
    signal_status = log_version_entry_check(
        "YES_C SHADOW",
        [diagnostic],
    )
    return signal_status, diagnostic


def _try_open_shadow(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
) -> None:
    if has_yes_c_shadow_trade(conn, market.slug, SHADOW_STRATEGY_NAME):
        return

    _, ask = _side_prices(quotes, YES_C_SIGNAL.side)
    threshold = effective_entry_threshold(YES_C_SIGNAL.entry_threshold)
    if ask is None or ask > threshold:
        return

    logger.info(
        "YES_C SHADOW ENTRY | side=%s price=%s size_usdc=%s",
        YES_C_SIGNAL.side,
        ask,
        EARLY_REVERSION_POSITION_SIZE_USDC,
    )
    insert_yes_c_shadow_trade(
        conn,
        market_slug=market.slug,
        window_start_ts=market.window_start_ts,
        end_ts=market.end_ts,
        side=YES_C_SIGNAL.side,
        strategy_name=SHADOW_STRATEGY_NAME,
        entry_price=ask,
        entry_ts=now_ts,
    )
    record_entry_success(conn, YES_C_SHADOW_VERSION, SHADOW_STRATEGY_NAME)
    logger.info(
        "YES_C SHADOW | %s | BUY %s @ %.3f",
        market.slug,
        YES_C_SIGNAL.side,
        ask,
    )


def _close_expired_trades(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
) -> None:
    if now_ts < market.end_ts:
        return

    for trade in get_open_yes_c_shadow_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            bid = trade["last_bid"]
        if bid is None:
            bid = float(trade["entry_price"])

        entry_price = float(trade["entry_price"])
        entry_ts = int(trade["entry_ts"])
        bid_f = float(bid)
        tick = process_trailing_tick(trade, entry_price, bid_f)
        seconds_in_trade = now_ts - entry_ts

        exit_reason = _resolve_exit_reason(
            entry_price=entry_price,
            bid=bid_f,
            max_price_seen=tick.peak_bid,
            seconds_in_trade=seconds_in_trade,
            trailing_active=tick.snapshot.trailing_active,
            highest_after_activation=tick.highest_price,
        ) or "TIME_STOP"

        _persist_trailing_tracking(conn, trade["id"], bid=bid_f, tick=tick)
        _close_shadow_trade(
            conn,
            trade,
            bid=bid_f,
            exit_reason=exit_reason,
            now_ts=now_ts,
            trailing_tick=tick,
        )


def close_due_yes_c_shadow_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    due = conn.execute(
        """
        SELECT * FROM yes_c_shadow_trades
        WHERE status = 'open' AND end_ts <= ?
        ORDER BY end_ts ASC
        """,
        (now_ts,),
    ).fetchall()

    for trade in due:
        bid = trade["last_bid"]
        if bid is None:
            bid = trade["entry_price"]

        entry_price = float(trade["entry_price"])
        entry_ts = int(trade["entry_ts"])
        bid_f = float(bid)
        tick = process_trailing_tick(trade, entry_price, bid_f)
        seconds_in_trade = now_ts - entry_ts

        exit_reason = _resolve_exit_reason(
            entry_price=entry_price,
            bid=bid_f,
            max_price_seen=tick.peak_bid,
            seconds_in_trade=seconds_in_trade,
            trailing_active=tick.snapshot.trailing_active,
            highest_after_activation=tick.highest_price,
        ) or "TIME_STOP"

        _persist_trailing_tracking(conn, trade["id"], bid=bid_f, tick=tick)
        _close_shadow_trade(
            conn,
            trade,
            bid=bid_f,
            exit_reason=exit_reason,
            now_ts=now_ts,
            trailing_tick=tick,
        )

    return len(due)


def process_yes_c_shadow(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
) -> str:
    now_ts = int(time.time())
    seconds_open = _seconds_since_open(market)

    signal_status, diagnostic = _shadow_entry_diagnostics(
        conn,
        market,
        quotes,
        seconds_open=seconds_open,
    )

    if seconds_open <= ER_V2_ENTRY_WINDOW_SEC:
        _try_open_shadow(conn, market, quotes, now_ts)

    for trade in get_open_yes_c_shadow_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            continue
        _manage_open_trade(conn, trade, bid=bid, now_ts=now_ts)

    _close_expired_trades(conn, market, quotes, now_ts)
    return format_cycle_signal_status(signal_status)
