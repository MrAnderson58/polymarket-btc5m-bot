"""Phase G.0.1 — Resilient multi-venue market data (research recorder only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from bot.research.futures_agent.market_provider import BinanceMarketProvider, symbol_pair
from bot.research.market_events.signal_intelligence.candles import CandleBar

logger = logging.getLogger(__name__)

BYBIT_API = "https://api.bybit.com"
OKX_API = "https://www.okx.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
_TIMEOUT = (5, 12)


@dataclass
class SymbolMarketDataG01:
    symbol: str
    price: float | None = None
    funding: float | None = None
    open_interest: float | None = None
    volume_24h: float | None = None
    bars: list[CandleBar] = field(default_factory=list)
    source: str = "none"
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class FetchStatusG01:
    ok: bool
    label: str
    detail: str = ""


def _pair(symbol: str) -> str:
    return symbol_pair(symbol)


def _okx_inst(symbol: str) -> str:
    return f"{symbol.upper()}-USDT-SWAP"


def _hl_coin(symbol: str) -> str:
    return symbol.upper()


def _bars_from_binance_klines(klines: list[list]) -> list[CandleBar]:
    return [
        CandleBar(
            open_ts=int(k[0] // 1000),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in klines
    ]


def _http_get(url: str, params: dict | None = None) -> Any:
    resp = requests.get(url, params=params or {}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _http_post(url: str, payload: dict) -> Any:
    resp = requests.post(url, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_binance_futures(
    provider: BinanceMarketProvider,
    symbol: str,
    *,
    end_ts: int,
    limit: int,
) -> SymbolMarketDataG01 | None:
    pair = _pair(symbol)
    started = time.time()
    klines = provider.fetch_futures_klines(pair, "5m", end_ts, limit=limit)
    if not klines:
        return None
    funding, _ = provider.fetch_funding_rate(pair, end_ts)
    oi_data = provider._get(f"{provider.futures_api}/fapi/v1/openInterest", {"symbol": pair})  # noqa: SLF001
    oi = float(oi_data["openInterest"]) if oi_data and oi_data.get("openInterest") else None
    bars = _bars_from_binance_klines(klines)
    return SymbolMarketDataG01(
        symbol=symbol.upper(),
        price=bars[-1].close if bars else None,
        funding=funding,
        open_interest=oi,
        volume_24h=sum(b.volume for b in bars[-12:]),
        bars=bars,
        source="binance_futures",
        latency_ms=round((time.time() - started) * 1000, 1),
    )


def _fetch_bybit(symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    pair = _pair(symbol)
    try:
        kdata = _http_get(
            f"{BYBIT_API}/v5/market/kline",
            {"category": "linear", "symbol": pair, "interval": "5", "limit": min(limit, 200)},
        )
        rows = (kdata.get("result") or {}).get("list") or []
        if not rows:
            return None
        rows = list(reversed(rows))
        bars = [
            CandleBar(
                open_ts=int(r[0]) // 1000,
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]
        ticker = _http_get(
            f"{BYBIT_API}/v5/market/tickers",
            {"category": "linear", "symbol": pair},
        )
        trows = (ticker.get("result") or {}).get("list") or []
        funding = oi = vol24 = None
        if trows:
            tr = trows[0]
            funding = float(tr["fundingRate"]) if tr.get("fundingRate") not in (None, "") else None
            oi = float(tr["openInterest"]) if tr.get("openInterest") not in (None, "") else None
            vol24 = float(tr.get("volume24h") or 0) or None
        return SymbolMarketDataG01(
            symbol=symbol.upper(),
            price=bars[-1].close,
            funding=funding,
            open_interest=oi,
            volume_24h=vol24,
            bars=bars,
            source="bybit",
            latency_ms=round((time.time() - started) * 1000, 1),
        )
    except Exception as exc:
        logger.debug("bybit fetch failed %s: %s", symbol, exc)
        return None


def _fetch_okx(symbol: str, *, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    inst = _okx_inst(symbol)
    try:
        cdata = _http_get(
            f"{OKX_API}/api/v5/market/candles",
            {"instId": inst, "bar": "5m", "limit": min(limit, 100)},
        )
        rows = cdata.get("data") or []
        if not rows:
            return None
        rows = list(reversed(rows))
        bars = [
            CandleBar(
                open_ts=int(r[0]) // 1000,
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]
        funding = oi = None
        fr = _http_get(f"{OKX_API}/api/v5/public/funding-rate", {"instId": inst})
        if fr.get("data"):
            funding = float(fr["data"][0].get("fundingRate") or 0)
        oi_resp = _http_get(f"{OKX_API}/api/v5/public/open-interest", {"instId": inst})
        if oi_resp.get("data"):
            oi = float(oi_resp["data"][0].get("oi") or 0)
        return SymbolMarketDataG01(
            symbol=symbol.upper(),
            price=bars[-1].close,
            funding=funding,
            open_interest=oi,
            volume_24h=sum(b.volume for b in bars[-12:]),
            bars=bars,
            source="okx",
            latency_ms=round((time.time() - started) * 1000, 1),
        )
    except Exception as exc:
        logger.debug("okx fetch failed %s: %s", symbol, exc)
        return None


def _fetch_hyperliquid(symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    coin = _hl_coin(symbol)
    try:
        start_ms = (end_ts - limit * 300) * 1000
        candles = _http_post(HYPERLIQUID_API, {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": "5m", "startTime": start_ms, "endTime": end_ts * 1000},
        })
        if not candles:
            return None
        bars = [
            CandleBar(
                open_ts=int(c["t"]) // 1000,
                open=float(c["o"]),
                high=float(c["h"]),
                low=float(c["l"]),
                close=float(c["c"]),
                volume=float(c.get("v") or 0),
            )
            for c in candles
        ]
        meta = _http_post(HYPERLIQUID_API, {"type": "metaAndAssetCtxs"})
        funding = oi = None
        if isinstance(meta, list) and len(meta) >= 2:
            universe = meta[0].get("universe") or []
            ctxs = meta[1] or []
            idx = next((i for i, u in enumerate(universe) if u.get("name") == coin), None)
            if idx is not None and idx < len(ctxs):
                ctx = ctxs[idx]
                funding = float(ctx.get("funding") or 0)
                oi = float(ctx.get("openInterest") or 0)
        return SymbolMarketDataG01(
            symbol=symbol.upper(),
            price=bars[-1].close,
            funding=funding,
            open_interest=oi,
            volume_24h=sum(b.volume for b in bars[-12:]),
            bars=bars,
            source="hyperliquid",
            latency_ms=round((time.time() - started) * 1000, 1),
        )
    except Exception as exc:
        logger.debug("hyperliquid fetch failed %s: %s", symbol, exc)
        return None


def _fetch_binance_spot(provider: BinanceMarketProvider, symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    pair = _pair(symbol)
    klines = provider.fetch_spot_klines(pair, "5m", end_ts, limit=limit)
    if not klines:
        return None
    bars = _bars_from_binance_klines(klines)
    return SymbolMarketDataG01(
        symbol=symbol.upper(),
        price=bars[-1].close,
        funding=None,
        open_interest=None,
        volume_24h=sum(b.volume for b in bars[-12:]),
        bars=bars,
        source="binance_spot",
        latency_ms=round((time.time() - started) * 1000, 1),
    )


def _snapshot_fallback(conn: Any, symbol: str, *, hours: int = 2) -> SymbolMarketDataG01 | None:
    from bot.research.market_events.signal_intelligence.trend_history_g38 import snapshot_fallback_bars_g38

    bars = snapshot_fallback_bars_g38(conn, symbol, hours=hours)
    if not bars:
        row = conn.execute(
            """
            SELECT funding, open_interest, btc_price, eth_price, sol_price, bnb_price
            FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1
            """,
        ).fetchone()
        if not row:
            return None
        col = {"BTC": "btc_price", "ETH": "eth_price", "SOL": "sol_price", "BNB": "bnb_price"}.get(symbol.upper())
        price = float(row[col]) if col and row[col] is not None else None
        return SymbolMarketDataG01(
            symbol=symbol.upper(),
            price=price,
            funding=float(row["funding"]) if row["funding"] is not None else None,
            open_interest=float(row["open_interest"]) if row["open_interest"] is not None else None,
            bars=[],
            source="snapshot_db",
        )
    return SymbolMarketDataG01(
        symbol=symbol.upper(),
        price=bars[-1].close,
        bars=bars,
        source="snapshot_bars",
    )


def fetch_symbol_market_data_g01(
    conn: Any,
    symbol: str,
    *,
    end_ts: int | None = None,
    limit: int = 60,
    provider: BinanceMarketProvider | None = None,
) -> SymbolMarketDataG01:
    """Try Binance Futures, then Bybit → OKX → Hyperliquid → Binance Spot → snapshot."""
    ts = end_ts or int(time.time())
    provider = provider or BinanceMarketProvider()
    errors: list[str] = []

    data = _fetch_binance_futures(provider, symbol, end_ts=ts, limit=limit)
    if data and data.bars:
        return data
    errors.append("binance_futures unavailable")

    for fetcher, name in (
        (lambda: _fetch_bybit(symbol, end_ts=ts, limit=limit), "bybit"),
        (lambda: _fetch_okx(symbol, limit=limit), "okx"),
        (lambda: _fetch_hyperliquid(symbol, end_ts=ts, limit=limit), "hyperliquid"),
        (lambda: _fetch_binance_spot(provider, symbol, end_ts=ts, limit=limit), "binance_spot"),
    ):
        try:
            result = fetcher()
            if result and result.bars:
                result.errors = errors
                logger.info("data source fallback %s → %s", symbol, name)
                return result
            errors.append(f"{name} unavailable")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    snap = _snapshot_fallback(conn, symbol)
    if snap:
        snap.errors = errors
        logger.warning("data source snapshot fallback %s", symbol)
        return snap

    return SymbolMarketDataG01(symbol=symbol.upper(), errors=errors)


def fetch_btc_global_metrics_g01(
    conn: Any,
    *,
    end_ts: int | None = None,
    provider: BinanceMarketProvider | None = None,
) -> SymbolMarketDataG01:
    """Funding/OI for snapshot — BTC with full fallback chain."""
    return fetch_symbol_market_data_g01(conn, "BTC", end_ts=end_ts, limit=60, provider=provider)


def upsert_candles_g01(
    conn: Any,
    *,
    symbol: str,
    bars: list[CandleBar],
    source: str,
    venue: str = "binance_futures",
    timeframe: str = "5m",
) -> int:
    """Insert or update candles including volume (fixes NULL volume bug)."""
    if not bars:
        return 0
    now = int(time.time())
    updated = 0
    for b in bars:
        conn.execute(
            """
            INSERT INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume,
              source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, symbol, timeframe, open_ts) DO UPDATE SET
              open = excluded.open,
              high = excluded.high,
              low = excluded.low,
              close = excluded.close,
              volume = excluded.volume,
              source = excluded.source,
              fetched_at = excluded.fetched_at
            """,
            (
                venue, symbol.upper(), timeframe, b.open_ts,
                b.open, b.high, b.low, b.close, b.volume,
                source, now,
            ),
        )
        updated += 1
    return updated


def refresh_symbol_candles_g01(
    conn: Any,
    symbol: str,
    *,
    provider: BinanceMarketProvider | None = None,
    limit: int = 12,
) -> int:
    data = fetch_symbol_market_data_g01(conn, symbol, limit=limit, provider=provider)
    if not data.bars:
        return 0
    venue = "binance_futures" if data.source in ("binance_futures", "bybit", "okx", "hyperliquid") else data.source
    return upsert_candles_g01(conn, symbol=symbol, bars=data.bars[-limit:], source=data.source, venue=venue)


def refresh_universe_candles_g01(
    conn: Any,
    symbols: list[str] | tuple[str, ...],
    *,
    provider: BinanceMarketProvider | None = None,
    limit: int = 12,
) -> int:
    total = 0
    for sym in symbols:
        try:
            total += refresh_symbol_candles_g01(conn, sym, provider=provider, limit=limit)
        except Exception as exc:
            logger.warning("candle refresh failed %s: %s", sym, exc)
    return total


def _volume_score_preview(bars: list[CandleBar]) -> tuple[float, float, float]:
    from bot.research.market_events.signal_intelligence.candidate_g31 import _volume_score

    if len(bars) < 6:
        return 0.0, 0.0, 0.0
    recent = sum(b.volume for b in bars[-5:])
    prior = sum(b.volume for b in bars[-10:-5]) or 1.0
    ratio = recent / prior
    return recent, ratio, _volume_score(bars)


def format_data_source_debug_g01(conn: Any, *, symbols: list[str] | None = None) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    syms = symbols or list(load_g31_universe_symbols(conn))[:8]
    lines = ["Data Source Debug", ""]
    provider = BinanceMarketProvider()

    btc = fetch_btc_global_metrics_g01(conn, provider=provider)
    lines.extend([
        "Binance Futures",
        "",
        "Funding",
        "OK" if btc.funding is not None else "FAIL",
        "",
        "OI",
        "OK" if btc.open_interest is not None else "FAIL",
        "",
        "Candles",
        "OK" if btc.bars else "FAIL",
        "",
        "BTC price",
        "OK" if btc.price is not None else "FAIL",
        "",
        f"Latency {btc.latency_ms}ms",
        f"Source {btc.source}",
        "",
        "---",
        "",
    ])

    for sym in syms:
        data = fetch_symbol_market_data_g01(conn, sym, limit=60, provider=provider)
        bars = data.bars
        if not bars:
            bars_db = conn.execute(
                """
                SELECT open_ts, open, high, low, close, volume
                FROM market_events_historical_candles
                WHERE venue = 'binance_futures' AND symbol = ? AND timeframe = '5m'
                ORDER BY open_ts DESC LIMIT 60
                """,
                (sym.upper(),),
            ).fetchall()
            bars = [
                CandleBar(
                    open_ts=int(r["open_ts"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"] or 0),
                )
                for r in reversed(bars_db)
            ]

        raw, ratio, score = _volume_score_preview(bars)
        lines.extend([
            sym.upper(),
            "",
            "Funding",
            "OK" if data.funding is not None else ("—" if sym != "BTC" else "FAIL"),
            "",
            "Volume",
            "OK" if raw > 0 else "FAIL",
            "",
            "OI",
            "OK" if data.open_interest is not None else ("—" if sym != "BTC" else "FAIL"),
            "",
            "Candles",
            "OK" if bars else "FAIL",
            "",
            f"{sym.upper()} price",
            "OK" if data.price or (bars and bars[-1].close) else "FAIL",
            "",
            "Latency",
            f"{data.latency_ms}ms ({data.source})",
            "",
            "raw volume",
            f"{raw:.2f}",
            "",
            "normalized",
            f"{ratio:.3f}",
            "",
            "score",
            f"{score:.1f}",
            "",
            "---",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_volume_debug_g01(conn: Any, *, symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "XRP")) -> str:
    from bot.research.market_events.signal_intelligence.candles import load_recent_candles

    lines: list[str] = []
    for sym in symbols:
        bars = load_recent_candles(conn, symbol=sym, venue="binance_futures", timeframe="5m", limit=60)
        if len(bars) < 6:
            data = fetch_symbol_market_data_g01(conn, sym, limit=60)
            if data.bars:
                upsert_candles_g01(conn, symbol=sym, bars=data.bars, source=data.source)
                bars = data.bars
        raw, ratio, score = _volume_score_preview(bars)
        lines.extend([
            sym.upper(),
            "",
            "raw volume",
            f"{raw:.2f}",
            "",
            "normalized",
            f"{ratio:.3f}",
            "",
            "score",
            f"{score:.1f}",
            "",
            "---",
            "",
        ])
    return "\n".join(lines).rstrip()
