"""Reconcile open trades stuck after failed live exit attempts."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from bot.clob_client import wallet_configured
from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    is_live_exit_enabled,
    is_live_trading_enabled,
)
from bot.database import (
    close_early_reversion_v25_trade,
    close_early_reversion_v2_trade,
    close_early_reversion_v3_trade,
    delete_order_intent,
    get_order_intent,
)
from bot.execution import EntryOrder, ExitOrder, build_entry_idempotency_key, build_exit_idempotency_key
from bot.market_scanner import (
    get_token_id_for_market_slug_and_side,
    is_exit_token_tradable,
    is_permanent_exit_error_message,
)

logger = logging.getLogger(__name__)

USDC_DECIMALS = 1_000_000
RECOVERY_MIN_SHARES = 0.01
EXIT_REASON_NO_POSITION = "RECOVERY_NO_POSITION"

_RECOVERY_WARNING_MESSAGE = """RECOVERY WARNING

Trade closed in SQLite.

Market expired.

Token is not tradable on CLOB.

Wallet may still contain redeemable shares.

Manual redeem may be required."""

_OPEN_ER_TABLES: tuple[tuple[str, str], ...] = (
    ("v2", "early_reversion_v2_trades"),
    ("v2.5", "early_reversion_v25_trades"),
    ("v3", "early_reversion_v3_trades"),
)

_CLOSE_BY_VERSION = {
    "v2": close_early_reversion_v2_trade,
    "v2.5": close_early_reversion_v25_trade,
    "v3": close_early_reversion_v3_trade,
}


@dataclass(frozen=True)
class StuckExitRow:
    strategy_version: str
    trade_id: int
    strategy_name: str
    market_slug: str
    side: str
    entry_price: float
    entry_ts: int
    end_ts: int
    last_bid: float | None
    exit_key: str
    exit_status: str
    exit_error: str | None
    token_id: str | None
    shares: float


def is_transient_exit_error_message(error: str | None) -> bool:
    """Return True when a failed exit may succeed on retry."""
    if not error or is_permanent_exit_error_message(error):
        return False
    text = error.lower()
    transient_markers = (
        "timeout",
        "timed out",
        "request exception",
        "connection reset",
        "connection error",
        "rate limit",
        "503",
        "502",
        "504",
        "not enough balance",
        "allowance",
    )
    return any(marker in text for marker in transient_markers)


def get_conditional_token_balance_shares(token_id: str) -> float | None:
    """Return wallet balance for a conditional token, or None if unavailable."""
    if not wallet_configured() or not is_live_trading_enabled():
        return None
    try:
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        from bot.clob_client import get_authenticated_clob_client

        client = get_authenticated_clob_client()
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id,
        )
        client.update_balance_allowance(params)
        response = client.get_balance_allowance(params)
        if not isinstance(response, dict):
            return None
        raw_balance = response.get("balance")
        if raw_balance in (None, ""):
            return 0.0
        return int(str(raw_balance)) / USDC_DECIMALS
    except Exception as exc:
        logger.warning(
            "Exit recovery: could not fetch CLOB balance for token %s: %s",
            token_id,
            exc,
        )
        return None


def _pnl_usdc(entry_price: float, exit_price: float) -> float:
    shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
    return shares * (exit_price - entry_price)


def _is_market_expired(end_ts: int, now_ts: int) -> bool:
    return end_ts < now_ts


def _token_is_permanently_untradable(
    *,
    exit_error: str | None,
    token_id: str | None,
) -> bool:
    if exit_error and is_permanent_exit_error_message(exit_error):
        return True
    if token_id and not is_exit_token_tradable(token_id):
        return True
    return False


def _should_close_expired_untradable(
    *,
    end_ts: int,
    now_ts: int,
    exit_error: str | None,
    token_id: str | None,
) -> bool:
    """
    Close SQLite only when the market has ended and CLOB exit cannot be retried.

    All must hold:
    - market expired (end_ts < now)
    - token permanently untradable (invalid / no orderbook)
    - error is not a transient API failure (or token check confirms no orderbook)
    """
    if not _is_market_expired(end_ts, now_ts):
        return False
    if exit_error and is_transient_exit_error_message(exit_error):
        return False
    return _token_is_permanently_untradable(exit_error=exit_error, token_id=token_id)


def _log_recovery_warning(
    row: StuckExitRow,
    *,
    token_id: str | None,
    balance: float | None,
) -> None:
    lines = [_RECOVERY_WARNING_MESSAGE]
    if balance is not None and balance >= RECOVERY_MIN_SHARES:
        lines.extend(["", f"wallet_balance={balance:.4f}"])
    lines.extend(
        [
            "",
            f"trade_id={row.trade_id}",
            f"market_slug={row.market_slug}",
            f"exit_key={row.exit_key}",
            f"token_id={token_id}",
            f"error={row.exit_error or 'token not tradable on CLOB'}",
        ]
    )
    logger.warning("\n".join(lines))


def _fetch_stuck_exit_rows(conn: sqlite3.Connection) -> list[StuckExitRow]:
    rows: list[StuckExitRow] = []
    for strategy_version, table in _OPEN_ER_TABLES:
        query_rows = conn.execute(
            f"""
            SELECT t.id AS trade_id,
                   t.strategy_name,
                   t.market_slug,
                   t.side,
                   t.entry_price,
                   t.entry_ts,
                   t.end_ts,
                   t.last_bid,
                   o.idempotency_key AS exit_key,
                   o.status AS exit_status,
                   o.error_message AS exit_error,
                   o.token_id,
                   o.shares
            FROM {table} t
            INNER JOIN order_intents o
              ON o.idempotency_key = ?
                 || t.strategy_name || ':'
                 || t.market_slug || ':'
                 || t.side || ':exit'
            WHERE t.status = 'open'
              AND o.status IN ('failed', 'pending')
            """,
            (f"{strategy_version}:",),
        ).fetchall()
        for row in query_rows:
            rows.append(
                StuckExitRow(
                    strategy_version=strategy_version,
                    trade_id=int(row["trade_id"]),
                    strategy_name=row["strategy_name"],
                    market_slug=row["market_slug"],
                    side=row["side"],
                    entry_price=float(row["entry_price"]),
                    entry_ts=int(row["entry_ts"]),
                    end_ts=int(row["end_ts"]),
                    last_bid=None if row["last_bid"] is None else float(row["last_bid"]),
                    exit_key=row["exit_key"],
                    exit_status=row["exit_status"],
                    exit_error=row["exit_error"],
                    token_id=row["token_id"],
                    shares=float(row["shares"] or 0.0),
                )
            )
    return rows


def _resolve_token_and_shares(
    conn: sqlite3.Connection,
    row: StuckExitRow,
) -> tuple[str | None, float]:
    entry_key = build_entry_idempotency_key(
        EntryOrder(
            strategy_version=row.strategy_version,
            strategy_name=row.strategy_name,
            market_slug=row.market_slug,
            side=row.side,
            token_id=row.token_id or "",
            price=row.entry_price,
            size_usdc=EARLY_REVERSION_POSITION_SIZE_USDC,
        )
    )
    entry = get_order_intent(conn, entry_key)
    entry_token = entry["token_id"] if entry is not None else None

    token_id = entry_token or row.token_id
    shares = row.shares
    if entry is not None and entry["shares"] is not None:
        if shares <= 0:
            shares = float(entry["shares"])
    if not token_id:
        token_id = get_token_id_for_market_slug_and_side(row.market_slug, row.side)
    return token_id, shares


def _force_close_open_trade(
    conn: sqlite3.Connection,
    row: StuckExitRow,
    *,
    exit_price: float,
    exit_reason: str,
    now_ts: int,
) -> None:
    close_fn = _CLOSE_BY_VERSION[row.strategy_version]
    holding_time = float(max(0, now_ts - row.entry_ts))
    pnl_percent = (exit_price - row.entry_price) / row.entry_price * 100
    close_fn(
        conn,
        row.trade_id,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_percent=pnl_percent,
        pnl_usdc=_pnl_usdc(row.entry_price, exit_price),
        holding_time_seconds=holding_time,
    )


def _exit_price_for_recovery(row: StuckExitRow) -> float:
    return row.last_bid if row.last_bid is not None else row.entry_price


def _close_recovered_trade(
    conn: sqlite3.Connection,
    row: StuckExitRow,
    *,
    exit_price: float,
    now_ts: int,
    token_id: str | None = None,
    balance: float | None = None,
    log_recovery: bool = False,
    log_message: str | None = None,
    log_args: tuple = (),
) -> bool:
    """Close an open trade in SQLite and remove its exit intent."""
    _force_close_open_trade(
        conn,
        row,
        exit_price=exit_price,
        exit_reason=EXIT_REASON_NO_POSITION,
        now_ts=now_ts,
    )
    delete_order_intent(conn, row.exit_key)
    from bot.recovery_health import EVENT_RECOVERY_ACTION, record_health_event

    record_health_event(conn, EVENT_RECOVERY_ACTION, event_ts=now_ts)
    if log_recovery:
        _log_recovery_warning(row, token_id=token_id, balance=balance)
    elif log_message is not None:
        logger.warning(log_message, *log_args)
    return True


def _close_expired_untradable_position(
    conn: sqlite3.Connection,
    row: StuckExitRow,
    *,
    token_id: str | None,
    balance: float | None,
    now_ts: int,
) -> bool:
    """Close SQLite for expired markets with permanently untradable tokens."""
    return _close_recovered_trade(
        conn,
        row,
        exit_price=_exit_price_for_recovery(row),
        now_ts=now_ts,
        token_id=token_id,
        balance=balance,
        log_recovery=True,
    )


def _reconcile_stuck_row(
    conn: sqlite3.Connection,
    row: StuckExitRow,
    *,
    now_ts: int,
) -> bool:
    token_id, shares = _resolve_token_and_shares(conn, row)
    if not is_live_exit_enabled():
        return False

    balance: float | None = None
    if token_id and is_live_trading_enabled():
        balance = get_conditional_token_balance_shares(token_id)

    if balance is not None and balance < RECOVERY_MIN_SHARES:
        return _close_recovered_trade(
            conn,
            row,
            exit_price=_exit_price_for_recovery(row),
            now_ts=now_ts,
            log_message=(
                "Exit recovery: closed open trade %s (%s) — no CLOB token balance "
                "(balance=%.4f, exit_status=%s, error=%s)"
            ),
            log_args=(
                row.trade_id,
                row.exit_key,
                balance,
                row.exit_status,
                row.exit_error,
            ),
        )

    if row.exit_status == "failed":
        if _should_close_expired_untradable(
            end_ts=row.end_ts,
            now_ts=now_ts,
            exit_error=row.exit_error,
            token_id=token_id,
        ):
            return _close_expired_untradable_position(
                conn,
                row,
                token_id=token_id,
                balance=balance,
                now_ts=now_ts,
            )
        if is_permanent_exit_error_message(row.exit_error):
            logger.warning(
                "Exit recovery: permanent exit error on active market — "
                "keeping open trade for retry (trade_id=%s, end_ts=%s, now=%s, error=%s)",
                row.trade_id,
                row.end_ts,
                now_ts,
                row.exit_error,
            )
            return False
        delete_order_intent(conn, row.exit_key)
        logger.info(
            "Exit recovery: cleared failed exit intent %s for retry "
            "(balance=%s, shares=%.4f, error=%s)",
            row.exit_key,
            "-" if balance is None else f"{balance:.4f}",
            shares,
            row.exit_error,
        )
        return True

    return False


def _reconcile_expired_open_trades(
    conn: sqlite3.Connection,
    *,
    now_ts: int,
    stuck_exit_keys: set[str],
) -> int:
    """
    Close expired open trades that cannot be exited on CLOB.

    Covers cases where close_due failed before an exit intent was recorded, or the
    intent is absent while the market window has already ended.
    """
    if not is_live_exit_enabled():
        return 0

    reconciled = 0
    for strategy_version, table in _OPEN_ER_TABLES:
        rows = conn.execute(
            f"""
            SELECT id, strategy_name, market_slug, side, entry_price, entry_ts,
                   end_ts, last_bid
            FROM {table}
            WHERE status = 'open' AND end_ts < ?
            ORDER BY end_ts ASC
            """,
            (now_ts,),
        ).fetchall()
        for trade in rows:
            exit_key = build_exit_idempotency_key(
                ExitOrder(
                    strategy_version=strategy_version,
                    strategy_name=trade["strategy_name"],
                    market_slug=trade["market_slug"],
                    side=trade["side"],
                    token_id="",
                    price=0.0,
                    shares=0.0,
                    exit_reason="TIME_STOP",
                )
            )
            if exit_key in stuck_exit_keys:
                continue

            intent = get_order_intent(conn, exit_key)
            if intent is not None and intent["status"] in (
                "failed",
                "pending",
                "submitted",
            ):
                continue

            row = StuckExitRow(
                strategy_version=strategy_version,
                trade_id=int(trade["id"]),
                strategy_name=trade["strategy_name"],
                market_slug=trade["market_slug"],
                side=trade["side"],
                entry_price=float(trade["entry_price"]),
                entry_ts=int(trade["entry_ts"]),
                end_ts=int(trade["end_ts"]),
                last_bid=None if trade["last_bid"] is None else float(trade["last_bid"]),
                exit_key=exit_key,
                exit_status="none",
                exit_error=None,
                token_id=None,
                shares=0.0,
            )
            token_id, _ = _resolve_token_and_shares(conn, row)
            balance: float | None = None
            if token_id and is_live_trading_enabled():
                balance = get_conditional_token_balance_shares(token_id)

            if balance is not None and balance < RECOVERY_MIN_SHARES:
                if _close_recovered_trade(
                    conn,
                    row,
                    exit_price=_exit_price_for_recovery(row),
                    now_ts=now_ts,
                    log_message=(
                        "Exit recovery: closed expired open trade %s (%s) — "
                        "no CLOB token balance (balance=%.4f)"
                    ),
                    log_args=(row.trade_id, row.exit_key, balance),
                ):
                    reconciled += 1
                continue

            if _should_close_expired_untradable(
                end_ts=row.end_ts,
                now_ts=now_ts,
                exit_error=None,
                token_id=token_id,
            ):
                if _close_expired_untradable_position(
                    conn,
                    row,
                    token_id=token_id,
                    balance=balance,
                    now_ts=now_ts,
                ):
                    reconciled += 1

    return reconciled


def reconcile_stuck_exits(conn: sqlite3.Connection, now_ts: int | None = None) -> int:
    """
    Reconcile open ER trades with failed/pending exit intents.

    - No CLOB token balance -> close trade in DB and remove exit intent.
    - Expired market + permanently untradable token -> close trade in DB.
    - Transient failed exits with tokens still held -> remove exit intent for retry.
    """
    if now_ts is None:
        now_ts = int(time.time())
    if not is_live_exit_enabled():
        return 0

    stuck_rows = _fetch_stuck_exit_rows(conn)
    stuck_exit_keys = {row.exit_key for row in stuck_rows}
    reconciled = 0
    for row in stuck_rows:
        if _reconcile_stuck_row(conn, row, now_ts=now_ts):
            reconciled += 1
    reconciled += _reconcile_expired_open_trades(
        conn,
        now_ts=now_ts,
        stuck_exit_keys=stuck_exit_keys,
    )
    return reconciled


def reconcile_failed_exit_for_order(
    conn: sqlite3.Connection,
    order: ExitOrder,
) -> bool:
    """Run recovery immediately after a failed live exit attempt."""
    if not is_live_exit_enabled():
        return False

    exit_key = build_exit_idempotency_key(order)
    intent = get_order_intent(conn, exit_key)
    if intent is None or intent["status"] != "failed":
        return False

    table_name = dict(_OPEN_ER_TABLES).get(order.strategy_version)
    if table_name is None:
        return False
    trade = conn.execute(
        f"""
        SELECT id, entry_price, entry_ts, end_ts, last_bid
        FROM {table_name}
        WHERE market_slug = ? AND strategy_name = ? AND side = ? AND status = 'open'
        LIMIT 1
        """,
        (order.market_slug, order.strategy_name, order.side),
    ).fetchone()
    if trade is None:
        delete_order_intent(conn, exit_key)
        logger.info(
            "Exit recovery: cleared orphan failed exit intent %s (no open trade)",
            exit_key,
        )
        return True

    row = StuckExitRow(
        strategy_version=order.strategy_version,
        trade_id=int(trade["id"]),
        strategy_name=order.strategy_name,
        market_slug=order.market_slug,
        side=order.side,
        entry_price=float(trade["entry_price"]),
        entry_ts=int(trade["entry_ts"]),
        end_ts=int(trade["end_ts"]),
        last_bid=None if trade["last_bid"] is None else float(trade["last_bid"]),
        exit_key=exit_key,
        exit_status="failed",
        exit_error=intent["error_message"],
        token_id=order.token_id,
        shares=order.shares,
    )
    return _reconcile_stuck_row(conn, row, now_ts=int(time.time()))
