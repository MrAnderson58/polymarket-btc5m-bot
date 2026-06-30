"""Early Reversion v2.5 — V2 with 15s grace period."""

from __future__ import annotations

import logging
import sqlite3
import time

from bot.config import (
    EARLY_REVERSION_POSITION_SIZE_USDC,
    ENABLED_STRATEGIES_V25,
    ER_V2_ENTRY_WINDOW_SEC,
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    ER_V2_TRAILING_STOP_PCT,
    ER_V25_GRACE_PERIOD_SEC,
    effective_entry_threshold,
)
from bot.database import (
    close_early_reversion_v25_trade,
    get_open_early_reversion_v25_trades,
    has_early_reversion_v25_trade,
    insert_early_reversion_v25_trade,
    update_early_reversion_v25_trade_tracking,
)
from bot.early_reversion import SIGNALS
from bot.er_entry_check import (
    SignalEntryDiagnostic,
    evaluate_version_entries,
    log_version_entry_check,
)
from bot.er_pipeline import (
    STOP_ASK_CHANGED,
    STOP_ASK_MISSING,
    STOP_ENTRY_FAILED,
    STOP_HAS_TRADE,
    STOP_NO_ACTIVE_SIGNALS,
    STOP_OUTSIDE_WINDOW,
    diagnostic_passed_pre_risk,
    log_pipeline,
    log_pipeline_stop,
    log_try_open_skipped_outside_window,
    pipeline_stop,
    pipeline_stop_for_diagnostic,
    set_pipeline_log_context,
)
from bot.er_stats import record_exit
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
from bot.execution import (
    EntryAuditContext,
    EntryOrder,
    attempt_entry_open,
    close_early_reversion_position,
)
from bot.market_scanner import Btc5mMarket

logger = logging.getLogger(__name__)

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
    trailing_active: bool = False,
    highest_after_activation: float | None = None,
) -> ExitReason | None:
    return resolve_er_exit_reason(
        entry_price=entry_price,
        bid=bid,
        max_price_seen=max_price_seen,
        seconds_in_trade=seconds_in_trade,
        grace_sec=ER_V25_GRACE_PERIOD_SEC,
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
    update_early_reversion_v25_trade_tracking(
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
    trailing_tick: TrailingTickResult | None = None,
    quotes: dict[str, float | None] | None = None,
    market: Btc5mMarket | None = None,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time = now_ts - entry_ts

    def _finalize() -> None:
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
        record_exit(conn, "v2.5", trade["strategy_name"], exit_reason)
        close_early_reversion_v25_trade(
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
        quotes=quotes,
        yes_token_id=market.yes_token_id if market else None,
        no_token_id=market.no_token_id if market else None,
    )


def _manage_open_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    now_ts: int,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
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
        _close_trade(
            conn,
            trade,
            bid=bid,
            exit_reason=exit_reason,
            now_ts=now_ts,
            token_id=_token_id_for_side(market, trade["side"]),
            trailing_tick=tick,
            quotes=quotes,
            market=market,
        )
        return

    _persist_trailing_tracking(conn, trade["id"], bid=bid, tick=tick)


def _log_entry_diagnostics(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    *,
    seconds_open: float,
) -> tuple[str | None, list[SignalEntryDiagnostic]]:
    diagnostics = evaluate_version_entries(
        conn,
        strategy_version="v2.5",
        active_signals=ACTIVE_SIGNALS_V25,
        market_slug=market.slug,
        seconds_open=seconds_open,
        entry_window_sec=ER_V2_ENTRY_WINDOW_SEC,
        quotes=quotes,
        has_trade_fn=has_early_reversion_v25_trade,
    )
    return log_version_entry_check("V2.5", diagnostics), diagnostics


def _try_open_signals(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
    *,
    diagnostics: list[SignalEntryDiagnostic],
) -> None:
    log_pipeline(7, detail=f"active_signals={len(ACTIVE_SIGNALS_V25)}")
    if not ACTIVE_SIGNALS_V25:
        log_pipeline(8)
        log_pipeline_stop(STOP_NO_ACTIVE_SIGNALS)
        for diagnostic in diagnostics:
            if diagnostic_passed_pre_risk(diagnostic):
                pipeline_stop(
                    conn,
                    strategy_version="v2.5",
                    strategy_name=diagnostic.strategy_name,
                    reason=STOP_NO_ACTIVE_SIGNALS,
                )
        return

    diagnostic_by_name = {diagnostic.strategy_name: diagnostic for diagnostic in diagnostics}
    for signal in ACTIVE_SIGNALS_V25:
        log_pipeline(7, strategy=signal.strategy_name)
        diagnostic = diagnostic_by_name.get(signal.strategy_name)
        if has_early_reversion_v25_trade(conn, market.slug, signal.strategy_name):
            log_pipeline(9, strategy=signal.strategy_name)
            pipeline_stop_for_diagnostic(
                conn,
                strategy_version="v2.5",
                diagnostic=diagnostic,
                strategy_name=signal.strategy_name,
                reason=STOP_HAS_TRADE,
            )
            continue

        _, ask = _side_prices(quotes, signal.side)
        if ask is None:
            log_pipeline(10, strategy=signal.strategy_name, detail="ask=None")
            pipeline_stop_for_diagnostic(
                conn,
                strategy_version="v2.5",
                diagnostic=diagnostic,
                strategy_name=signal.strategy_name,
                reason=STOP_ASK_MISSING,
                detail="ask=None",
            )
            continue
        if ask > effective_entry_threshold(signal.entry_threshold):
            log_pipeline(10, strategy=signal.strategy_name, detail=f"ask={ask}")
            pipeline_stop_for_diagnostic(
                conn,
                strategy_version="v2.5",
                diagnostic=diagnostic,
                strategy_name=signal.strategy_name,
                reason=STOP_ASK_CHANGED,
                detail=(
                    f"ask={ask} threshold="
                    f"{effective_entry_threshold(signal.entry_threshold):.2f}"
                ),
            )
            continue

        token_id = market.yes_token_id if signal.side == "YES" else market.no_token_id
        log_pipeline(11, strategy=signal.strategy_name, detail=f"ask={ask}")
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
            entry_audit=EntryAuditContext(
                quotes=quotes,
                selected_price=ask,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id,
            ),
        )
        if not opened:
            pipeline_stop(
                conn,
                strategy_version="v2.5",
                strategy_name=signal.strategy_name,
                reason=STOP_ENTRY_FAILED,
                increment_counter=False,
            )
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
        _close_trade(
            conn,
            trade,
            bid=bid_f,
            exit_reason=exit_reason,
            now_ts=now_ts,
            token_id=_token_id_for_side(market, trade["side"]),
            trailing_tick=tick,
            quotes=quotes,
            market=market,
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
        _close_trade(
            conn,
            trade,
            bid=bid_f,
            exit_reason=exit_reason,
            now_ts=now_ts,
            trailing_tick=tick,
        )

    return len(due)


def process_early_reversion_v25(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
) -> str | None:
    now_ts = int(time.time())
    seconds_open = _seconds_since_open(market)
    set_pipeline_log_context(
        window_start_ts=market.window_start_ts,
        seconds_from_start=seconds_open,
    )

    signal_status, diagnostics = _log_entry_diagnostics(conn, market, quotes, seconds_open=seconds_open)

    if seconds_open <= ER_V2_ENTRY_WINDOW_SEC:
        log_pipeline(5, detail=f"seconds_open={seconds_open:.0f}")
        _try_open_signals(conn, market, quotes, now_ts, diagnostics=diagnostics)
    else:
        log_pipeline(6, detail=f"seconds_open={seconds_open:.0f}")
        log_pipeline_stop(STOP_OUTSIDE_WINDOW)
        log_try_open_skipped_outside_window(
            conn,
            strategy_version="v2.5",
            diagnostics=diagnostics,
        )

    for trade in get_open_early_reversion_v25_trades(conn, market_slug=market.slug):
        bid, _ = _side_prices(quotes, trade["side"])
        if bid is None:
            continue
        _manage_open_trade(
            conn,
            trade,
            bid=bid,
            now_ts=now_ts,
            market=market,
            quotes=quotes,
        )

    _close_expired_trades(conn, market, quotes, now_ts)
    return signal_status
