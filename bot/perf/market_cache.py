"""Batch-loaded market_checks / BTC history — no SQL in loops."""

from __future__ import annotations

import bisect
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any

from bot.er_btc_direction_stats import _exit_ts
from bot.no_c_filter_shadow import MAX_BTC_LOOKUP_DELTA_SEC
from bot.report.analytics import SETTLEMENT_BID, _bid_column, _pnl_pct, trade_pnl

LOOKBACK_BUFFER_SEC = 100


@dataclass(frozen=True)
class _CheckRow:
    ts: int
    btc_price: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    strike_price: float | None
    seconds_remaining: float | None


class MarketDataCache:
    """In-memory market_checks index for feature building."""

    def __init__(self) -> None:
        self._btc_ts: list[int] = []
        self._btc_prices: list[float] = []
        self._by_slug: dict[str, list[_CheckRow]] = {}

    @classmethod
    def build(cls, conn: sqlite3.Connection, trades: list[Any]) -> MarketDataCache:
        cache = cls()
        if not trades:
            return cache

        min_ts = min(int(t["entry_ts"]) for t in trades) - LOOKBACK_BUFFER_SEC - 90
        max_ts = max(_exit_ts(t) for t in trades) + LOOKBACK_BUFFER_SEC
        slugs = sorted({str(t["market_slug"]) for t in trades})

        btc_rows = conn.execute(
            """
            SELECT cast(strftime('%s', checked_at) AS integer) AS ts, btc_price
            FROM market_checks
            WHERE cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
              AND btc_price IS NOT NULL
            ORDER BY ts ASC
            """,
            (min_ts, max_ts),
        ).fetchall()
        for row in btc_rows:
            cache._btc_ts.append(int(row["ts"]))
            cache._btc_prices.append(float(row["btc_price"]))

        if slugs:
            placeholders = ", ".join("?" for _ in slugs)
            slug_rows = conn.execute(
                f"""
                SELECT market_slug,
                       cast(strftime('%s', checked_at) AS integer) AS ts,
                       btc_price, yes_bid, yes_ask, no_bid, no_ask,
                       strike_price, seconds_remaining
                FROM market_checks
                WHERE market_slug IN ({placeholders})
                  AND cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
                ORDER BY market_slug ASC, ts ASC
                """,
                (*slugs, min_ts, max_ts),
            ).fetchall()
            for row in slug_rows:
                slug = str(row["market_slug"])
                cache._by_slug.setdefault(slug, []).append(
                    _CheckRow(
                        ts=int(row["ts"]),
                        btc_price=float(row["btc_price"]) if row["btc_price"] is not None else None,
                        yes_bid=float(row["yes_bid"]) if row["yes_bid"] is not None else None,
                        yes_ask=float(row["yes_ask"]) if row["yes_ask"] is not None else None,
                        no_bid=float(row["no_bid"]) if row["no_bid"] is not None else None,
                        no_ask=float(row["no_ask"]) if row["no_ask"] is not None else None,
                        strike_price=float(row["strike_price"])
                        if row["strike_price"] is not None
                        else None,
                        seconds_remaining=float(row["seconds_remaining"])
                        if row["seconds_remaining"] is not None
                        else None,
                    )
                )
        return cache

    def _nearest_ts_index(self, series_ts: list[int], target: int) -> int | None:
        if not series_ts:
            return None
        i = bisect.bisect_left(series_ts, target)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(series_ts)]
        best_j = min(candidates, key=lambda j: abs(series_ts[j] - target))
        if abs(series_ts[best_j] - target) > MAX_BTC_LOOKUP_DELTA_SEC:
            return None
        return best_j

    def slug_btc_price_at(self, market_slug: str, target_ts: int) -> float | None:
        rows = self._by_slug.get(market_slug, [])
        if not rows:
            return self.btc_price_at(target_ts)
        ts_list = [r.ts for r in rows]
        idx = self._nearest_ts_index(ts_list, target_ts)
        if idx is None:
            return None
        return rows[idx].btc_price

    def btc_price_at(self, target_ts: int) -> float | None:
        idx = self._nearest_ts_index(self._btc_ts, target_ts)
        return self._btc_prices[idx] if idx is not None else None

    def btc_move_at(self, entry_ts: int, lookback: int) -> float | None:
        current = self.btc_price_at(entry_ts)
        if current is None:
            return None
        past = self.btc_price_at(entry_ts - lookback)
        if past is None:
            return None
        return current - past

    def btc_volatility(self, entry_ts: int, window: int) -> float | None:
        lo, hi = entry_ts - window, entry_ts
        prices = [p for ts, p in zip(self._btc_ts, self._btc_prices) if lo <= ts <= hi]
        if len(prices) < 2:
            return None
        return statistics.pstdev(prices)

    def quote_at_entry(
        self,
        *,
        market_slug: str,
        side: str,
        entry_ts: int,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        rows = self._by_slug.get(market_slug, [])
        if not rows:
            return None, None, None, None
        ts_list = [r.ts for r in rows]
        idx = self._nearest_ts_index(ts_list, entry_ts)
        if idx is None:
            return None, None, None, None
        row = rows[idx]
        bid = row.yes_bid if side == "YES" else row.no_bid
        ask = row.yes_ask if side == "YES" else row.no_ask
        dist = None
        if row.btc_price is not None and row.strike_price is not None:
            dist = abs(row.btc_price - row.strike_price)
        return bid, ask, dist, row.seconds_remaining

    def bid_series(
        self,
        *,
        market_slug: str,
        side: str,
        start_ts: int,
        end_ts: int,
    ) -> list[tuple[int, float]]:
        column = _bid_column(side)
        rows = self._by_slug.get(market_slug, [])
        series: list[tuple[int, float]] = []
        for row in rows:
            if row.ts < start_ts or row.ts > end_ts:
                continue
            bid = row.yes_bid if column == "yes_bid" else row.no_bid
            if bid is None:
                continue
            if bid + 1e-9 >= SETTLEMENT_BID:
                continue
            series.append((row.ts, bid))
        return series

    def mfe_mae(self, trade: Any) -> tuple[float | None, float | None]:
        entry = float(trade["entry_price"])
        series = self.bid_series(
            market_slug=str(trade["market_slug"]),
            side=str(trade["side"]),
            start_ts=int(trade["entry_ts"]),
            end_ts=_exit_ts(trade),
        )
        if not series:
            peak = trade["max_price_seen"]
            if peak is None:
                return None, None
            mfe = (float(peak) - entry) / entry * 100
            return mfe, trade_pnl(trade)
        bids = [b for _, b in series]
        return (max(bids) - entry) / entry * 100, (min(bids) - entry) / entry * 100
