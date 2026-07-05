"""BTC spot multi-timeframe context — no look-ahead (quotes timestamp <= T)."""

from __future__ import annotations

import math
import sqlite3
import statistics

from bot.research.mtf.config import BTC_RETURN_WINDOWS
from bot.research.mtf.models import BtcSpotContext


def _btc_prices_before(
    conn: sqlite3.Connection,
    ts: int,
    limit: int = 500,
) -> list[tuple[int, float]]:
    rows = conn.execute(
        """
        SELECT cast(strftime('%s', checked_at) AS integer) AS ts_unix, btc_price
        FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) <= ?
          AND btc_price IS NOT NULL AND btc_price > 0
        ORDER BY checked_at DESC
        LIMIT ?
        """,
        (ts, limit),
    ).fetchall()
    return [(int(r["ts_unix"]), float(r["btc_price"])) for r in rows if r["ts_unix"]]


def _price_at_or_before(prices: list[tuple[int, float]], ts: int) -> float | None:
    for qts, price in prices:
        if qts <= ts:
            return price
    return None


def _return_pct(now: float, then: float) -> float | None:
    if then <= 0:
        return None
    return (now - then) / then * 100.0


def build_btc_context(conn: sqlite3.Connection, ts: int) -> BtcSpotContext:
    """Build BTC spot context at timestamp T using only data <= T."""
    series = _btc_prices_before(conn, ts)
    if not series:
        return BtcSpotContext(timestamp=ts, price=None)

    now_price = series[0][1]
    ctx = BtcSpotContext(timestamp=ts, price=now_price)

    for name, window in BTC_RETURN_WINDOWS.items():
        then = _price_at_or_before(series, ts - window)
        if then is not None:
            ret = _return_pct(now_price, then)
            if name == "5m":
                ctx.return_5m = ret
            elif name == "15m":
                ctx.return_15m = ret
            elif name == "30m":
                ctx.return_30m = ret
            elif name == "1h":
                ctx.return_1h = ret
            elif name == "4h":
                ctx.return_4h = ret

    # Rolling high/low over ~4h window
    window_prices = [p for qts, p in series if qts >= ts - 14400]
    if window_prices:
        hi = max(window_prices)
        lo = min(window_prices)
        if hi > 0:
            ctx.dist_from_high_4h_pct = (now_price - hi) / hi * 100
        if lo > 0:
            ctx.dist_from_low_4h_pct = (now_price - lo) / lo * 100

    # Realized vol + trend on 1h subsample
    hour_prices = [(qts, p) for qts, p in series if qts >= ts - 3600]
    hour_prices.reverse()
    if len(hour_prices) >= 5:
        rets = []
        for i in range(1, len(hour_prices)):
            prev = hour_prices[i - 1][1]
            if prev > 0:
                rets.append((hour_prices[i][1] - prev) / prev * 100)
        if rets:
            ctx.realized_vol_1h = round(statistics.pstdev(rets), 4)
        # Simple slope: last vs first in window
        first_p = hour_prices[0][1]
        last_p = hour_prices[-1][1]
        if first_p > 0:
            ctx.trend_slope_1h = round((last_p - first_p) / first_p * 100, 4)

    if ctx.return_15m is not None and ctx.return_5m is not None:
        ctx.acceleration_15m = round(ctx.return_5m - (ctx.return_15m / 3), 4)

    return ctx
