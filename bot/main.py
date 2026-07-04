"""Main loop for Polymarket BTC 5m paper trading research."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from bot.btc_price import BtcPriceError, get_current_btc_price, get_strike_price
from bot.config import (
    ENABLE_LATE_WINDOW,
    ENABLE_V1,
    ENABLE_V2,
    ENABLE_V25,
    ENABLE_V3,
    ENABLE_V4_SHADOW,
    ENABLE_NO_C_FILTER_SHADOW,
    ENABLE_YES_C_SHADOW,
    ER_SUMMARY_INTERVAL_SEC,
    ER_V3_POLL_INTERVAL_SEC,
    V4_POLL_INTERVAL_SEC,
    ENABLED_STRATEGIES,
    ENABLED_STRATEGIES_V2,
    ENABLED_STRATEGIES_V25,
    ENABLED_STRATEGIES_V3,
    LIVE_EXIT_ENABLED,
    POLL_INTERVAL_SEC,
    TRADING_MODE,
    format_enabled_strategies,
)
from bot.database import connect, init_db, insert_market_check
from bot.market_scanner import find_active_btc_5m_market, get_best_bid_ask
from bot.early_reversion import close_due_early_reversion_trades, log_er_entry_threshold_config, process_early_reversion
from bot.no_c_btc_filter import log_no_c_filter_live_config
from bot.early_reversion_v2 import close_due_early_reversion_v2_trades, process_early_reversion_v2
from bot.early_reversion_v25 import close_due_early_reversion_v25_trades, process_early_reversion_v25
from bot.early_reversion_v3 import close_due_early_reversion_v3_trades, process_early_reversion_v3
from bot.v4.shadow_trade import close_due_v4_shadow_trades, process_v4_shadow
from bot.er_entry_check import format_cycle_signal_status
from bot.er_stats import log_er_summary, log_er_funnel_time_report
from bot.er_btc_direction_stats import log_btc_direction_summary
from bot.er_trailing_stats import log_trailing_summary
from bot.no_c_filter_shadow import log_no_c_filter_shadow_report
from bot.yes_c_shadow import close_due_yes_c_shadow_trades, process_yes_c_shadow
from bot.yes_c_shadow_stats import log_yes_c_shadow_reports
from bot.v4_shadow_stats import log_v4_shadow_report
from bot.exit_recovery import reconcile_stuck_exits
from bot.fill_audit import sync_pending_order_fills
from bot.paper_trader import record_virtual_trade, settle_due_trades, track_delta_for_open_trades
from bot.recovery import run_startup_recovery
from bot.strategy import evaluate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_cycle_signal_status = {"v2": "-", "v2.5": "-", "v3": "-"}
_last_logged_window: int | None = None


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log_effective_thresholds() -> None:
    from bot.config import ER_ENTRY_PRICE_OFFSET, effective_entry_threshold
    from bot.no_c_btc_filter import NO_C_NORMAL_THRESHOLD, NO_C_STRICT_THRESHOLD

    logger.info(
        "NO_C thresholds:\n"
        "  normal  base=%.2f  effective=%.2f\n"
        "  strict  base=%.2f  effective=%.2f\n"
        "  offset=%+.3f",
        NO_C_NORMAL_THRESHOLD,
        effective_entry_threshold(NO_C_NORMAL_THRESHOLD),
        NO_C_STRICT_THRESHOLD,
        effective_entry_threshold(NO_C_STRICT_THRESHOLD),
        ER_ENTRY_PRICE_OFFSET,
    )


def _log_bidirectional_shadow_status() -> None:
    try:
        from bot.strategy.bidirectional_momentum import EntryConfig
        cfg = EntryConfig()
        logger.info(
            "BIDIRECTIONAL SHADOW V1.1: ENABLED\n"
            "  MODE: OBSERVE ONLY\n"
            "  CONFIG: skip=%s | NO_avoid=%.2f-%.2f | conf>=%.2f | move>=%.0f$ | window=%d-%ds",
            ",".join(cfg.skip_regimes),
            cfg.no_avoid_zone_lo, cfg.no_avoid_zone_hi,
            cfg.min_confidence, cfg.min_move_30s,
            cfg.min_seconds_from_start, cfg.max_seconds_from_start,
        )
    except Exception:
        logger.info("BIDIRECTIONAL SHADOW V1.1: DISABLED")


def _log_new_window_check(market) -> None:
    global _last_logged_window
    if market.window_start_ts == _last_logged_window:
        return

    logger.info(
        "=== NEW 5M WINDOW === window_start=%s",
        market.window_start_ts,
    )
    now = int(time.time())
    lag = now - market.window_start_ts
    seconds_from_start = now - market.window_start_ts
    seconds_left = market.end_ts - now
    logger.info(
        "WINDOW CHECK | now=%s window_start=%s lag=%ss seconds_from_start=%s seconds_left=%s slug=%s",
        now,
        market.window_start_ts,
        lag,
        seconds_from_start,
        seconds_left,
        market.slug,
    )
    _last_logged_window = market.window_start_ts


def _log_cycle_strategy_status() -> None:
    logger.info(
        "Active strategies | v2: %s | v2.5: %s | v3: %s",
        _cycle_signal_status["v2"],
        _cycle_signal_status["v2.5"],
        _cycle_signal_status["v3"],
    )


def run() -> None:
    run_startup_recovery()
    logger.info(
        "Starting BTC 5m trader | mode=%s | live_exit=%s | main poll %.1fs | v3 poll %.1fs | v4 poll %.1fs",
        TRADING_MODE,
        LIVE_EXIT_ENABLED,
        POLL_INTERVAL_SEC,
        ER_V3_POLL_INTERVAL_SEC,
        V4_POLL_INTERVAL_SEC,
    )
    logger.info(
        "Active versions:\nV1=%s\nV2=%s\nV2.5=%s\nV3=%s\nV4 Shadow=%s\nYES_C Shadow=%s\nNO_C Filter Shadow=%s",
        ENABLE_V1,
        ENABLE_V2,
        ENABLE_V25,
        ENABLE_V3,
        ENABLE_V4_SHADOW,
        ENABLE_YES_C_SHADOW,
        ENABLE_NO_C_FILTER_SHADOW,
    )
    logger.info(
        "Active strategies | v1: %s | v2: %s | v2.5: %s | v3: %s",
        format_enabled_strategies(ENABLED_STRATEGIES),
        format_enabled_strategies(ENABLED_STRATEGIES_V2),
        format_enabled_strategies(ENABLED_STRATEGIES_V25),
        format_enabled_strategies(ENABLED_STRATEGIES_V3),
    )
    log_er_entry_threshold_config()
    _log_effective_thresholds()
    _log_bidirectional_shadow_status()
    log_no_c_filter_live_config()

    last_full_cycle = 0.0
    last_v3_cycle = 0.0
    last_v4_cycle = 0.0
    last_er_summary = 0.0

    while True:
        now = time.time()
        try:
            if now - last_er_summary >= ER_SUMMARY_INTERVAL_SEC:
                with connect() as conn:
                    log_er_summary(conn)
                    log_er_funnel_time_report(conn)
                    log_trailing_summary(conn)
                    log_btc_direction_summary(conn)
                    if ENABLE_YES_C_SHADOW:
                        log_yes_c_shadow_reports(conn)
                    if ENABLE_V4_SHADOW:
                        log_v4_shadow_report(conn)
                    if ENABLE_NO_C_FILTER_SHADOW:
                        log_no_c_filter_shadow_report(conn)
                last_er_summary = now
            if ENABLE_V4_SHADOW and now - last_v4_cycle >= V4_POLL_INTERVAL_SEC:
                _v4_cycle()
                last_v4_cycle = now
            if ENABLE_V3 and now - last_v3_cycle >= ER_V3_POLL_INTERVAL_SEC:
                _v3_cycle()
                last_v3_cycle = now
            if now - last_full_cycle >= POLL_INTERVAL_SEC:
                _cycle()
                last_full_cycle = now
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception:
            logger.exception("Unexpected error in main loop")
        time.sleep(0.05)


def _v4_cycle() -> None:
    now_ts = int(time.time())
    with connect() as conn:
        closed = close_due_v4_shadow_trades(conn, now_ts)
        if closed:
            conn.commit()
            logger.info("Closed %s V4 Shadow trade(s)", closed)

    market = find_active_btc_5m_market()
    if not market:
        return

    _log_new_window_check(market)

    try:
        btc_price = get_current_btc_price()
        strike = get_strike_price(market.window_start_ts)
    except BtcPriceError as exc:
        logger.error("[%s] V4 price/strike fetch failed: %s", _ts(), exc)
        return

    quotes = get_best_bid_ask(market)
    with connect() as conn:
        process_v4_shadow(
            conn,
            market,
            quotes,
            btc_price=btc_price,
            strike=strike,
        )
        conn.commit()


def _v3_cycle() -> None:
    now_ts = int(time.time())
    with connect() as conn:
        closed = close_due_early_reversion_v3_trades(conn, now_ts)
        if closed:
            conn.commit()
            logger.info("Closed %s Early Reversion v3 trade(s)", closed)

    market = find_active_btc_5m_market()
    if not market:
        return

    _log_new_window_check(market)

    quotes = get_best_bid_ask(market)
    with connect() as conn:
        v3_status = "-"
        if ENABLE_V3:
            v3_status = format_cycle_signal_status(
                process_early_reversion_v3(conn, market, quotes)
            )
        conn.commit()

    _cycle_signal_status["v3"] = v3_status
    _log_cycle_strategy_status()


def _cycle() -> None:
    try:
        btc_price = get_current_btc_price()
    except BtcPriceError as exc:
        logger.error("[%s] Price fetch failed: %s", _ts(), exc)
        return

    with connect() as conn:
        settled = 0
        if ENABLE_LATE_WINDOW:
            track_delta_for_open_trades(conn, btc_price)
            settled = settle_due_trades(conn, btc_price=btc_price)
        closed_er = 0
        closed_er_v2 = 0
        closed_er_v25 = 0
        closed_yes_c_shadow = 0
        if ENABLE_V1:
            closed_er = close_due_early_reversion_trades(conn, int(time.time()))
        if ENABLE_V2:
            closed_er_v2 = close_due_early_reversion_v2_trades(conn, int(time.time()))
        if ENABLE_V25:
            closed_er_v25 = close_due_early_reversion_v25_trades(conn, int(time.time()))
        if ENABLE_YES_C_SHADOW:
            closed_yes_c_shadow = close_due_yes_c_shadow_trades(conn, int(time.time()))
        stuck_exits = reconcile_stuck_exits(conn, int(time.time()))
        fill_audits = sync_pending_order_fills(conn)
        if settled or closed_er or closed_er_v2 or closed_er_v25 or closed_yes_c_shadow or stuck_exits or fill_audits:
            conn.commit()
            if settled:
                logger.info("Settled %s trade(s)", settled)
            if closed_er:
                logger.info("Closed %s Early Reversion trade(s)", closed_er)
            if closed_er_v2:
                logger.info("Closed %s Early Reversion v2 trade(s)", closed_er_v2)
            if closed_er_v25:
                logger.info("Closed %s Early Reversion v2.5 trade(s)", closed_er_v25)
            if closed_yes_c_shadow:
                logger.info("Closed %s YES_C shadow trade(s)", closed_yes_c_shadow)
            if stuck_exits:
                logger.warning(
                    "Exit recovery reconciled %s stuck open position(s)",
                    stuck_exits,
                )

        # Observe-only: sync evolution shadows every cycle (catch-up + new closes)
        try:
            from bot.evolution.observe import observe_closed_trades
            observe_closed_trades(conn)
        except Exception as exc:
            logger.warning("evolution observe failed: %s", exc, exc_info=True)

        conn.commit()

    market = find_active_btc_5m_market()
    if not market:
        logger.warning("[%s] No active BTC 5m market found", _ts())
        return

    _log_new_window_check(market)

    try:
        strike = get_strike_price(market.window_start_ts)
    except BtcPriceError as exc:
        logger.error("[%s] Strike fetch failed: %s", _ts(), exc)
        return

    quotes = get_best_bid_ask(market)
    seconds_left = market.seconds_remaining
    signal = None
    if ENABLE_LATE_WINDOW:
        signal = evaluate(
            seconds_remaining=seconds_left,
            btc_price=btc_price,
            strike_price=strike,
        )

    signal_name = signal.side.value if signal else None
    logger.info(
        "[%s] %s | %.0fs left | strike %.2f | BTC %.2f | Δ %.2f | "
        "YES %.3f/%.3f | NO %.3f/%.3f | signal=%s",
        _ts(),
        market.slug,
        seconds_left,
        strike,
        btc_price,
        btc_price - strike,
        quotes["yes_bid"] or 0,
        quotes["yes_ask"] or 0,
        quotes["no_bid"] or 0,
        quotes["no_ask"] or 0,
        signal_name or "-",
    )

    with connect() as conn:
        insert_market_check(
            conn,
            market_slug=market.slug,
            seconds_remaining=seconds_left,
            strike_price=strike,
            btc_price=btc_price,
            yes_bid=quotes["yes_bid"],
            yes_ask=quotes["yes_ask"],
            no_bid=quotes["no_bid"],
            no_ask=quotes["no_ask"],
            signal=signal_name,
        )

        if ENABLE_LATE_WINDOW and signal:
            trade_id = record_virtual_trade(conn, market, signal)
            if trade_id is not None:
                conn.commit()

        if ENABLE_V1:
            process_early_reversion(conn, market, quotes)
        v2_status = "-"
        v25_status = "-"
        if ENABLE_V2:
            v2_status = format_cycle_signal_status(
                process_early_reversion_v2(conn, market, quotes, btc_price=btc_price)
            )
        if ENABLE_V25:
            v25_status = format_cycle_signal_status(
                process_early_reversion_v25(conn, market, quotes)
            )
        if ENABLE_YES_C_SHADOW:
            process_yes_c_shadow(conn, market, quotes)

        # Bidirectional Momentum V1.1 — observe-only shadow
        try:
            from bot.strategy.bidirectional_observe import observe_market
            observe_market(
                conn,
                market_slug=market.slug,
                window_start_ts=market.window_start_ts,
                btc_price=btc_price,
                strike=strike,
                yes_bid=quotes["yes_bid"] or 0,
                yes_ask=quotes["yes_ask"] or 0,
                no_bid=quotes["no_bid"] or 0,
                no_ask=quotes["no_ask"] or 0,
                seconds_from_start=int(300 - seconds_left),
                seconds_left=int(seconds_left),
            )
        except Exception as exc:
            logger.debug("bidirectional shadow skipped: %s", exc)

        conn.commit()

    _cycle_signal_status["v2"] = v2_status
    _cycle_signal_status["v2.5"] = v25_status
    _log_cycle_strategy_status()


def main() -> None:
    run()


if __name__ == "__main__":
    sys.exit(main() or 0)
