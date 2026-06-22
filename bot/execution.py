"""Order execution with paper, dry_run, and live trading modes."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from bot.clob_client import get_authenticated_clob_client
from bot.config import (
    TRADING_MODE,
    is_live_exit_enabled,
    is_live_trading_enabled,
    is_paper_mode,
)
from bot.database import (
    get_order_intent,
    has_order_intent,
    insert_order_intent,
    update_order_intent_status,
)
from bot.risk import check_can_open_position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntryOrder:
    strategy_version: str
    strategy_name: str
    market_slug: str
    side: str
    token_id: str
    price: float
    size_usdc: float


@dataclass(frozen=True)
class ExitOrder:
    strategy_version: str
    strategy_name: str
    market_slug: str
    side: str
    token_id: str
    price: float
    shares: float
    exit_reason: str


def build_entry_idempotency_key(order: EntryOrder) -> str:
    return (
        f"{order.strategy_version}:{order.strategy_name}:"
        f"{order.market_slug}:{order.side}:entry"
    )


def build_exit_idempotency_key(order: ExitOrder) -> str:
    return (
        f"{order.strategy_version}:{order.strategy_name}:"
        f"{order.market_slug}:{order.side}:exit"
    )


def build_idempotency_key(order: EntryOrder) -> str:
    return build_entry_idempotency_key(order)


def format_dry_run_log(order: EntryOrder) -> str:
    shares = order.size_usdc / order.price if order.price > 0 else 0.0
    return (
        f"[DRY_RUN] BUY {order.strategy_name} {order.side} @ {order.price:.2f} "
        f"size={order.size_usdc:.2f} USDC shares={shares:.4f} "
        f"market={order.market_slug} token={order.token_id}"
    )


def format_dry_run_exit_log(order: ExitOrder) -> str:
    return (
        f"[DRY_RUN] SELL {order.strategy_name} {order.side} @ {order.price:.2f} "
        f"shares={order.shares:.4f} reason={order.exit_reason} "
        f"market={order.market_slug} token={order.token_id}"
    )


def _shares_for_size(size_usdc: float, price: float) -> float:
    if price <= 0:
        raise ValueError(f"Invalid price: {price}")
    return size_usdc / price


def resolve_entry_shares_and_token(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    market_slug: str,
    side: str,
    entry_price: float,
    size_usdc: float,
) -> tuple[str | None, float]:
    entry_key = (
        f"{strategy_version}:{strategy_name}:{market_slug}:{side}:entry"
    )
    row = get_order_intent(conn, entry_key)
    if row is not None:
        return row["token_id"], float(row["shares"])
    return None, _shares_for_size(size_usdc, entry_price)


def _submit_live_buy(order: EntryOrder) -> tuple[bool, str | None, str | None]:
    shares = _shares_for_size(order.size_usdc, order.price)
    client = get_authenticated_clob_client()
    order_args = OrderArgs(
        token_id=order.token_id,
        price=order.price,
        size=shares,
        side=BUY,
    )
    response = client.create_and_post_order(order_args, OrderType.GTC)
    order_id = None
    if isinstance(response, dict):
        order_id = response.get("orderID") or response.get("id")
    return True, str(order_id) if order_id else None, None


def _submit_live_sell(order: ExitOrder) -> tuple[bool, str | None, str | None]:
    client = get_authenticated_clob_client()
    order_args = OrderArgs(
        token_id=order.token_id,
        price=order.price,
        size=order.shares,
        side=SELL,
    )
    response = client.create_and_post_order(order_args, OrderType.GTC)
    order_id = None
    if isinstance(response, dict):
        order_id = response.get("orderID") or response.get("id")
    return True, str(order_id) if order_id else None, None


def attempt_entry_open(
    conn: sqlite3.Connection,
    order: EntryOrder,
    insert_trade: Callable[[], int],
) -> bool:
    """
    Gate a new position open with risk checks and idempotency.

    Returns True when a new paper/live position record was created.
    """
    risk = check_can_open_position(conn)
    if not risk.allowed:
        logger.info(
            "Entry blocked for %s %s | %s",
            order.strategy_version,
            order.strategy_name,
            risk.reason,
        )
        return False

    idempotency_key = build_entry_idempotency_key(order)
    shares = _shares_for_size(order.size_usdc, order.price)

    if is_paper_mode():
        if has_order_intent(conn, idempotency_key):
            logger.debug("Duplicate entry skipped (paper intent): %s", idempotency_key)
            return False
        try:
            insert_order_intent(
                conn,
                idempotency_key=idempotency_key,
                trading_mode=TRADING_MODE,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                market_slug=order.market_slug,
                side=order.side,
                token_id=order.token_id,
                price=order.price,
                size_usdc=order.size_usdc,
                shares=shares,
                status="paper",
            )
            insert_trade()
            return True
        except sqlite3.IntegrityError:
            logger.debug("Duplicate entry skipped (paper): %s", idempotency_key)
            return False

    if has_order_intent(conn, idempotency_key):
        logger.info(
            "Duplicate entry skipped (idempotency): %s",
            idempotency_key,
        )
        return False

    try:
        insert_order_intent(
            conn,
            idempotency_key=idempotency_key,
            trading_mode=TRADING_MODE,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            side=order.side,
            token_id=order.token_id,
            price=order.price,
            size_usdc=order.size_usdc,
            shares=shares,
            status="pending",
        )
    except sqlite3.IntegrityError:
        logger.info(
            "Duplicate entry skipped (race): %s",
            idempotency_key,
        )
        return False

    if TRADING_MODE == "dry_run":
        logger.info(format_dry_run_log(order))
        update_order_intent_status(conn, idempotency_key, status="dry_run")
        try:
            insert_trade()
            return True
        except sqlite3.IntegrityError:
            logger.debug("Duplicate paper record after dry_run: %s", idempotency_key)
            return False

    if is_live_trading_enabled():
        try:
            ok, clob_order_id, error = _submit_live_buy(order)
            if not ok:
                update_order_intent_status(
                    conn,
                    idempotency_key,
                    status="failed",
                    error_message=error or "unknown error",
                )
                logger.error(
                    "Live entry failed for %s: %s",
                    order.strategy_name,
                    error,
                )
                return False
            update_order_intent_status(
                conn,
                idempotency_key,
                status="submitted",
                clob_order_id=clob_order_id,
            )
            logger.info(
                "Live entry submitted | %s %s @ %.4f | order_id=%s",
                order.strategy_name,
                order.side,
                order.price,
                clob_order_id or "unknown",
            )
            try:
                insert_trade()
                return True
            except sqlite3.IntegrityError:
                logger.warning(
                    "Live entry submitted but paper record exists: %s",
                    idempotency_key,
                )
                return False
        except Exception as exc:
            update_order_intent_status(
                conn,
                idempotency_key,
                status="failed",
                error_message=str(exc),
            )
            logger.exception(
                "Live entry exception for %s %s",
                order.strategy_name,
                order.market_slug,
            )
            return False

    return False


def attempt_exit_close(
    conn: sqlite3.Connection,
    order: ExitOrder,
    close_trade: Callable[[], None],
) -> bool:
    """
    Close a position with optional live CLOB sell.

    Returns True when the position was closed in the database.
    """
    if not is_live_exit_enabled():
        close_trade()
        return True

    idempotency_key = build_exit_idempotency_key(order)
    existing = get_order_intent(conn, idempotency_key)
    if existing is not None:
        status = existing["status"]
        if status in ("submitted", "dry_run"):
            logger.info(
                "Exit recovery: finalizing DB close for %s (status=%s)",
                idempotency_key,
                status,
            )
            close_trade()
            return True
        logger.info(
            "Duplicate exit skipped (idempotency): %s status=%s",
            idempotency_key,
            status,
        )
        return False

    if is_paper_mode():
        close_trade()
        return True

    size_usdc = order.price * order.shares
    try:
        insert_order_intent(
            conn,
            idempotency_key=idempotency_key,
            trading_mode=TRADING_MODE,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            side=order.side,
            token_id=order.token_id,
            price=order.price,
            size_usdc=size_usdc,
            shares=order.shares,
            status="pending",
        )
    except sqlite3.IntegrityError:
        logger.info("Duplicate exit skipped (race): %s", idempotency_key)
        return False

    if TRADING_MODE == "dry_run":
        logger.info(format_dry_run_exit_log(order))
        update_order_intent_status(conn, idempotency_key, status="dry_run")
        close_trade()
        return True

    if is_live_trading_enabled():
        try:
            ok, clob_order_id, error = _submit_live_sell(order)
            if not ok:
                update_order_intent_status(
                    conn,
                    idempotency_key,
                    status="failed",
                    error_message=error or "unknown error",
                )
                logger.error(
                    "Live exit failed for %s %s: %s",
                    order.strategy_name,
                    order.exit_reason,
                    error,
                )
                return False
            update_order_intent_status(
                conn,
                idempotency_key,
                status="submitted",
                clob_order_id=clob_order_id,
            )
            logger.info(
                "Live exit submitted | %s %s %s @ %.4f | shares=%.4f | order_id=%s",
                order.strategy_name,
                order.side,
                order.exit_reason,
                order.price,
                order.shares,
                clob_order_id or "unknown",
            )
            close_trade()
            return True
        except Exception as exc:
            update_order_intent_status(
                conn,
                idempotency_key,
                status="failed",
                error_message=str(exc),
            )
            logger.exception(
                "Live exit exception for %s %s",
                order.strategy_name,
                order.market_slug,
            )
            return False

    return False


def close_early_reversion_position(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    trade: sqlite3.Row,
    token_id: str | None,
    bid: float,
    exit_reason: str,
    size_usdc: float,
    close_trade: Callable[[], None],
) -> bool:
    entry_price = float(trade["entry_price"])
    resolved_token, shares = resolve_entry_shares_and_token(
        conn,
        strategy_version=strategy_version,
        strategy_name=trade["strategy_name"],
        market_slug=trade["market_slug"],
        side=trade["side"],
        entry_price=entry_price,
        size_usdc=size_usdc,
    )
    effective_token = token_id or resolved_token
    if effective_token is None:
        logger.warning(
            "No token_id for %s exit on %s — using paper close",
            strategy_version,
            trade["market_slug"],
        )
        close_trade()
        return True

    exit_order = ExitOrder(
        strategy_version=strategy_version,
        strategy_name=trade["strategy_name"],
        market_slug=trade["market_slug"],
        side=trade["side"],
        token_id=effective_token,
        price=bid,
        shares=shares,
        exit_reason=exit_reason,
    )
    return attempt_exit_close(conn, exit_order, close_trade)
