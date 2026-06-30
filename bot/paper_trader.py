"""Virtual trade execution and settlement."""

from __future__ import annotations

import logging
import sqlite3
import time

from bot.btc_price import BtcPriceError, get_current_btc_price
from bot.config import TRADE_SIZE_USDC
from bot.database import (
    get_open_trades,
    get_open_trades_due_for_settlement,
    has_open_trade_for_market,
    insert_virtual_trade,
    settle_virtual_trade,
)
from bot.execution import EntryOrder, attempt_entry_open
from bot.market_scanner import Btc5mMarket
from bot.strategy import SignalSide, StrategySignal

logger = logging.getLogger(__name__)


def _winning_side(btc_price: float, strike_price: float) -> str:
    """YES wins when BTC finishes at or above strike (Up market rule)."""
    return "YES" if btc_price >= strike_price else "NO"


def _current_delta(btc_price: float, strike_price: float) -> float:
    return btc_price - strike_price


def track_delta_for_open_trades(conn: sqlite3.Connection, btc_price: float) -> None:
    """
    Update adverse excursion delta for open trades.

    YES: keep minimum delta (worst pullback toward/below strike).
    NO: keep maximum delta (worst pullback toward/above strike).
    """
    for trade in get_open_trades(conn):
        delta = _current_delta(btc_price, float(trade["strike_price"]))
        trade_id = trade["id"]

        if trade["side"] == "YES":
            prev = trade["min_delta_after_entry"]
            new_min = delta if prev is None else min(float(prev), delta)
            conn.execute(
                "UPDATE virtual_trades SET min_delta_after_entry = ? WHERE id = ?",
                (new_min, trade_id),
            )
        else:
            prev = trade["max_delta_after_entry"]
            new_max = delta if prev is None else max(float(prev), delta)
            conn.execute(
                "UPDATE virtual_trades SET max_delta_after_entry = ? WHERE id = ?",
                (new_max, trade_id),
            )


def record_virtual_trade(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    signal: StrategySignal,
) -> int | None:
    """Persist a paper trade if one has not already been taken for this market."""
    if has_open_trade_for_market(conn, market.slug):
        logger.info("Paper trade already exists for %s — skipping", market.slug)
        return None

    if signal.side == SignalSide.YES:
        token_id = market.yes_token_id
        entry_ask = market.yes_quotes.ask
        entry_bid = market.yes_quotes.bid
        side = "YES"
    else:
        token_id = market.no_token_id
        entry_ask = market.no_quotes.ask
        entry_bid = market.no_quotes.bid
        side = "NO"

    if entry_ask is None or entry_ask <= 0:
        logger.warning("No valid ask for %s on %s — cannot paper trade", side, market.slug)
        return None

    shares = round(TRADE_SIZE_USDC / entry_ask, 4)
    trade = {
        "market_slug": market.slug,
        "condition_id": market.condition_id,
        "window_start_ts": market.window_start_ts,
        "end_ts": market.end_ts,
        "strike_price": signal.strike_price,
        "side": side,
        "token_id": token_id,
        "entry_ask": entry_ask,
        "entry_bid": entry_bid,
        "btc_price_at_entry": signal.btc_price,
        "btc_delta_at_entry": signal.delta_usd,
        "seconds_remaining": signal.seconds_remaining,
        "size_usdc": TRADE_SIZE_USDC,
        "shares": shares,
    }

    def _insert() -> int:
        return insert_virtual_trade(conn, trade)

    logger.info(
        "ENTRY CREATED | strategy=%s side=%s price=%s size_usdc=%s",
        f"LATE_{side}",
        side,
        entry_ask,
        TRADE_SIZE_USDC,
    )
    opened = attempt_entry_open(
        conn,
        EntryOrder(
            strategy_version="late_window",
            strategy_name=f"LATE_{side}",
            market_slug=market.slug,
            side=side,
            token_id=token_id,
            price=entry_ask,
            size_usdc=TRADE_SIZE_USDC,
        ),
        insert_trade=_insert,
    )
    if not opened:
        return None

    row = conn.execute(
        "SELECT id FROM virtual_trades WHERE market_slug = ? AND side = ?",
        (market.slug, side),
    ).fetchone()
    trade_id = int(row["id"])
    logger.info(
        "Paper trade #%s | %s | %s @ %.4f | BTC %.2f vs strike %.2f (Δ %.2f)",
        trade_id,
        market.slug,
        side,
        entry_ask,
        signal.btc_price,
        signal.strike_price,
        signal.delta_usd,
    )
    return trade_id


def settle_due_trades(conn: sqlite3.Connection, btc_price: float | None = None) -> int:
    """Settle open paper trades whose markets have ended."""
    now_ts = int(time.time())
    due_trades = get_open_trades_due_for_settlement(conn, now_ts)
    if not due_trades:
        return 0

    try:
        settlement_btc = btc_price if btc_price is not None else get_current_btc_price()
    except BtcPriceError as exc:
        logger.error("Cannot settle trades: %s", exc)
        return 0

    track_delta_for_open_trades(conn, settlement_btc)
    settled_count = 0

    for trade in due_trades:
        winner = _winning_side(settlement_btc, trade["strike_price"])
        won = trade["side"] == winner
        payout = float(trade["shares"]) if won else 0.0
        pnl = payout - float(trade["size_usdc"])
        outcome = "win" if won else "loss"

        distance_at_close = abs(settlement_btc - float(trade["strike_price"]))
        settle_virtual_trade(
            conn,
            trade["id"],
            outcome=outcome,
            market_end_price=settlement_btc,
            strike_price=float(trade["strike_price"]),
            payout_usdc=payout,
            pnl_usdc=pnl,
        )
        settled_count += 1
        logger.info(
            "Settled trade #%s | %s | %s | end BTC %.2f | distance %.2f | PnL %.4f USDC",
            trade["id"],
            trade["market_slug"],
            outcome.upper(),
            settlement_btc,
            distance_at_close,
            pnl,
        )

    return settled_count
