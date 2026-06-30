"""Early Reversion paper strategy — first 30 seconds of each BTC 5m market."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    EARLY_REVERSION_WINDOW_SEC,
    ENABLED_STRATEGIES,
    ER_ENTRY_PRICE_OFFSET,
    effective_entry_threshold,
)
from bot.database import (
    close_early_reversion_trade,
    get_early_reversion_market,
    get_open_early_reversion_trades,
    has_early_reversion_trade,
    insert_early_reversion_trade,
    upsert_early_reversion_market_prices,
)
from bot.execution import EntryOrder, attempt_entry_open
from bot.market_scanner import Btc5mMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarlyReversionSignal:
    strategy_name: str
    side: str
    entry_threshold: float
    target_price: float


SIGNALS: tuple[EarlyReversionSignal, ...] = (
    EarlyReversionSignal("YES_A", "YES", 0.30, 0.35),
    EarlyReversionSignal("YES_B", "YES", 0.35, 0.40),
    EarlyReversionSignal("YES_C", "YES", 0.40, 0.45),
    EarlyReversionSignal("NO_A", "NO", 0.30, 0.35),
    EarlyReversionSignal("NO_B", "NO", 0.35, 0.40),
    EarlyReversionSignal("NO_C", "NO", 0.40, 0.45),
)

ACTIVE_SIGNALS: tuple[EarlyReversionSignal, ...] = tuple(
    signal for signal in SIGNALS if signal.strategy_name in ENABLED_STRATEGIES
)


def log_er_entry_threshold_config() -> None:
    seen_thresholds: set[float] = set()
    for signal in ACTIVE_SIGNALS:
        if signal.entry_threshold in seen_thresholds:
            continue
        seen_thresholds.add(signal.entry_threshold)
        effective = effective_entry_threshold(signal.entry_threshold)
        logger.info(
            "Entry threshold: %.2f\nOffset: %+.2f\nEffective threshold: %.2f",
            signal.entry_threshold,
            ER_ENTRY_PRICE_OFFSET,
            effective,
        )


def _seconds_since_open(market: Btc5mMarket) -> float:
    return time.time() - market.window_start_ts


def _side_prices(quotes: dict[str, float | None], side: str) -> tuple[float | None, float | None]:
    prefix = side.lower()
    return quotes[f"{prefix}_bid"], quotes[f"{prefix}_ask"]


def _profit_percent(entry_price: float, bid: float) -> float:
    return (bid - entry_price) / entry_price * 100


def _pnl_percent(entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) / entry_price * 100


def _pnl_usdc(entry_price: float, exit_price: float) -> float:
    shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
    return shares * (exit_price - entry_price)


def _drawdown_percent(entry_price: float, bid: float) -> float:
    if bid >= entry_price:
        return 0.0
    return (entry_price - bid) / entry_price * 100


def _resolve_exit_price(
    trade: sqlite3.Row,
    *,
    reached_target: bool,
    bid: float | None,
) -> float:
    if reached_target:
        return float(trade["target_price"])

    if bid is not None:
        return bid

    last_bid = trade["last_bid"]
    if last_bid is not None:
        return float(last_bid)

    logger.warning(
        "No bid for %s on %s at close — using entry price as exit",
        trade["strategy_name"],
        trade["market_slug"],
    )
    return float(trade["entry_price"])


def _finalize_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    reached_target: bool,
    max_profit_percent: float,
    max_drawdown_percent: float,
    time_to_target_seconds: float | None,
    exit_price: float,
    close_ts: int,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time_seconds = close_ts - entry_ts

    close_early_reversion_trade(
        conn,
        trade["id"],
        reached_target=reached_target,
        max_profit_percent=max_profit_percent,
        max_drawdown_percent=max_drawdown_percent,
        time_to_target_seconds=time_to_target_seconds,
        exit_price=exit_price,
        pnl_percent=_pnl_percent(entry_price, exit_price),
        pnl_usdc=_pnl_usdc(entry_price, exit_price),
        holding_time_seconds=holding_time_seconds,
    )


def _update_open_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    now_ts: int,
) -> None:
    entry_price = float(trade["entry_price"])
    target_price = float(trade["target_price"])
    entry_ts = int(trade["entry_ts"])

    profit = _profit_percent(entry_price, bid)
    drawdown = _drawdown_percent(entry_price, bid)
    max_profit = max(float(trade["max_profit_percent"]), profit)
    max_drawdown = max(float(trade["max_drawdown_percent"]), drawdown)

    if bid >= target_price:
        time_to_target = now_ts - entry_ts
        _finalize_trade(
            conn,
            trade,
            reached_target=True,
            max_profit_percent=max_profit,
            max_drawdown_percent=max_drawdown,
            time_to_target_seconds=time_to_target,
            exit_price=target_price,
            close_ts=now_ts,
        )
        logger.info(
            "Early Reversion %s | %s | target %.2f hit | +%.2f%% in %ss",
            trade["strategy_name"],
            trade["market_slug"],
            target_price,
            _pnl_percent(entry_price, target_price),
            time_to_target,
        )
        return

    conn.execute(
        """
        UPDATE early_reversion_trades
        SET max_profit_percent = ?,
            max_drawdown_percent = ?,
            last_bid = ?
        WHERE id = ?
        """,
        (max_profit, max_drawdown, bid, trade["id"]),
    )


def _try_open_signals(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
) -> None:
    for signal in ACTIVE_SIGNALS:
        if has_early_reversion_trade(conn, market.slug, signal.strategy_name):
            continue

        bid, ask = _side_prices(quotes, signal.side)
        threshold = effective_entry_threshold(signal.entry_threshold)
        if ask is None or ask > threshold:
            continue

        token_id = market.yes_token_id if signal.side == "YES" else market.no_token_id
        opened = attempt_entry_open(
            conn,
            EntryOrder(
                strategy_version="v1",
                strategy_name=signal.strategy_name,
                market_slug=market.slug,
                side=signal.side,
                token_id=token_id,
                price=ask,
                size_usdc=EARLY_REVERSION_POSITION_SIZE_USDC,
            ),
            insert_trade=lambda: insert_early_reversion_trade(
                conn,
                market_slug=market.slug,
                window_start_ts=market.window_start_ts,
                end_ts=market.end_ts,
                side=signal.side,
                strategy_name=signal.strategy_name,
                entry_price=ask,
                target_price=signal.target_price,
                entry_ts=now_ts,
            ),
        )
        if not opened:
            continue
        logger.info(
            "Early Reversion %s | %s | BUY %s @ %.3f → TP %.3f",
            signal.strategy_name,
            market.slug,
            signal.side,
            ask,
            signal.target_price,
        )


def _close_expired_trades(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
) -> None:
    if now_ts < market.end_ts:
        return

    for trade in get_open_early_reversion_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        entry_price = float(trade["entry_price"])

        if bid is not None:
            profit = _profit_percent(entry_price, bid)
            drawdown = _drawdown_percent(entry_price, bid)
            max_profit = max(float(trade["max_profit_percent"]), profit)
            max_drawdown = max(float(trade["max_drawdown_percent"]), drawdown)
        else:
            max_profit = float(trade["max_profit_percent"])
            max_drawdown = float(trade["max_drawdown_percent"])

        exit_price = _resolve_exit_price(trade, reached_target=False, bid=bid)
        _finalize_trade(
            conn,
            trade,
            reached_target=False,
            max_profit_percent=max_profit,
            max_drawdown_percent=max_drawdown,
            time_to_target_seconds=None,
            exit_price=exit_price,
            close_ts=now_ts,
        )


def close_due_early_reversion_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    """Close open Early Reversion trades whose market has ended."""
    due = conn.execute(
        """
        SELECT * FROM early_reversion_trades
        WHERE status = 'open' AND end_ts <= ?
        ORDER BY end_ts ASC
        """,
        (now_ts,),
    ).fetchall()

    for trade in due:
        if trade["reached_target"]:
            exit_price = float(trade["target_price"])
            close_ts = int(trade["entry_ts"] + (trade["time_to_target_seconds"] or 0))
        else:
            exit_price = _resolve_exit_price(trade, reached_target=False, bid=None)
            close_ts = int(trade["end_ts"])

        _finalize_trade(
            conn,
            trade,
            reached_target=bool(trade["reached_target"]),
            max_profit_percent=float(trade["max_profit_percent"]),
            max_drawdown_percent=float(trade["max_drawdown_percent"]),
            time_to_target_seconds=trade["time_to_target_seconds"],
            exit_price=exit_price,
            close_ts=close_ts,
        )

    return len(due)


def process_early_reversion(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
) -> None:
    """Track first-30s prices, open signals, and manage open paper trades."""
    now_ts = int(time.time())
    seconds_open = _seconds_since_open(market)

    yes_ask = quotes.get("yes_ask")
    no_ask = quotes.get("no_ask")

    if seconds_open <= EARLY_REVERSION_WINDOW_SEC and (yes_ask is not None or no_ask is not None):
        existing = get_early_reversion_market(conn, market.slug)

        def _merge(
            current: float | None,
            previous: float | None,
            *,
            use_min: bool,
        ) -> float | None:
            if current is None:
                return previous
            if previous is None:
                return current
            return min(current, previous) if use_min else max(current, previous)

        min_yes = _merge(yes_ask, existing["min_yes_price"] if existing else None, use_min=True)
        max_yes = _merge(yes_ask, existing["max_yes_price"] if existing else None, use_min=False)
        min_no = _merge(no_ask, existing["min_no_price"] if existing else None, use_min=True)
        max_no = _merge(no_ask, existing["max_no_price"] if existing else None, use_min=False)

        upsert_early_reversion_market_prices(
            conn,
            market_slug=market.slug,
            window_start_ts=market.window_start_ts,
            min_yes_price=min_yes,
            max_yes_price=max_yes,
            min_no_price=min_no,
            max_no_price=max_no,
        )

        _try_open_signals(conn, market, quotes, now_ts)

    for trade in get_open_early_reversion_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            continue
        _update_open_trade(conn, trade, bid=bid, now_ts=now_ts)

    _close_expired_trades(conn, market, quotes, now_ts)
