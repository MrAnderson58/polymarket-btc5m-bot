"""Early Reversion v3 — immediate stop loss, trailing grace, fast poll."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal

from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    ENABLED_STRATEGIES_V3,
    ER_V3_ENTRY_WINDOW_SEC,
    ER_V3_STOP_LOSS_PCT,
    ER_V3_TIME_STOP_SEC,
    ER_V3_TRAILING_GRACE_SEC,
    ER_V3_TRAILING_STOP_PCT,
)
from bot.database import (
    close_early_reversion_v3_trade,
    get_open_early_reversion_v3_trades,
    has_early_reversion_v3_trade,
    insert_early_reversion_v3_trade,
    update_early_reversion_v3_trade_tracking,
)
from bot.early_reversion import SIGNALS
from bot.market_scanner import Btc5mMarket

logger = logging.getLogger(__name__)

ExitReason = Literal["TRAILING_STOP", "STOP_LOSS", "TIME_STOP"]

ACTIVE_SIGNALS_V3 = tuple(
    signal for signal in SIGNALS if signal.strategy_name in ENABLED_STRATEGIES_V3
)


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    stop_loss_trigger_pnl: float | None = None


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


def _resolve_exit_decision(
    *,
    entry_price: float,
    bid: float,
    max_price_seen: float,
    seconds_in_trade: float,
) -> ExitDecision | None:
    pnl = _pnl_percent(entry_price, bid)

    if pnl <= ER_V3_STOP_LOSS_PCT:
        return ExitDecision(reason="STOP_LOSS", stop_loss_trigger_pnl=pnl)

    if seconds_in_trade >= ER_V3_TIME_STOP_SEC:
        return ExitDecision(reason="TIME_STOP")

    if seconds_in_trade >= ER_V3_TRAILING_GRACE_SEC:
        trailing_floor = max_price_seen * (1 - ER_V3_TRAILING_STOP_PCT / 100)
        if bid <= trailing_floor:
            return ExitDecision(reason="TRAILING_STOP")

    return None


def _close_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    decision: ExitDecision,
    now_ts: int,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time = now_ts - entry_ts
    exit_pnl = _pnl_percent(entry_price, bid)

    close_early_reversion_v3_trade(
        conn,
        trade["id"],
        exit_price=bid,
        exit_reason=decision.reason,
        stop_loss_trigger_pnl=decision.stop_loss_trigger_pnl,
        pnl_percent=exit_pnl,
        pnl_usdc=_pnl_usdc(entry_price, bid),
        holding_time_seconds=holding_time,
    )

    if decision.reason == "STOP_LOSS":
        slippage = exit_pnl - (decision.stop_loss_trigger_pnl or exit_pnl)
        logger.info(
            "Early Reversion v3 %s | %s | STOP_LOSS @ %.3f | trigger %.2f%% | exit %.2f%% | slip %.2f%% | held %ss",
            trade["strategy_name"],
            trade["market_slug"],
            bid,
            decision.stop_loss_trigger_pnl or exit_pnl,
            exit_pnl,
            slippage,
            holding_time,
        )
    else:
        logger.info(
            "Early Reversion v3 %s | %s | %s @ %.3f | PnL %.2f%% | held %ss",
            trade["strategy_name"],
            trade["market_slug"],
            decision.reason,
            bid,
            exit_pnl,
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
    max_price_seen = max(float(trade["max_price_seen"] or entry_price), bid)
    seconds_in_trade = now_ts - entry_ts

    decision = _resolve_exit_decision(
        entry_price=entry_price,
        bid=bid,
        max_price_seen=max_price_seen,
        seconds_in_trade=seconds_in_trade,
    )

    if decision:
        _close_trade(conn, trade, bid=bid, decision=decision, now_ts=now_ts)
        return

    update_early_reversion_v3_trade_tracking(
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
    for signal in ACTIVE_SIGNALS_V3:
        if has_early_reversion_v3_trade(conn, market.slug, signal.strategy_name):
            continue

        _, ask = _side_prices(quotes, signal.side)
        if ask is None or ask > signal.entry_threshold:
            continue

        insert_early_reversion_v3_trade(
            conn,
            market_slug=market.slug,
            window_start_ts=market.window_start_ts,
            end_ts=market.end_ts,
            side=signal.side,
            strategy_name=signal.strategy_name,
            entry_price=ask,
            entry_ts=now_ts,
        )
        logger.info(
            "Early Reversion v3 %s | %s | BUY %s @ %.3f",
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

    for trade in get_open_early_reversion_v3_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            bid = trade["last_bid"]
        if bid is None:
            bid = float(trade["entry_price"])

        entry_price = float(trade["entry_price"])
        entry_ts = int(trade["entry_ts"])
        max_price_seen = max(float(trade["max_price_seen"] or entry_price), float(bid))
        seconds_in_trade = now_ts - entry_ts

        decision = _resolve_exit_decision(
            entry_price=entry_price,
            bid=float(bid),
            max_price_seen=max_price_seen,
            seconds_in_trade=seconds_in_trade,
        ) or ExitDecision(reason="TIME_STOP")

        _close_trade(conn, trade, bid=float(bid), decision=decision, now_ts=now_ts)


def close_due_early_reversion_v3_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    due = conn.execute(
        """
        SELECT * FROM early_reversion_v3_trades
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
        max_price_seen = max(float(trade["max_price_seen"] or entry_price), float(bid))
        seconds_in_trade = now_ts - entry_ts

        decision = _resolve_exit_decision(
            entry_price=entry_price,
            bid=float(bid),
            max_price_seen=max_price_seen,
            seconds_in_trade=seconds_in_trade,
        ) or ExitDecision(reason="TIME_STOP")

        _close_trade(conn, trade, bid=float(bid), decision=decision, now_ts=now_ts)

    return len(due)


def process_early_reversion_v3(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
) -> None:
    now_ts = int(time.time())
    seconds_open = _seconds_since_open(market)

    if seconds_open <= ER_V3_ENTRY_WINDOW_SEC:
        _try_open_signals(conn, market, quotes, now_ts)

    for trade in get_open_early_reversion_v3_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            continue
        _manage_open_trade(conn, trade, bid=bid, now_ts=now_ts)

    _close_expired_trades(conn, market, quotes, now_ts)
