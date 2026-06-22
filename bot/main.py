"""Main loop for Polymarket BTC 5m paper trading research."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from bot.btc_price import BtcPriceError, get_current_btc_price, get_strike_price
from bot.config import (
    ER_V3_POLL_INTERVAL_SEC,
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
from bot.early_reversion import close_due_early_reversion_trades, process_early_reversion
from bot.early_reversion_v2 import close_due_early_reversion_v2_trades, process_early_reversion_v2
from bot.early_reversion_v25 import close_due_early_reversion_v25_trades, process_early_reversion_v25
from bot.early_reversion_v3 import close_due_early_reversion_v3_trades, process_early_reversion_v3
from bot.paper_trader import record_virtual_trade, settle_due_trades, track_delta_for_open_trades
from bot.recovery import run_startup_recovery
from bot.strategy import evaluate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def run() -> None:
    run_startup_recovery()
    logger.info(
        "Starting BTC 5m trader | mode=%s | live_exit=%s | main poll %.1fs | v3 poll %.1fs",
        TRADING_MODE,
        LIVE_EXIT_ENABLED,
        POLL_INTERVAL_SEC,
        ER_V3_POLL_INTERVAL_SEC,
    )
    logger.info(
        "Active strategies | v1: %s | v2: %s | v2.5: %s | v3: %s",
        format_enabled_strategies(ENABLED_STRATEGIES),
        format_enabled_strategies(ENABLED_STRATEGIES_V2),
        format_enabled_strategies(ENABLED_STRATEGIES_V25),
        format_enabled_strategies(ENABLED_STRATEGIES_V3),
    )

    last_full_cycle = 0.0
    last_v3_cycle = 0.0

    while True:
        now = time.time()
        try:
            if now - last_v3_cycle >= ER_V3_POLL_INTERVAL_SEC:
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

    quotes = get_best_bid_ask(market)
    with connect() as conn:
        process_early_reversion_v3(conn, market, quotes)
        conn.commit()


def _cycle() -> None:
    try:
        btc_price = get_current_btc_price()
    except BtcPriceError as exc:
        logger.error("[%s] Price fetch failed: %s", _ts(), exc)
        return

    with connect() as conn:
        track_delta_for_open_trades(conn, btc_price)
        settled = settle_due_trades(conn, btc_price=btc_price)
        closed_er = close_due_early_reversion_trades(conn, int(time.time()))
        closed_er_v2 = close_due_early_reversion_v2_trades(conn, int(time.time()))
        closed_er_v25 = close_due_early_reversion_v25_trades(conn, int(time.time()))
        if settled or closed_er or closed_er_v2 or closed_er_v25:
            conn.commit()
            if settled:
                logger.info("Settled %s trade(s)", settled)
            if closed_er:
                logger.info("Closed %s Early Reversion trade(s)", closed_er)
            if closed_er_v2:
                logger.info("Closed %s Early Reversion v2 trade(s)", closed_er_v2)
            if closed_er_v25:
                logger.info("Closed %s Early Reversion v2.5 trade(s)", closed_er_v25)
        else:
            conn.commit()

    market = find_active_btc_5m_market()
    if not market:
        logger.warning("[%s] No active BTC 5m market found", _ts())
        return

    try:
        strike = get_strike_price(market.window_start_ts)
    except BtcPriceError as exc:
        logger.error("[%s] Strike fetch failed: %s", _ts(), exc)
        return

    quotes = get_best_bid_ask(market)
    seconds_left = market.seconds_remaining
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

        if signal:
            trade_id = record_virtual_trade(conn, market, signal)
            if trade_id is not None:
                conn.commit()

        process_early_reversion(conn, market, quotes)
        process_early_reversion_v2(conn, market, quotes)
        process_early_reversion_v25(conn, market, quotes)

        conn.commit()


def main() -> None:
    run()


if __name__ == "__main__":
    sys.exit(main() or 0)
