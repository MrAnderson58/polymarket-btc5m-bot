"""1h feature engineering — no look-ahead."""

from __future__ import annotations

import sqlite3
import statistics

from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.polymarket_context import pm_context_from_snapshot_row
from bot.research.mtf.research_1h.models import Obs1h, parse_1h_window


def _btc_series(conn: sqlite3.Connection, ts: int) -> list[tuple[int, float]]:
    rows = conn.execute(
        """
        SELECT cast(strftime('%s', checked_at) AS integer) AS ts_unix, btc_price
        FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) <= ?
          AND btc_price IS NOT NULL AND btc_price > 0
        ORDER BY checked_at DESC LIMIT 1200
        """,
        (ts,),
    ).fetchall()
    return [(int(r["ts_unix"]), float(r["btc_price"])) for r in rows if r["ts_unix"]]


def _price_at(series: list[tuple[int, float]], ts: int) -> float | None:
    for qts, p in series:
        if qts <= ts:
            return p
    return None


def _move(series: list[tuple[int, float]], ts: int, btc: float, sec: int) -> float:
    past = _price_at(series, ts - sec)
    return btc - past if past is not None else 0.0


def _mid(bid, ask):
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return ask if ask is not None else bid


def build_obs1h(conn: sqlite3.Connection, row: sqlite3.Row) -> Obs1h:
    ts = int(row["timestamp"])
    slug = row["market_1h_slug"]
    bounds = parse_1h_window(slug)
    ws, we = bounds if bounds else (ts, ts + 3600)
    btc = float(row["btc_price"]) if row["btc_price"] else None
    strike = row["market_1h_strike"]
    yb, ya = row["market_1h_yes_bid"], row["market_1h_yes_ask"]
    nb, na = row["market_1h_no_bid"], row["market_1h_no_ask"]
    spread = (ya - yb) if ya is not None and yb is not None else None
    series = _btc_series(conn, ts) if btc else []
    btc_f = btc or 0.0

    moves = {k: _move(series, ts, btc_f, s) for k, s in [
        ("1m", 60), ("3m", 180), ("5m", 300), ("10m", 600), ("15m", 900),
    ]}

    rets = []
    hour = [(q, p) for q, p in series if q >= ts - 3600]
    hour.reverse()
    for i in range(1, len(hour)):
        if hour[i - 1][1] > 0:
            rets.append((hour[i][1] - hour[i - 1][1]) / hour[i - 1][1] * 100)
    rv = statistics.pstdev(rets) if len(rets) >= 3 else 0.0
    rv_short = statistics.pstdev(rets[-10:]) if len(rets) >= 10 else rv
    vol_exp = (rv_short / rv) if rv > 0 else 1.0

    signs = [1 if moves["5m"] > 0 else (-1 if moves["5m"] < 0 else 0),
             1 if moves["10m"] > 0 else (-1 if moves["10m"] < 0 else 0),
             1 if moves["15m"] > 0 else (-1 if moves["15m"] < 0 else 0)]
    consistency = abs(sum(signs)) / 3

    ctx = build_btc_context(conn, ts)
    pm15 = pm_context_from_snapshot_row(row, "15m", "15m")
    yes_mid = _mid(yb, ya)

    dist_usd = dist_pct = 0.0
    if strike and btc:
        dist_usd = btc - float(strike)
        dist_pct = dist_usd / float(strike) * 100

    sl = row["market_1h_seconds_left"]
    if sl is None:
        sl = max(0, we - ts)

    return Obs1h(
        timestamp=ts, market_slug=slug, window_start_ts=ws, window_end_ts=we,
        entry_second=ts - ws, seconds_left=int(sl),
        btc_price=btc, strike=float(strike) if strike else None,
        yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na, spread=spread, yes_mid=yes_mid,
        btc_move_1m=moves["1m"], btc_move_3m=moves["3m"], btc_move_5m=moves["5m"],
        btc_move_10m=moves["10m"], btc_move_15m=moves["15m"],
        dist_strike_usd=dist_usd, dist_strike_pct=dist_pct,
        btc_trend_5m=ctx.return_5m, btc_trend_15m=ctx.return_15m,
        btc_trend_30m=ctx.return_30m, btc_trend_1h=ctx.return_1h,
        realized_vol=rv, vol_expansion=vol_exp, momentum_consistency=consistency,
        pm_prob_15m=pm15.midpoint,
    )
