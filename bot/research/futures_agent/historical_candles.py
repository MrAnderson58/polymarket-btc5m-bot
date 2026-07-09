"""Historical 1m OHLCV provider with local cache (Binance spot/futures)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from bot.research.futures_agent.market_provider import BinanceMarketProvider, symbol_pair
from bot.research.futures_agent.signal_outcome_constants import INTERVAL_1M_SEC, MAX_EVAL_SEC

BINANCE_PAGE_LIMIT = 1000


@dataclass(frozen=True)
class Candle:
    open_ts: int
    open: float
    high: float
    low: float
    close: float

    @property
    def close_ts(self) -> int:
        return self.open_ts + INTERVAL_1M_SEC - 1


def candle_from_binance(row: list) -> Candle:
    return Candle(
        open_ts=int(row[0] // 1000),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
    )


def resolve_exchange_symbol(symbol: str | None) -> tuple[str | None, str, str]:
    """Return (exchange_symbol, data_source, status). Never silently map unknown."""
    if not symbol or not str(symbol).strip():
        return None, "", "missing_symbol"
    raw = str(symbol).upper().strip()
    if raw.endswith("USDT"):
        return raw, "binance_spot", "resolved"
    if not raw.isalpha() or not (2 <= len(raw) <= 10):
        return None, "", "unresolved_symbol"
    return symbol_pair(raw), "binance_spot", "resolved"


def count_gaps(candles: list[Candle], *, interval_sec: int = INTERVAL_1M_SEC) -> int:
    if len(candles) < 2:
        return 0
    gaps = 0
    for i in range(1, len(candles)):
        expected = candles[i - 1].open_ts + interval_sec
        if candles[i].open_ts > expected:
            gaps += int((candles[i].open_ts - expected) / interval_sec)
    return gaps


class CandleProvider(Protocol):
    def fetch_range(
        self,
        exchange_symbol: str,
        start_ts: int,
        end_ts: int,
        *,
        interval: str = "1m",
    ) -> tuple[list[Candle], dict[str, Any]]:
        ...


class BinanceCandleProvider:
    """Paginated Binance 1m fetch with retry via BinanceMarketProvider."""

    def __init__(self, provider: BinanceMarketProvider | None = None) -> None:
        self._provider = provider or BinanceMarketProvider()

    def fetch_range(
        self,
        exchange_symbol: str,
        start_ts: int,
        end_ts: int,
        *,
        interval: str = "1m",
    ) -> tuple[list[Candle], dict[str, Any]]:
        meta: dict[str, Any] = {
            "exchange_symbol": exchange_symbol,
            "interval": interval,
            "requested_start_ts": start_ts,
            "requested_end_ts": end_ts,
            "data_source": "binance_spot",
            "fetch_status": "ok",
            "pages": 0,
        }
        if start_ts >= end_ts:
            meta["fetch_status"] = "empty_range"
            return [], meta

        all_rows: list[list] = []
        cursor_end = end_ts + INTERVAL_1M_SEC
        earliest_needed = start_ts
        safety = 0
        while cursor_end > earliest_needed and safety < 50:
            safety += 1
            page = self._provider.fetch_spot_klines(
                exchange_symbol, interval, cursor_end, limit=BINANCE_PAGE_LIMIT,
            )
            if not page:
                page = self._provider.fetch_futures_klines(
                    exchange_symbol, interval, cursor_end, limit=BINANCE_PAGE_LIMIT,
                )
                if page:
                    meta["data_source"] = "binance_futures"
            meta["pages"] += 1
            if not page:
                meta["fetch_status"] = "no_data"
                break
            all_rows = page + all_rows
            first_open = int(page[0][0] // 1000)
            if first_open <= earliest_needed:
                break
            cursor_end = first_open - 1
            time.sleep(0.05)

        candles = [candle_from_binance(r) for r in all_rows]
        candles = [c for c in candles if start_ts <= c.open_ts <= end_ts]
        candles.sort(key=lambda c: c.open_ts)
        dedup: dict[int, Candle] = {c.open_ts: c for c in candles}
        candles = sorted(dedup.values(), key=lambda c: c.open_ts)

        meta["first_candle_ts"] = candles[0].open_ts if candles else None
        meta["last_candle_ts"] = candles[-1].open_ts if candles else None
        meta["candle_count"] = len(candles)
        meta["gap_count"] = count_gaps(candles)
        if not candles:
            meta["fetch_status"] = "no_data"
        elif meta["gap_count"] > 0:
            meta["fetch_status"] = "partial_gaps"
        return candles, meta


class CachedCandleProvider:
    """Read-through cache in agent DB table futures_agent_research_market_data_cache."""

    def __init__(self, conn: Any, inner: CandleProvider | None = None) -> None:
        self._conn = conn
        self._inner = inner or BinanceCandleProvider()

    def _load_cached(
        self, exchange_symbol: str, start_ts: int, end_ts: int, interval: str,
    ) -> list[Candle]:
        rows = self._conn.execute(
            """
            SELECT open_ts, open_price, high_price, low_price, close_price
            FROM futures_agent_research_market_data_cache
            WHERE exchange_symbol = ? AND interval = ?
              AND open_ts >= ? AND open_ts <= ?
            ORDER BY open_ts ASC
            """,
            (exchange_symbol, interval, start_ts, end_ts),
        ).fetchall()
        return [
            Candle(
                open_ts=int(r["open_ts"]),
                open=float(r["open_price"]),
                high=float(r["high_price"]),
                low=float(r["low_price"]),
                close=float(r["close_price"]),
            )
            for r in rows
        ]

    def _store_cache(self, exchange_symbol: str, interval: str, candles: list[Candle], source: str) -> None:
        for c in candles:
            self._conn.execute(
                """
                INSERT INTO futures_agent_research_market_data_cache (
                  exchange_symbol, interval, open_ts,
                  open_price, high_price, low_price, close_price,
                  data_source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange_symbol, interval, open_ts) DO NOTHING
                """,
                (
                    exchange_symbol, interval, c.open_ts,
                    c.open, c.high, c.low, c.close,
                    source, int(time.time()),
                ),
            )

    def fetch_range(
        self,
        exchange_symbol: str,
        start_ts: int,
        end_ts: int,
        *,
        interval: str = "1m",
    ) -> tuple[list[Candle], dict[str, Any]]:
        cached = self._load_cached(exchange_symbol, start_ts, end_ts, interval)
        expected = max(0, (end_ts - start_ts) // INTERVAL_1M_SEC + 1)
        if cached and len(cached) >= expected * 0.98:
            meta = {
                "exchange_symbol": exchange_symbol,
                "interval": interval,
                "requested_start_ts": start_ts,
                "requested_end_ts": end_ts,
                "data_source": "cache",
                "fetch_status": "cache_hit",
                "first_candle_ts": cached[0].open_ts,
                "last_candle_ts": cached[-1].open_ts,
                "candle_count": len(cached),
                "gap_count": count_gaps(cached),
            }
            return cached, meta

        candles, meta = self._inner.fetch_range(
            exchange_symbol, start_ts, end_ts, interval=interval,
        )
        if candles:
            self._store_cache(exchange_symbol, interval, candles, str(meta.get("data_source", "binance")))
        return candles, meta


def candles_covering(
    candles: list[Candle], start_ts: int, end_ts: int,
) -> list[Candle]:
    return [c for c in candles if start_ts <= c.open_ts <= end_ts]


def required_end_ts(decision_ts: int) -> int:
    return decision_ts + MAX_EVAL_SEC + INTERVAL_1M_SEC
