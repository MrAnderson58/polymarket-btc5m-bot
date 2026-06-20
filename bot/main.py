"""Main loop for Polymarket BTC 5m paper trading research."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone

from bot.btc_price import BtcPriceError, get_current_btc_price, get_strike_price
from bot.config import POLL_INTERVAL_SEC
from bot.database import connect, init_db, insert_market_check
from bot.market_scanner import find_active_btc_5m_market, get_best_bid_ask
from bot.early_reversion import close_due_early_reversion_trades, process_early_reversion
from bot.early_reversion_v2 import close_due_early_reversion_v2_trades, process_early_reversion_v2
from bot.paper_trader import record_virtual_trade, settle_due_trades, track_delta_for_open_trades
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
    init_db()
    logger.info("Starting BTC 5m paper trader | poll every %.1fs", POLL_INTERVAL_SEC)

    while True:
        try:
            _cycle()
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception:
            logger.exception("Unexpected error in main loop")
        time.sleep(POLL_INTERVAL_SEC)


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
        if settled or closed_er or closed_er_v2:
            conn.commit()
            if settled:
                logger.info("Settled %s trade(s)", settled)
            if closed_er:
                logger.info("Closed %s Early Reversion trade(s)", closed_er)
            if closed_er_v2:
                logger.info("Closed %s Early Reversion v2 trade(s)", closed_er_v2)
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

        conn.commit()


def main() -> None:
    run()


if __name__ == "__main__":
    sys.exit(main() or 0)
