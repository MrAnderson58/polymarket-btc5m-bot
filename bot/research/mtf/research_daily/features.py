"""Daily feature engineering — session structure, no look-ahead."""

from __future__ import annotations

import sqlite3
import statistics

from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.polymarket_context import pm_context_from_snapshot_row
from bot.research.mtf.research_daily.models import ObsDaily, parse_daily_window, session_label


def _btc_series(conn, ts):
    rows = conn.execute(
        """
        SELECT cast(strftime('%s', checked_at) AS integer) AS ts_unix, btc_price
        FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) <= ?
          AND btc_price IS NOT NULL AND btc_price > 0
        ORDER BY checked_at DESC LIMIT 2000
        """,
        (ts,),
    ).fetchall()
    return [(int(r["ts_unix"]), float(r["btc_price"])) for r in rows if r["ts_unix"]]


def _price_at(series, ts):
    for qts, p in series:
        if qts <= ts:
            return p
    return None


def _mid(bid, ask):
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return ask if ask is not None else bid


def build_obs_daily(conn: sqlite3.Connection, row: sqlite3.Row) -> ObsDaily:
    ts = int(row["timestamp"])
    slug = row["market_daily_slug"]
    bounds = parse_daily_window(slug)
    ws, we = bounds if bounds else (ts - 86400, ts)
    btc = float(row["btc_price"]) if row["btc_price"] else None
    strike = row["market_daily_strike"]
    yb, ya = row["market_daily_yes_bid"], row["market_daily_yes_ask"]
    nb, na = row["market_daily_no_bid"], row["market_daily_no_ask"]
    spread = (ya - yb) if ya is not None and yb is not None else None
    series = _btc_series(conn, ts) if btc else []
    btc_f = btc or 0.0

    open_price = _price_at(series, ws) or btc_f
    window_prices = [(q, p) for q, p in series if ws <= q <= ts]
    hi = max((p for _, p in window_prices), default=btc_f)
    lo = min((p for _, p in window_prices), default=btc_f)

    rets = []
    sorted_w = sorted(window_prices)
    for i in range(1, min(len(sorted_w), 60)):
        if sorted_w[i - 1][1] > 0:
            rets.append((sorted_w[i][1] - sorted_w[i - 1][1]) / sorted_w[i - 1][1] * 100)
    rv = statistics.pstdev(rets) if len(rets) >= 3 else 0.0

    ctx = build_btc_context(conn, ts)
    pm1h = pm_context_from_snapshot_row(row, "1h", "1h")
    yes_mid = _mid(yb, ya)

    entry_second = ts - ws
    dist_open = btc_f - open_price if open_price else 0.0
    dist_open_pct = dist_open / open_price * 100 if open_price else 0.0
    dist_strike = (btc_f - float(strike)) if strike and btc else 0.0
    dist_strike_pct = dist_strike / float(strike) * 100 if strike else 0.0

    trend_start = _price_at(series, max(ws, ts - 14400))
    intraday_trend = btc_f - trend_start if trend_start else 0.0

    vol_regime = "high" if rv > 0.15 else ("low" if rv < 0.05 else "normal")
    sl = row["market_daily_seconds_left"]
    if sl is None:
        sl = max(0, we - ts)

    return ObsDaily(
        timestamp=ts, market_slug=slug, window_start_ts=ws, window_end_ts=we,
        entry_second=entry_second, seconds_left=int(sl),
        btc_price=btc, strike=float(strike) if strike else None,
        yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na, spread=spread, yes_mid=yes_mid,
        session=session_label(entry_second),
        dist_strike_usd=dist_strike, dist_strike_pct=dist_strike_pct,
        dist_daily_open_usd=dist_open, dist_daily_open_pct=dist_open_pct,
        dist_from_high_pct=(btc_f - hi) / btc_f * 100 if btc_f else 0,
        dist_from_low_pct=(btc_f - lo) / btc_f * 100 if btc_f else 0,
        realized_vol=rv, vol_regime=vol_regime, intraday_trend_usd=intraday_trend,
        btc_trend_1h=ctx.return_1h, btc_trend_4h=ctx.return_4h,
        pm_prob_1h=pm1h.midpoint,
    )
