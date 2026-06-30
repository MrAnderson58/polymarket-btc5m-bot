"""Order execution with paper, dry_run, and live trading modes."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from py_clob_client_v2 import OrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import BUY, SELL

from bot.clob_client import get_authenticated_clob_client
from bot.config import (
    CHAIN_ID,
    POLY_PROXY_WALLET,
    POLY_SIGNATURE_TYPE,
    TRADING_MODE,
    WINDOW_SECONDS,
    is_live_exit_enabled,
    is_live_trading_enabled,
    is_paper_mode,
)
from bot.database import (
    clear_order_intent_error,
    get_order_intent,
    has_early_reversion_v25_trade,
    has_early_reversion_v2_trade,
    has_early_reversion_v3_trade,
    has_order_intent,
    insert_early_reversion_v25_trade,
    insert_early_reversion_v2_trade,
    insert_early_reversion_v3_trade,
    insert_order_intent,
    update_order_intent_status,
)
from bot.execution_audit import (
    EntryAuditContext,
    ExitAuditContext,
    entry_trace_stop_reason,
    log_attempt_entry_result,
    log_entry_audit,
    log_entry_trace_start,
    log_entry_trace_stop,
    log_entry_trace_submit,
    log_entry_trace_success,
    log_exit_audit,
    log_order_args,
    log_trade_audit_for_exit,
)
from bot.risk import check_can_open_position

logger = logging.getLogger(__name__)

_logged_exit_idempotency_skips: set[str] = set()

UNRECORDED_TRADE_PREFIX = "unrecorded_trade:"
MIN_ORDER_SHARES = 5.0
MAX_LIVE_ORDER_SIZE_USDC = 2.5


def _mark_entry_opened(conn: sqlite3.Connection, order: EntryOrder) -> None:
    from bot.er_stats import record_entry_success

    record_entry_success(conn, order.strategy_version, order.strategy_name)


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


def _log_duplicate_exit_skip(idempotency_key: str, status: str) -> None:
    if idempotency_key in _logged_exit_idempotency_skips:
        logger.debug(
            "Duplicate exit skipped (idempotency): %s status=%s",
            idempotency_key,
            status,
        )
        return
    _logged_exit_idempotency_skips.add(idempotency_key)
    logger.info(
        "Duplicate exit skipped (idempotency): %s status=%s — "
        "trade stays open; only submitted/dry_run intents recover on retry",
        idempotency_key,
        status,
    )


def format_dry_run_log(order: EntryOrder) -> str:
    shares = _shares_for_size(order.size_usdc, order.price) if order.price > 0 else 0.0
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
    requested_shares = size_usdc / price
    shares = max(MIN_ORDER_SHARES, requested_shares)
    if shares > requested_shares:
        adjusted_notional = shares * price
        logger.info(
            "ORDER SIZE ADJUSTED | requested_usdc=%.1f requested_shares=%.3f "
            "adjusted_shares=%.0f adjusted_notional=%.2f",
            size_usdc,
            requested_shares,
            shares,
            adjusted_notional,
        )
    return shares


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


def resolve_exit_token_id(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    market_slug: str,
    side: str,
    entry_price: float,
    size_usdc: float,
    hint_token_id: str | None = None,
) -> tuple[str | None, float]:
    """
    Resolve the CLOB token_id for a live exit.

    Priority: entry order_intent (same token as live BUY) -> Gamma by market_slug
    -> caller hint (active market token).
    """
    entry_token, shares = resolve_entry_shares_and_token(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        market_slug=market_slug,
        side=side,
        entry_price=entry_price,
        size_usdc=size_usdc,
    )
    if entry_token:
        if hint_token_id and hint_token_id != entry_token:
            logger.warning(
                "Exit token hint %s ignored; using entry intent token %s for %s %s",
                hint_token_id,
                entry_token,
                market_slug,
                side,
            )
        return entry_token, shares

    from bot.market_scanner import get_token_id_for_market_slug_and_side

    gamma_token = get_token_id_for_market_slug_and_side(market_slug, side)
    if gamma_token:
        logger.info(
            "Exit token resolved from Gamma for %s %s: %s",
            market_slug,
            side,
            gamma_token,
        )
        return gamma_token, shares

    if hint_token_id:
        logger.warning(
            "Exit token fallback to active-market hint %s for %s %s",
            hint_token_id,
            market_slug,
            side,
        )
        return hint_token_id, shares

    return None, shares


def _submit_live_buy(
    order: EntryOrder,
    *,
    quotes: dict[str, float | None] | None = None,
) -> tuple[bool, str | None, str | None]:
    if order.size_usdc > MAX_LIVE_ORDER_SIZE_USDC:
        raise RuntimeError(
            "Safety check failed: refusing to submit order larger than "
            f"{MAX_LIVE_ORDER_SIZE_USDC} USDC (got {order.size_usdc})"
        )

    shares = _shares_for_size(order.size_usdc, order.price)
    notional_usdc = shares * order.price
    logger.info(
        "LIVE BUY SIZE CHECK\nconfigured_size_usdc=%.2f\nactual_notional=%.4f\nshares=%.4f",
        order.size_usdc,
        notional_usdc,
        shares,
    )
    logger.info(
        "LIVE BUY | token=%s price=%.4f shares=%.4f cost=%.4f USDC",
        order.token_id,
        order.price,
        shares,
        notional_usdc,
    )
    client = get_authenticated_clob_client()
    order_args = OrderArgs(
        token_id=order.token_id,
        price=order.price,
        size=shares,
        side=BUY,
    )
    log_order_args(
        clob_side="BUY",
        token_id=order.token_id,
        price=order.price,
        shares=shares,
        quotes=quotes,
        trade_side=order.side,
    )
    logger.info(
        "Live buy pre-submit | token_id=%s price=%.6f shares=%.6f "
        "client_chain_id=%s client_signature_type=%s "
        "config_chain_id=%s config_signature_type=%s config_proxy_wallet=%s",
        order.token_id,
        order.price,
        shares,
        client.chain_id,
        client.builder.signature_type,
        CHAIN_ID,
        POLY_SIGNATURE_TYPE,
        POLY_PROXY_WALLET,
    )
    response = client.create_and_post_order(order_args, order_type=OrderType.GTC)
    order_id = None
    if isinstance(response, dict):
        order_id = response.get("orderID") or response.get("id")
    return True, str(order_id) if order_id else None, None


def _submit_live_sell(
    order: ExitOrder,
    *,
    quotes: dict[str, float | None] | None = None,
) -> tuple[bool, str | None, str | None]:
    log_order_args(
        clob_side="SELL",
        token_id=order.token_id,
        price=order.price,
        shares=order.shares,
        quotes=quotes,
        trade_side=order.side,
    )
    client = get_authenticated_clob_client()
    order_args = OrderArgs(
        token_id=order.token_id,
        price=order.price,
        size=order.shares,
        side=SELL,
    )
    try:
        response = client.create_and_post_order(order_args, order_type=OrderType.GTC)
    except Exception as exc:
        from bot.market_scanner import is_invalid_token_error

        if is_invalid_token_error(exc):
            return False, None, str(exc)
        raise
    order_id = None
    if isinstance(response, dict):
        order_id = response.get("orderID") or response.get("id")
    return True, str(order_id) if order_id else None, None


def _window_times_from_slug(market_slug: str) -> tuple[int, int]:
    window_start = int(market_slug.rsplit("-", 1)[-1])
    return window_start, window_start + WINDOW_SECONDS


def _trade_exists_for_intent(conn: sqlite3.Connection, intent: sqlite3.Row) -> bool:
    version = intent["strategy_version"]
    market_slug = intent["market_slug"]
    strategy_name = intent["strategy_name"]
    if version == "v2":
        return has_early_reversion_v2_trade(conn, market_slug, strategy_name)
    if version == "v2.5":
        return has_early_reversion_v25_trade(conn, market_slug, strategy_name)
    if version == "v3":
        return has_early_reversion_v3_trade(conn, market_slug, strategy_name)
    return False


def _mark_entry_unrecorded(
    conn: sqlite3.Connection,
    idempotency_key: str,
    exc: Exception,
) -> None:
    update_order_intent_status(
        conn,
        idempotency_key,
        status="submitted",
        error_message=f"{UNRECORDED_TRADE_PREFIX}{exc}",
    )


def _clear_entry_unrecorded(conn: sqlite3.Connection, idempotency_key: str) -> None:
    clear_order_intent_error(conn, idempotency_key)


def _insert_trade_from_intent(conn: sqlite3.Connection, intent: sqlite3.Row) -> int:
    window_start, end_ts = _window_times_from_slug(intent["market_slug"])
    entry_ts = int(time.time())
    common = {
        "conn": conn,
        "market_slug": intent["market_slug"],
        "window_start_ts": window_start,
        "end_ts": end_ts,
        "side": intent["side"],
        "strategy_name": intent["strategy_name"],
        "entry_price": float(intent["price"]),
        "entry_ts": entry_ts,
    }
    version = intent["strategy_version"]
    if version == "v2":
        return insert_early_reversion_v2_trade(**common)
    if version == "v2.5":
        return insert_early_reversion_v25_trade(**common)
    if version == "v3":
        return insert_early_reversion_v3_trade(**common)
    raise ValueError(f"unsupported strategy_version for recovery: {version}")


def reconcile_unrecorded_entry_intents(conn: sqlite3.Connection) -> int:
    """Backfill SQLite trades for live CLOB entries that were submitted but not recorded."""
    rows = conn.execute(
        """
        SELECT * FROM order_intents
        WHERE status = 'submitted'
          AND idempotency_key LIKE '%:entry'
          AND error_message LIKE ?
        ORDER BY id ASC
        """,
        (f"{UNRECORDED_TRADE_PREFIX}%",),
    ).fetchall()

    recovered = 0
    for intent in rows:
        key = intent["idempotency_key"]
        if _trade_exists_for_intent(conn, intent):
            _clear_entry_unrecorded(conn, key)
            recovered += 1
            logger.info("Entry intent already has trade record: %s", key)
            continue
        try:
            trade_id = _insert_trade_from_intent(conn, intent)
            _clear_entry_unrecorded(conn, key)
            recovered += 1
            logger.critical(
                "Recovered unrecorded live entry | %s | trade_id=%s | clob_order_id=%s",
                key,
                trade_id,
                intent["clob_order_id"],
            )
        except Exception as exc:
            logger.exception(
                "Failed to recover unrecorded live entry %s: %s",
                key,
                exc,
            )
    return recovered


def attempt_entry_open(
    conn: sqlite3.Connection,
    order: EntryOrder,
    insert_trade: Callable[[], int],
    *,
    entry_audit: EntryAuditContext | None = None,
) -> bool:
    """
    Gate a new position open with risk checks and idempotency.

    Returns True when a new paper/live position record was created.
    """
    from bot.er_pipeline import (
        STOP_DAILY_LOSS,
        STOP_DUPLICATE,
        STOP_LIVE_MODE,
        STOP_MAX_OPEN,
        STOP_OTHER,
        STOP_UNKNOWN,
        log_pipeline,
        pipeline_stop,
        stop_reason_for_block,
    )
    from bot.er_stats import (
        BLOCK_REASON_MAX_DAILY_LOSS,
        BLOCK_REASON_MAX_OPEN_POSITIONS,
        classify_block_reason,
        record_entry_attempt,
    )

    def _result(
        success: bool,
        reason: str | None = None,
        detail: str | None = None,
        *,
        trace_context: str = "",
    ) -> bool:
        if not success:
            log_entry_trace_stop(
                entry_trace_stop_reason(reason, context=trace_context)
            )
        log_attempt_entry_result(
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            success=success,
            reason=reason,
            detail=detail,
        )
        return success

    def _risk_failure_reason() -> str:
        block_code = classify_block_reason(risk.reason)
        if block_code == BLOCK_REASON_MAX_OPEN_POSITIONS:
            return "max_open_positions"
        if block_code == BLOCK_REASON_MAX_DAILY_LOSS:
            return "daily_loss"
        return "risk"

    shares = _shares_for_size(order.size_usdc, order.price)
    log_entry_trace_start(
        strategy_name=order.strategy_name,
        market_slug=order.market_slug,
        price=order.price,
        shares=shares,
    )

    log_pipeline(12, strategy=order.strategy_name)
    risk = check_can_open_position(conn)
    if not risk.allowed:
        stop_reason = stop_reason_for_block(classify_block_reason(risk.reason))
        pipeline_stop(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            reason=stop_reason,
            detail=risk.reason,
        )
        logger.info(
            "Entry blocked for %s %s | %s",
            order.strategy_version,
            order.strategy_name,
            risk.reason,
        )
        return _result(False, reason=_risk_failure_reason(), detail=risk.reason)

    record_entry_attempt(conn, order.strategy_version, order.strategy_name)

    idempotency_key = build_entry_idempotency_key(order)
    selected_price = entry_audit.selected_price if entry_audit else order.price
    log_entry_audit(
        strategy_name=order.strategy_name,
        side=order.side,
        token_id=order.token_id,
        shares=shares,
        selected_price=selected_price,
        submitted_price=order.price,
        quotes=entry_audit.quotes if entry_audit else None,
        yes_token_id=entry_audit.yes_token_id if entry_audit else None,
        no_token_id=entry_audit.no_token_id if entry_audit else None,
    )

    if is_paper_mode():
        if has_order_intent(conn, idempotency_key):
            logger.debug("Duplicate entry skipped (paper intent): %s", idempotency_key)
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_DUPLICATE,
                detail=idempotency_key,
            )
            return _result(False, reason="duplicate", detail=idempotency_key)
        try:
            log_entry_trace_submit()
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
            _mark_entry_opened(conn, order)
            log_entry_trace_success()
            return _result(True)
        except sqlite3.IntegrityError:
            logger.debug("Duplicate entry skipped (paper): %s", idempotency_key)
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_DUPLICATE,
                detail=idempotency_key,
            )
            return _result(
                False,
                reason="duplicate",
                detail=idempotency_key,
                trace_context="existing_position",
            )

    if has_order_intent(conn, idempotency_key):
        logger.info(
            "Duplicate entry skipped (idempotency): %s",
            idempotency_key,
        )
        pipeline_stop(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            reason=STOP_DUPLICATE,
            detail=idempotency_key,
        )
        return _result(False, reason="duplicate", detail=idempotency_key)

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
        pipeline_stop(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            reason=STOP_DUPLICATE,
            detail=idempotency_key,
        )
        return _result(False, reason="duplicate", detail=idempotency_key, trace_context="idempotency")

    if TRADING_MODE == "dry_run":
        logger.info(format_dry_run_log(order))
        update_order_intent_status(conn, idempotency_key, status="dry_run")
        try:
            log_entry_trace_submit()
            insert_trade()
            _mark_entry_opened(conn, order)
            log_entry_trace_success()
            return _result(True)
        except sqlite3.IntegrityError:
            logger.debug("Duplicate paper record after dry_run: %s", idempotency_key)
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_DUPLICATE,
                detail=idempotency_key,
            )
            return _result(
                False,
                reason="duplicate",
                detail=idempotency_key,
                trace_context="existing_position",
            )

    if is_live_trading_enabled():
        logger.info(
            "ENTRY SUBMIT | strategy=%s side=%s price=%s size_usdc=%s",
            order.strategy_name,
            order.side,
            order.price,
            order.size_usdc,
        )
        log_entry_trace_submit()
        try:
            ok, clob_order_id, error = _submit_live_buy(
                order,
                quotes=entry_audit.quotes if entry_audit else None,
            )
        except Exception as exc:
            update_order_intent_status(
                conn,
                idempotency_key,
                status="failed",
                error_message=str(exc),
            )
            logger.exception(
                "Live entry CLOB exception for %s %s",
                order.strategy_name,
                order.market_slug,
            )
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_LIVE_MODE,
                detail=str(exc),
            )
            return _result(False, reason="exception", detail=str(exc))

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
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_LIVE_MODE,
                detail=error or "unknown error",
            )
            return _result(False, reason="clob_error", detail=error or "unknown error")

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
            _mark_entry_opened(conn, order)
            log_entry_trace_success()
            return _result(True)
        except sqlite3.IntegrityError:
            if _trade_exists_for_intent(
                conn,
                get_order_intent(conn, idempotency_key),
            ):
                logger.warning(
                    "Live entry submitted; trade record already exists: %s",
                    idempotency_key,
                )
                _mark_entry_opened(conn, order)
                log_entry_trace_success()
                return _result(True)
            _mark_entry_unrecorded(
                conn,
                idempotency_key,
                sqlite3.IntegrityError("trade insert integrity error"),
            )
            logger.critical(
                "ORPHAN LIVE ENTRY | CLOB order submitted but trade insert failed | %s | order_id=%s",
                idempotency_key,
                clob_order_id,
            )
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_LIVE_MODE,
                detail=idempotency_key,
            )
            return _result(False, reason="exception", detail=idempotency_key)
        except Exception as exc:
            _mark_entry_unrecorded(conn, idempotency_key, exc)
            logger.critical(
                "ORPHAN LIVE ENTRY | CLOB order submitted but trade insert failed | %s | order_id=%s | %s",
                idempotency_key,
                clob_order_id,
                exc,
            )
            pipeline_stop(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                reason=STOP_LIVE_MODE,
                detail=str(exc),
            )
            return _result(False, reason="exception", detail=str(exc))

    pipeline_stop(
        conn,
        strategy_version=order.strategy_version,
        strategy_name=order.strategy_name,
        reason=STOP_OTHER,
    )
    return _result(False, reason="other")


def attempt_exit_close(
    conn: sqlite3.Connection,
    order: ExitOrder,
    close_trade: Callable[[], None],
    *,
    exit_audit: ExitAuditContext | None = None,
) -> bool:
    """
    Close a position with optional live CLOB sell.

    Returns True when the position was closed in the database.
    """
    if not is_live_exit_enabled():
        if exit_audit is not None:
            log_exit_audit(
                strategy_name=order.strategy_name,
                side=order.side,
                token_id=order.token_id,
                selected_price=exit_audit.selected_price,
                submitted_price=order.price,
                entry_price=exit_audit.entry_price,
                exit_price=order.price,
                quotes=exit_audit.quotes,
                yes_token_id=exit_audit.yes_token_id,
                no_token_id=exit_audit.no_token_id,
            )
        close_trade()
        log_trade_audit_for_exit(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            side=order.side,
            entry_price=exit_audit.entry_price if exit_audit else order.price,
            exit_price=order.price,
            shares=order.shares,
            exit_reason=order.exit_reason,
            submitted_exit=order.price,
        )
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
        _log_duplicate_exit_skip(idempotency_key, status)
        return False

    if is_paper_mode():
        if exit_audit is not None:
            log_exit_audit(
                strategy_name=order.strategy_name,
                side=order.side,
                token_id=order.token_id,
                selected_price=exit_audit.selected_price,
                submitted_price=order.price,
                entry_price=exit_audit.entry_price,
                exit_price=order.price,
                quotes=exit_audit.quotes,
                yes_token_id=exit_audit.yes_token_id,
                no_token_id=exit_audit.no_token_id,
            )
        close_trade()
        log_trade_audit_for_exit(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            side=order.side,
            entry_price=exit_audit.entry_price if exit_audit else order.price,
            exit_price=order.price,
            shares=order.shares,
            exit_reason=order.exit_reason,
            submitted_exit=order.price,
        )
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
        if exit_audit is not None:
            log_exit_audit(
                strategy_name=order.strategy_name,
                side=order.side,
                token_id=order.token_id,
                selected_price=exit_audit.selected_price,
                submitted_price=order.price,
                entry_price=exit_audit.entry_price,
                exit_price=order.price,
                quotes=exit_audit.quotes,
                yes_token_id=exit_audit.yes_token_id,
                no_token_id=exit_audit.no_token_id,
            )
        update_order_intent_status(conn, idempotency_key, status="dry_run")
        close_trade()
        log_trade_audit_for_exit(
            conn,
            strategy_version=order.strategy_version,
            strategy_name=order.strategy_name,
            market_slug=order.market_slug,
            side=order.side,
            entry_price=exit_audit.entry_price if exit_audit else order.price,
            exit_price=order.price,
            shares=order.shares,
            exit_reason=order.exit_reason,
            submitted_exit=order.price,
        )
        return True

    if is_live_trading_enabled():
        if exit_audit is not None:
            log_exit_audit(
                strategy_name=order.strategy_name,
                side=order.side,
                token_id=order.token_id,
                selected_price=exit_audit.selected_price,
                submitted_price=order.price,
                entry_price=exit_audit.entry_price,
                exit_price=order.price,
                quotes=exit_audit.quotes,
                yes_token_id=exit_audit.yes_token_id,
                no_token_id=exit_audit.no_token_id,
            )
        from bot.market_scanner import is_exit_token_tradable

        if not is_exit_token_tradable(order.token_id):
            error_msg = "exit blocked: token has no CLOB orderbook"
            update_order_intent_status(
                conn,
                idempotency_key,
                status="failed",
                error_message=error_msg,
            )
            logger.warning(
                "Live exit skipped for %s %s — %s (token_id=%s)",
                order.strategy_name,
                order.market_slug,
                error_msg,
                order.token_id,
            )
            from bot.exit_recovery import reconcile_failed_exit_for_order

            reconcile_failed_exit_for_order(conn, order)
            return False
        try:
            ok, clob_order_id, error = _submit_live_sell(
                order,
                quotes=exit_audit.quotes if exit_audit else None,
            )
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
                from bot.exit_recovery import reconcile_failed_exit_for_order

                reconcile_failed_exit_for_order(conn, order)
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
            log_trade_audit_for_exit(
                conn,
                strategy_version=order.strategy_version,
                strategy_name=order.strategy_name,
                market_slug=order.market_slug,
                side=order.side,
                entry_price=exit_audit.entry_price if exit_audit else order.price,
                exit_price=order.price,
                shares=order.shares,
                exit_reason=order.exit_reason,
                submitted_exit=order.price,
            )
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
            from bot.exit_recovery import reconcile_failed_exit_for_order

            reconcile_failed_exit_for_order(conn, order)
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
    quotes: dict[str, float | None] | None = None,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
) -> bool:
    entry_price = float(trade["entry_price"])
    effective_token, shares = resolve_exit_token_id(
        conn,
        strategy_version=strategy_version,
        strategy_name=trade["strategy_name"],
        market_slug=trade["market_slug"],
        side=trade["side"],
        entry_price=entry_price,
        size_usdc=size_usdc,
        hint_token_id=token_id,
    )
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
    exit_audit = ExitAuditContext(
        quotes=quotes or {},
        selected_price=bid,
        entry_price=entry_price,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )
    return attempt_exit_close(conn, exit_order, close_trade, exit_audit=exit_audit)
