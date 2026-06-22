"""Early Reversion v2.5 — V2 with 15s grace period."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Literal

from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    ENABLED_STRATEGIES_V25,
    ER_V2_ENTRY_WINDOW_SEC,
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    ER_V2_TRAILING_STOP_PCT,
    ER_V25_GRACE_PERIOD_SEC,
)
from bot.database import (
    close_early_reversion_v25_trade,
    get_open_early_reversion_v25_trades,
    has_early_reversion_v25_trade,
    insert_early_reversion_v25_trade,
    update_early_reversion_v25_trade_tracking,
)
from bot.early_reversion import SIGNALS
from bot.execution import EntryOrder, attempt_entry_open, close_early_reversion_position
from bot.market_scanner import Btc5mMarket

logger = logging.getLogger(__name__)

ExitReason = Literal["TRAILING_STOP", "STOP_LOSS", "TIME_STOP"]

ACTIVE_SIGNALS_V25 = tuple(
    signal for signal in SIGNALS if signal.strategy_name in ENABLED_STRATEGIES_V25
)


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
) -> ExitReason | None:
    pnl = _pnl_percent(entry_price, bid)

    if seconds_in_trade < ER_V25_GRACE_PERIOD_SEC:
        return None

    if pnl <= ER_V2_STOP_LOSS_PCT:
        return "STOP_LOSS"

    if seconds_in_trade >= ER_V2_TIME_STOP_SEC:
        return "TIME_STOP"

    trailing_floor = max_price_seen * (1 - ER_V2_TRAILING_STOP_PCT / 100)
    if bid <= trailing_floor:
        return "TRAILING_STOP"

    return None


def _token_id_for_side(market: Btc5mMarket, side: str) -> str:
    return market.yes_token_id if side == "YES" else market.no_token_id


def _close_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    exit_reason: ExitReason,
    now_ts: int,
    token_id: str | None = None,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time = now_ts - entry_ts

    def _finalize() -> None:
        close_early_reversion_v25_trade(
            conn,
            trade["id"],
            exit_price=bid,
            exit_reason=exit_reason,
            pnl_percent=_pnl_percent(entry_price, bid),
            pnl_usdc=_pnl_usdc(entry_price, bid),
            holding_time_seconds=holding_time,
        )
        logger.info(
            "Early Reversion v2.5 %s | %s | %s @ %.3f | PnL %.2f%% | held %ss",
            trade["strategy_name"],
            trade["market_slug"],
            exit_reason,
            bid,
            _pnl_percent(entry_price, bid),
            holding_time,
        )

    close_early_reversion_position(
        conn,
        strategy_version="v2.5",
        trade=trade,
        token_id=token_id,
        bid=bid,
        exit_reason=exit_reason,
        size_usdc=EARLY_REVERSION_POSITION_SIZE_USDC,
        close_trade=_finalize,
    )


def _manage_open_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    now_ts: int,
    market: Btc5mMarket,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    max_price_seen = max(float(trade["max_price_seen"] or entry_price), bid)
    seconds_in_trade = now_ts - entry_ts

    exit_reason = _resolve_exit_reason(
        entry_price=entry_price,
        bid=bid,
        max_price_seen=max_price_seen,
        seconds_in_trade=seconds_in_trade,
    )

    if exit_reason:
        _close_trade(
            conn,
            trade,
            bid=bid,
            exit_reason=exit_reason,
            now_ts=now_ts,
            token_id=_token_id_for_side(market, trade["side"]),
        )
        return

    update_early_reversion_v25_trade_tracking(
        conn,
        trade["id"],
        max_price_seen=max_price_seen,
        last_bid=bid,
    )


def _try_open_signals(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
) -> None:
    for signal in ACTIVE_SIGNALS_V25:
        if has_early_reversion_v25_trade(conn, market.slug, signal.strategy_name):
            continue

        _, ask = _side_prices(quotes, signal.side)
        if ask is None or ask > signal.entry_threshold:
            continue

        token_id = market.yes_token_id if signal.side == "YES" else market.no_token_id
        opened = attempt_entry_open(
            conn,
            EntryOrder(
                strategy_version="v2.5",
                strategy_name=signal.strategy_name,
                market_slug=market.slug,
                side=signal.side,
                token_id=token_id,
                price=ask,
                size_usdc=EARLY_REVERSION_POSITION_SIZE_USDC,
            ),
            insert_trade=lambda: insert_early_reversion_v25_trade(
                conn,
                market_slug=market.slug,
                window_start_ts=market.window_start_ts,
                end_ts=market.end_ts,
                side=signal.side,
                strategy_name=signal.strategy_name,
                entry_price=ask,
                entry_ts=now_ts,
            ),
        )
        if not opened:
            continue
        logger.info(
            "Early Reversion v2.5 %s | %s | BUY %s @ %.3f",
            signal.strategy_name,
            market.slug,
            signal.side,
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

    for trade in get_open_early_reversion_v25_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            bid = trade["last_bid"]
        if bid is None:
            bid = float(trade["entry_price"])

        entry_price = float(trade["entry_price"])
        max_price_seen = max(float(trade["max_price_seen"] or entry_price), float(bid))
        seconds_in_trade = now_ts - int(trade["entry_ts"])

        exit_reason = _resolve_exit_reason(
            entry_price=entry_price,
            bid=float(bid),
            max_price_seen=max_price_seen,
            seconds_in_trade=seconds_in_trade,
        ) or "TIME_STOP"

        _close_trade(
            conn,
            trade,
            bid=float(bid),
            exit_reason=exit_reason,
            now_ts=now_ts,
            token_id=_token_id_for_side(market, trade["side"]),
        )


def close_due_early_reversion_v25_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    due = conn.execute(
        """
        SELECT * FROM early_reversion_v25_trades
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
        max_price_seen = max(float(trade["max_price_seen"] or entry_price), float(bid))
        seconds_in_trade = now_ts - int(trade["entry_ts"])

        exit_reason = _resolve_exit_reason(
            entry_price=entry_price,
            bid=float(bid),
            max_price_seen=max_price_seen,
            seconds_in_trade=seconds_in_trade,
        ) or "TIME_STOP"

        _close_trade(
            conn,
            trade,
            bid=float(bid),
            exit_reason=exit_reason,
            now_ts=now_ts,
        )

    return len(due)


def process_early_reversion_v25(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
) -> None:
    now_ts = int(time.time())
    seconds_open = _seconds_since_open(market)

    if seconds_open <= ER_V2_ENTRY_WINDOW_SEC:
        _try_open_signals(conn, market, quotes, now_ts)

    for trade in get_open_early_reversion_v25_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            continue
        _manage_open_trade(conn, trade, bid=bid, now_ts=now_ts, market=market)

    _close_expired_trades(conn, market, quotes, now_ts)
