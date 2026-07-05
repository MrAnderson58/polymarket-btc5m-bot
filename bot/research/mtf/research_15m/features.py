"""Feature engineering for 15m observations — strict no look-ahead."""

from __future__ import annotations

import sqlite3
import statistics

from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.discovery import parse_15m_window_start_ts
from bot.research.mtf.polymarket_context import pm_context_from_snapshot_row
from bot.research.mtf.research_15m.models import Obs15m


def _btc_series_before(conn: sqlite3.Connection, ts: int, limit: int = 800) -> list[tuple[int, float]]:
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


def _price_at(series: list[tuple[int, float]], ts: int) -> float | None:
    for qts, p in series:
        if qts <= ts:
            return p
    return None


def _move(series: list[tuple[int, float]], ts: int, btc: float, sec: int) -> float:
    past = _price_at(series, ts - sec)
    return btc - past if past is not None else 0.0


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return ask if ask is not None else bid


def build_obs15m(conn: sqlite3.Connection, row: sqlite3.Row) -> Obs15m:
    ts = int(row["timestamp"])
    slug = row["market_15m_slug"]
    ws = parse_15m_window_start_ts(slug) or ts
    btc = row["btc_price"]
    strike = row["market_15m_strike"]
    yb, ya = row["market_15m_yes_bid"], row["market_15m_yes_ask"]
    nb, na = row["market_15m_no_bid"], row["market_15m_no_ask"]
    spread = (ya - yb) if ya is not None and yb is not None else None

    series = _btc_series_before(conn, ts) if btc else []
    btc_f = float(btc) if btc else 0.0

    moves = {
        "30s": _move(series, ts, btc_f, 30),
        "1m": _move(series, ts, btc_f, 60),
        "3m": _move(series, ts, btc_f, 180),
        "5m": _move(series, ts, btc_f, 300),
        "10m": _move(series, ts, btc_f, 600),
    }

    rets = []
    hour = [(q, p) for q, p in series if q >= ts - 3600]
    hour.reverse()
    for i in range(1, len(hour)):
        if hour[i - 1][1] > 0:
            rets.append((hour[i][1] - hour[i - 1][1]) / hour[i - 1][1] * 100)
    realized_vol = statistics.pstdev(rets) if len(rets) >= 3 else 0.0

    vel_1m = moves["1m"] / 60.0
    vel_3m = moves["3m"] / 180.0 if moves["3m"] else 0.0
    acceleration = vel_1m - vel_3m

    signs = [1 if moves[k] > 0 else (-1 if moves[k] < 0 else 0) for k in ("30s", "1m", "3m")]
    consistency = abs(sum(signs)) / len(signs) if signs else 0.0

    btc_ctx = build_btc_context(conn, ts)
    pm1 = pm_context_from_snapshot_row(row, "1h", "1h")
    pmd = pm_context_from_snapshot_row(row, "daily", "daily")

    dist_usd = dist_pct = 0.0
    if strike and btc:
        dist_usd = float(btc) - float(strike)
        dist_pct = dist_usd / float(strike) * 100

    sl = row["market_15m_seconds_left"]
    if sl is None:
        sl = max(0, ws + 900 - ts)

    return Obs15m(
        timestamp=ts,
        market_slug=slug,
        window_start_ts=ws,
        entry_second=ts - ws,
        seconds_left=int(sl),
        btc_price=float(btc) if btc else None,
        strike=float(strike) if strike else None,
        yes_bid=yb,
        yes_ask=ya,
        no_bid=nb,
        no_ask=na,
        spread=spread,
        btc_move_30s=moves["30s"],
        btc_move_1m=moves["1m"],
        btc_move_3m=moves["3m"],
        btc_move_5m=moves["5m"],
        btc_move_10m=moves["10m"],
        dist_strike_usd=dist_usd,
        dist_strike_pct=dist_pct,
        realized_vol=realized_vol,
        acceleration=acceleration,
        momentum_consistency=consistency,
        btc_trend_1h=btc_ctx.return_1h,
        btc_trend_daily=btc_ctx.return_4h,
        pm_prob_1h=pm1.midpoint,
        pm_prob_daily=pmd.midpoint,
        yes_mid=_mid(yb, ya),
    )
