"""Audit logging for Early Reversion order execution (read-only diagnostics)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC

logger = logging.getLogger(__name__)

QUOTE_KEYS = ("yes_bid", "yes_ask", "no_bid", "no_ask")


@dataclass(frozen=True)
class EntryAuditContext:
    quotes: dict[str, float | None]
    selected_price: float
    yes_token_id: str | None = None
    no_token_id: str | None = None


@dataclass(frozen=True)
class ExitAuditContext:
    quotes: dict[str, float | None]
    selected_price: float
    entry_price: float
    yes_token_id: str | None = None
    no_token_id: str | None = None


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}"


def _side_book_prices(
    quotes: dict[str, float | None],
    side: str,
) -> tuple[float | None, float | None]:
    prefix = side.lower()
    return quotes.get(f"{prefix}_bid"), quotes.get(f"{prefix}_ask")


def _expected_token_id(
    side: str,
    *,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> str | None:
    if side == "YES":
        return yes_token_id
    if side == "NO":
        return no_token_id
    return None


def _validate_side_token(
    *,
    side: str,
    token_id: str,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> list[str]:
    expected = _expected_token_id(
        side,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )
    if expected is None or not token_id:
        return []
    if token_id != expected:
        return [
            f"token_id mismatch for {side}: got={token_id} expected={expected}",
        ]
    return []


def _validate_entry_price(
    *,
    side: str,
    quotes: dict[str, float | None],
    selected_price: float,
    submitted_price: float,
) -> list[str]:
    warnings: list[str] = []
    bid, ask = _side_book_prices(quotes, side)
    if ask is not None and abs(selected_price - ask) > 1e-6:
        warnings.append(
            f"selected_price {selected_price:.4f} != {side} ask {ask:.4f} (buy must use ask)",
        )
    if bid is not None and abs(selected_price - bid) < 1e-6:
        warnings.append(
            f"selected_price matches {side} bid {bid:.4f} but entry should use ask",
        )
    opposite = "NO" if side == "YES" else "YES"
    opp_bid, opp_ask = _side_book_prices(quotes, opposite)
    if opp_ask is not None and abs(selected_price - opp_ask) < 1e-6:
        warnings.append(f"selected_price matches opposite token {opposite} ask {opp_ask:.4f}")
    if opp_bid is not None and abs(selected_price - opp_bid) < 1e-6:
        warnings.append(f"selected_price matches opposite token {opposite} bid {opp_bid:.4f}")
    if abs(selected_price - submitted_price) > 1e-6:
        warnings.append(
            f"selected_price {selected_price:.4f} != submitted_price {submitted_price:.4f}",
        )
    return warnings


def _validate_exit_price(
    *,
    side: str,
    quotes: dict[str, float | None],
    selected_price: float,
    submitted_price: float,
) -> list[str]:
    warnings: list[str] = []
    bid, ask = _side_book_prices(quotes, side)
    if bid is not None and abs(selected_price - bid) > 1e-6:
        warnings.append(
            f"selected_price {selected_price:.4f} != {side} bid {bid:.4f} (sell must use bid)",
        )
    if ask is not None and abs(selected_price - ask) < 1e-6:
        warnings.append(
            f"selected_price matches {side} ask {ask:.4f} but exit should use bid",
        )
    opposite = "NO" if side == "YES" else "YES"
    opp_bid, opp_ask = _side_book_prices(quotes, opposite)
    if opp_bid is not None and abs(selected_price - opp_bid) < 1e-6:
        warnings.append(f"selected_price matches opposite token {opposite} bid {opp_bid:.4f}")
    if opp_ask is not None and abs(selected_price - opp_ask) < 1e-6:
        warnings.append(f"selected_price matches opposite token {opposite} ask {opp_ask:.4f}")
    if abs(selected_price - submitted_price) > 1e-6:
        warnings.append(
            f"selected_price {selected_price:.4f} != submitted_price {submitted_price:.4f}",
        )
    return warnings


def _log_warnings(prefix: str, warnings: list[str]) -> None:
    for warning in warnings:
        logger.warning("%s | %s", prefix, warning)


def log_entry_audit(
    *,
    strategy_name: str,
    side: str,
    token_id: str,
    shares: float,
    selected_price: float,
    submitted_price: float,
    quotes: dict[str, float | None] | None,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
) -> None:
    quote = quotes or {}
    logger.info(
        "ENTRY AUDIT\n"
        "strategy=%s\n"
        "token=%s\n"
        "shares=%.4f\n"
        "quote:\n"
        "NO bid=%s\n"
        "NO ask=%s\n"
        "YES bid=%s\n"
        "YES ask=%s\n"
        "selected_price=%s\n"
        "submitted_price=%s",
        strategy_name,
        side,
        shares,
        _fmt_price(quote.get("no_bid")),
        _fmt_price(quote.get("no_ask")),
        _fmt_price(quote.get("yes_bid")),
        _fmt_price(quote.get("yes_ask")),
        _fmt_price(selected_price),
        _fmt_price(submitted_price),
    )
    _log_warnings(
        "ENTRY AUDIT",
        _validate_side_token(
            side=side,
            token_id=token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        ),
    )
    if quotes is not None:
        _log_warnings(
            "ENTRY AUDIT",
            _validate_entry_price(
                side=side,
                quotes=quotes,
                selected_price=selected_price,
                submitted_price=submitted_price,
            ),
        )


def log_exit_audit(
    *,
    strategy_name: str,
    side: str,
    token_id: str,
    selected_price: float,
    submitted_price: float,
    entry_price: float,
    exit_price: float,
    quotes: dict[str, float | None] | None,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
) -> None:
    quote = quotes or {}
    logger.info(
        "EXIT AUDIT\n"
        "strategy=%s\n"
        "token=%s\n"
        "quote:\n"
        "NO bid=%s\n"
        "NO ask=%s\n"
        "YES bid=%s\n"
        "YES ask=%s\n"
        "selected_price=%s\n"
        "submitted_price=%s\n"
        "entry_price=%s\n"
        "exit_price=%s",
        strategy_name,
        side,
        _fmt_price(quote.get("no_bid")),
        _fmt_price(quote.get("no_ask")),
        _fmt_price(quote.get("yes_bid")),
        _fmt_price(quote.get("yes_ask")),
        _fmt_price(selected_price),
        _fmt_price(submitted_price),
        _fmt_price(entry_price),
        _fmt_price(exit_price),
    )
    _log_warnings(
        "EXIT AUDIT",
        _validate_side_token(
            side=side,
            token_id=token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        ),
    )
    if quotes is not None:
        _log_warnings(
            "EXIT AUDIT",
            _validate_exit_price(
                side=side,
                quotes=quotes,
                selected_price=selected_price,
                submitted_price=submitted_price,
            ),
        )


def log_order_args(
    *,
    clob_side: str,
    token_id: str,
    price: float,
    shares: float,
    quotes: dict[str, float | None] | None = None,
    trade_side: str | None = None,
) -> None:
    notional = price * shares
    logger.info(
        "ORDER ARGS\n"
        "side=%s\n"
        "token_id=%s\n"
        "price=%.6f\n"
        "shares=%.4f\n"
        "notional=%.4f",
        clob_side,
        token_id,
        price,
        shares,
        notional,
    )
    if quotes is not None and trade_side is not None:
        bid, ask = _side_book_prices(quotes, trade_side)
        logger.info(
            "ORDER ARGS quote cross-check | trade_side=%s bid=%s ask=%s submitted=%.6f",
            trade_side,
            _fmt_price(bid),
            _fmt_price(ask),
            price,
        )


def _lookup_submitted_entry(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    market_slug: str,
    side: str,
) -> tuple[float | None, float | None]:
    key = f"{strategy_version}:{strategy_name}:{market_slug}:{side}:entry"
    row = conn.execute(
        "SELECT price, shares FROM order_intents WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None, None
    return float(row["price"]), float(row["shares"])


def compute_trade_pnl(
    *,
    entry_price: float,
    exit_price: float,
    shares: float,
) -> tuple[float, float, float, float]:
    """
    PnL without fees: (exit - entry) * shares.

    Percent is relative to entry cost (entry * shares).
    """
    entry_cost = entry_price * shares
    exit_value = exit_price * shares
    realized_profit_usdc = exit_value - entry_cost
    if entry_cost > 0:
        realized_profit_pct = (exit_price - entry_price) / entry_price * 100
    else:
        realized_profit_pct = 0.0
    return entry_cost, exit_value, realized_profit_usdc, realized_profit_pct


def log_trade_audit(
    *,
    entry_price: float,
    exit_price: float,
    submitted_entry: float | None,
    submitted_exit: float,
    shares: float,
    exit_reason: str,
) -> None:
    entry_cost, exit_value, realized_usdc, realized_pct = compute_trade_pnl(
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
    )
    logger.info(
        "TRADE AUDIT\n"
        "entry_price=%.6f\n"
        "exit_price=%.6f\n"
        "submitted_entry=%s\n"
        "submitted_exit=%.6f\n"
        "shares=%.4f\n"
        "entry_cost=%.6f\n"
        "exit_value=%.6f\n"
        "realized_profit_usdc=%+.6f\n"
        "realized_profit_pct=%+.4f\n"
        "exit_reason=%s\n"
        "pnl_formula=(exit_price - entry_price) * shares; fees not included",
        entry_price,
        exit_price,
        "-" if submitted_entry is None else f"{submitted_entry:.6f}",
        submitted_exit,
        shares,
        entry_cost,
        exit_value,
        realized_usdc,
        realized_pct,
        exit_reason,
    )


def log_trade_audit_for_exit(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    market_slug: str,
    side: str,
    entry_price: float,
    exit_price: float,
    shares: float,
    exit_reason: str,
    submitted_exit: float,
) -> None:
    submitted_entry, intent_shares = _lookup_submitted_entry(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        market_slug=market_slug,
        side=side,
    )
    effective_shares = intent_shares if intent_shares is not None else shares
    log_trade_audit(
        entry_price=entry_price,
        exit_price=exit_price,
        submitted_entry=submitted_entry,
        submitted_exit=submitted_exit,
        shares=effective_shares,
        exit_reason=exit_reason,
    )


def log_attempt_entry_result(
    *,
    strategy_name: str,
    market_slug: str,
    success: bool,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    lines = [
        "ATTEMPT ENTRY RESULT",
        "",
        f"strategy={strategy_name}",
        "",
        f"market={market_slug}",
        "",
        f"result={'SUCCESS' if success else 'FAILED'}",
    ]
    if not success and reason:
        lines.extend(["", f"reason={reason}"])
        if detail:
            lines.extend(["", f"detail={detail}"])
    logger.info("\n".join(lines))


def entry_trace_stop_reason(
    result_reason: str | None,
    *,
    context: str = "",
) -> str:
    if result_reason == "duplicate":
        if context == "idempotency":
            return "idempotency"
        if context == "existing_position":
            return "existing_position"
        return "duplicate"
    if result_reason in ("max_open_positions", "daily_loss", "risk"):
        return "max_open_positions"
    if result_reason == "clob_error":
        return "clob_error"
    if result_reason == "exception":
        return "exception"
    if result_reason == "other":
        return "live_mode"
    return result_reason or "exception"


def log_entry_trace_start(
    *,
    strategy_name: str,
    market_slug: str,
    price: float,
    shares: float,
) -> None:
    logger.info(
        "\n".join(
            [
                "ENTRY TRACE START",
                "",
                f"strategy={strategy_name}",
                "",
                f"market={market_slug}",
                "",
                f"price={price}",
                "",
                f"shares={shares}",
            ]
        )
    )


def log_entry_trace_stop(reason: str) -> None:
    logger.info("\n".join(["ENTRY TRACE STOP", "", f"reason={reason}"]))


def log_entry_trace_submit() -> None:
    logger.info("ENTRY TRACE SUBMIT")


def log_entry_trace_success() -> None:
    logger.info("ENTRY TRACE SUCCESS")
