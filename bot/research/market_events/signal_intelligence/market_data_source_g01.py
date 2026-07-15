"""Phase G.0.1 — Resilient multi-venue market data (research recorder only)."""

from __future__ import annotations

import json
import logging
import platform
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from bot.research.futures_agent.market_provider import BinanceMarketProvider, symbol_pair
from bot.research.market_events.signal_intelligence.candles import CandleBar

logger = logging.getLogger(__name__)

BYBIT_API = "https://api.bybit.com"
OKX_API = "https://www.okx.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"
FEAR_GREED_API = "https://api.alternative.me/fng/"
_TIMEOUT = (5, 12)
_COOLDOWN_SEC = 30 * 60
_COOLDOWN_HTTP_CODES = frozenset({403, 418, 429, 451})

# Primary-first chain (G.0.3)
PROVIDER_CHAIN_ORDER: tuple[str, ...] = (
    "bybit",
    "okx",
    "hyperliquid",
    "binance_futures",
    "binance_spot",
)
PROVIDER_DISPLAY: dict[str, str] = {
    "bybit": "Bybit",
    "okx": "OKX",
    "hyperliquid": "Hyperliquid",
    "binance_futures": "Binance",
    "binance_spot": "Binance Spot",
    "snapshot_db": "Last Snapshot",
}

_provider_cooldown_until: dict[str, float] = {}
_provider_disable_code: dict[str, int] = {}
_active_provider_id: str = "bybit"


class ProviderDisabledError(Exception):
    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason
        super().__init__(reason)


class ProviderHttpError(Exception):
    def __init__(self, provider_id: str, status: int, detail: str) -> None:
        self.provider_id = provider_id
        self.status = status
        self.detail = detail
        super().__init__(f"{provider_id} HTTP {status}: {detail}")


def _load_provider_state_g03(conn: Any | None) -> None:
    global _provider_cooldown_until, _provider_disable_code, _active_provider_id
    if conn is None:
        return
    try:
        from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state

        raw_cd = get_g3_ops_state(conn, "provider_cooldown_g03")
        raw_code = get_g3_ops_state(conn, "provider_disable_code_g03")
        raw_active = get_g3_ops_state(conn, "active_provider_g03")
        if raw_cd:
            loaded = json.loads(raw_cd)
            now = time.time()
            _provider_cooldown_until = {k: v for k, v in loaded.items() if float(v) > now}
        if raw_code:
            _provider_disable_code = {k: int(v) for k, v in json.loads(raw_code).items()}
        if raw_active:
            _active_provider_id = raw_active
    except Exception as exc:
        logger.debug("provider state load skipped: %s", exc)


# When False, never INSERT/UPDATE provider cooldown / active_provider (read paths).
_allow_provider_state_persist: bool = True


def _save_provider_state_g03(conn: Any | None) -> None:
    if conn is None or not _allow_provider_state_persist:
        return
    try:
        from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state

        set_g3_ops_state(conn, "provider_cooldown_g03", json.dumps(_provider_cooldown_until))
        set_g3_ops_state(conn, "provider_disable_code_g03", json.dumps(_provider_disable_code))
        set_g3_ops_state(conn, "active_provider_g03", _active_provider_id)
    except Exception as exc:
        logger.debug("provider state save skipped: %s", exc)


def is_provider_disabled(provider_id: str) -> tuple[bool, str | None]:
    until = _provider_cooldown_until.get(provider_id, 0)
    if time.time() < until:
        code = _provider_disable_code.get(provider_id)
        remain = int(until - time.time())
        suffix = f" HTTP {code}" if code else ""
        return True, f"disabled{suffix} ({remain}s left)"
    return False, None


def disable_provider(provider_id: str, http_code: int, *, persist: bool | None = None) -> None:
    """Mark provider cooldown in-memory; persist only when provider_state writes allowed."""
    _provider_cooldown_until[provider_id] = time.time() + _COOLDOWN_SEC
    _provider_disable_code[provider_id] = http_code
    logger.warning(
        "provider %s disabled for %ds (HTTP %s)",
        provider_id, _COOLDOWN_SEC, http_code,
    )
    should_persist = _allow_provider_state_persist if persist is None else persist
    if should_persist:
        _save_provider_state_g03(_ops_conn)


def set_active_provider(provider_id: str, conn: Any | None = None) -> None:
    global _active_provider_id
    _active_provider_id = provider_id
    if _allow_provider_state_persist:
        _save_provider_state_g03(conn)


def get_active_provider_id() -> str:
    return _active_provider_id


def get_active_provider_display(conn: Any | None = None) -> str:
    _load_provider_state_g03(conn)
    return PROVIDER_DISPLAY.get(_active_provider_id, _active_provider_id)


def _http_get_provider(provider_id: str, url: str, params: dict | None = None) -> Any:
    disabled, reason = is_provider_disabled(provider_id)
    if disabled:
        raise ProviderDisabledError(provider_id, reason or "disabled")
    resp = requests.get(url, params=params or {}, timeout=_TIMEOUT)
    if resp.status_code in _COOLDOWN_HTTP_CODES:
        # Read paths: do not persist provider_state; still cooldown in-memory for this call chain.
        disable_provider(provider_id, resp.status_code, persist=_allow_provider_state_persist)
        raise ProviderHttpError(provider_id, resp.status_code, (resp.text or "")[:200])
    resp.raise_for_status()
    return resp.json()


def _http_post_provider(provider_id: str, url: str, payload: dict) -> Any:
    disabled, reason = is_provider_disabled(provider_id)
    if disabled:
        raise ProviderDisabledError(provider_id, reason or "disabled")
    resp = requests.post(url, json=payload, timeout=_TIMEOUT)
    if resp.status_code in _COOLDOWN_HTTP_CODES:
        disable_provider(provider_id, resp.status_code, persist=_allow_provider_state_persist)
        raise ProviderHttpError(provider_id, resp.status_code, (resp.text or "")[:200])
    resp.raise_for_status()
    return resp.json()


def _http_get(url: str, params: dict | None = None) -> Any:
    resp = requests.get(url, params=params or {}, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _http_post(url: str, payload: dict) -> Any:
    resp = requests.post(url, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


_ops_conn: Any | None = None


def _bind_ops_conn(conn: Any | None) -> None:
    global _ops_conn
    _ops_conn = conn


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


def _fetch_binance_futures(
    provider: BinanceMarketProvider,
    symbol: str,
    *,
    end_ts: int,
    limit: int,
) -> SymbolMarketDataG01 | None:
    pid = "binance_futures"
    if is_provider_disabled(pid)[0]:
        return None
    from bot.research.futures.config import BINANCE_FUTURES_API

    pair = _pair(symbol)
    started = time.time()
    try:
        klines = _http_get_provider(
            pid,
            f"{BINANCE_FUTURES_API}/fapi/v1/klines",
            {"symbol": pair, "interval": "5m", "endTime": end_ts * 1000, "limit": limit},
        )
        if not klines:
            return None
        fr_data = _http_get_provider(
            pid,
            f"{BINANCE_FUTURES_API}/fapi/v1/fundingRate",
            {"symbol": pair, "endTime": end_ts * 1000, "limit": 1},
        )
        funding = float(fr_data[0]["fundingRate"]) if fr_data else None
        oi_data = _http_get_provider(
            pid,
            f"{BINANCE_FUTURES_API}/fapi/v1/openInterest",
            {"symbol": pair},
        )
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
    except (ProviderDisabledError, ProviderHttpError):
        return None
    except Exception as exc:
        logger.debug("binance futures fetch failed %s: %s", symbol, exc)
        return None


def _fetch_bybit(symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    pair = _pair(symbol)
    try:
        kdata = _http_get_provider(
            "bybit",
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
        ticker = _http_get_provider(
            "bybit",
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
    except (ProviderDisabledError, ProviderHttpError):
        return None
    except Exception as exc:
        logger.debug("bybit fetch failed %s: %s", symbol, exc)
        return None


def _fetch_okx(symbol: str, *, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    inst = _okx_inst(symbol)
    try:
        cdata = _http_get_provider(
            "okx",
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
        fr = _http_get_provider("okx", f"{OKX_API}/api/v5/public/funding-rate", {"instId": inst})
        if fr.get("data"):
            funding = float(fr["data"][0].get("fundingRate") or 0)
        oi_resp = _http_get_provider("okx", f"{OKX_API}/api/v5/public/open-interest", {"instId": inst})
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
    except (ProviderDisabledError, ProviderHttpError):
        return None
    except Exception as exc:
        logger.debug("okx fetch failed %s: %s", symbol, exc)
        return None


def _fetch_hyperliquid(symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    started = time.time()
    coin = _hl_coin(symbol)
    try:
        start_ms = (end_ts - limit * 300) * 1000
        candles = _http_post_provider("hyperliquid", HYPERLIQUID_API, {
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
        meta = _http_post_provider("hyperliquid", HYPERLIQUID_API, {"type": "metaAndAssetCtxs"})
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
    except (ProviderDisabledError, ProviderHttpError):
        return None
    except Exception as exc:
        logger.debug("hyperliquid fetch failed %s: %s", symbol, exc)
        return None


def _fetch_binance_spot(provider: BinanceMarketProvider, symbol: str, *, end_ts: int, limit: int) -> SymbolMarketDataG01 | None:
    pid = "binance_spot"
    if is_provider_disabled(pid)[0]:
        return None
    from bot.research.futures.config import BINANCE_SPOT_API

    started = time.time()
    pair = _pair(symbol)
    try:
        klines = _http_get_provider(
            pid,
            f"{BINANCE_SPOT_API}/api/v3/klines",
            {"symbol": pair, "interval": "5m", "endTime": end_ts * 1000, "limit": limit},
        )
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
    except (ProviderDisabledError, ProviderHttpError):
        return None
    except Exception as exc:
        logger.debug("binance spot fetch failed %s: %s", symbol, exc)
        return None


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


def _provider_fetchers(
    provider: BinanceMarketProvider,
    symbol: str,
    *,
    end_ts: int,
    limit: int,
) -> dict[str, Any]:
    return {
        "bybit": lambda: _fetch_bybit(symbol, end_ts=end_ts, limit=limit),
        "okx": lambda: _fetch_okx(symbol, limit=limit),
        "hyperliquid": lambda: _fetch_hyperliquid(symbol, end_ts=end_ts, limit=limit),
        "binance_futures": lambda: _fetch_binance_futures(provider, symbol, end_ts=end_ts, limit=limit),
        "binance_spot": lambda: _fetch_binance_spot(provider, symbol, end_ts=end_ts, limit=limit),
    }


def fetch_provider_market_data_g03(
    conn: Any,
    provider_id: str,
    symbol: str,
    *,
    end_ts: int | None = None,
    limit: int = 12,
    provider: BinanceMarketProvider | None = None,
    persist_state: bool = False,
) -> SymbolMarketDataG01 | None:
    """Fetch from a single provider (for status probes). Default: no provider_state writes."""
    global _allow_provider_state_persist
    prev = _allow_provider_state_persist
    _allow_provider_state_persist = bool(persist_state)
    try:
        ts = end_ts or int(time.time())
        provider = provider or BinanceMarketProvider()
        _load_provider_state_g03(conn)
        _bind_ops_conn(conn if persist_state else None)
        fetchers = _provider_fetchers(provider, symbol, end_ts=ts, limit=limit)
        if provider_id == "snapshot_db":
            return _snapshot_fallback(conn, symbol)
        fn = fetchers.get(provider_id)
        if fn is None:
            return None
        if is_provider_disabled(provider_id)[0]:
            return None
        return fn()
    finally:
        _allow_provider_state_persist = prev
        if not persist_state:
            _bind_ops_conn(None)


def fetch_symbol_market_data_g01(
    conn: Any,
    symbol: str,
    *,
    end_ts: int | None = None,
    limit: int = 60,
    provider: BinanceMarketProvider | None = None,
    persist_state: bool = True,
) -> SymbolMarketDataG01:
    """Try Bybit → OKX → Hyperliquid → Binance Futures → Binance Spot → snapshot.

    persist_state=False: in-memory provider selection only — never write provider_state
    (required for pure RO telegram/CLI reads such as /decision).
    """
    global _allow_provider_state_persist
    prev_persist = _allow_provider_state_persist
    _allow_provider_state_persist = bool(persist_state)
    try:
        ts = end_ts or int(time.time())
        provider = provider or BinanceMarketProvider()
        _load_provider_state_g03(conn)
        _bind_ops_conn(conn if persist_state else None)
        errors: list[str] = []
        fetchers = _provider_fetchers(provider, symbol, end_ts=ts, limit=limit)

        for pid in PROVIDER_CHAIN_ORDER:
            disabled, reason = is_provider_disabled(pid)
            if disabled:
                errors.append(f"{pid}: {reason}")
                continue
            try:
                result = fetchers[pid]()
                if result and result.bars:
                    set_active_provider(pid, conn if persist_state else None)
                    result.errors = list(errors)
                    if pid != PROVIDER_CHAIN_ORDER[0]:
                        logger.info("data source fallback %s → %s", symbol, pid)
                    _save_provider_state_g03(conn if persist_state else None)
                    return result
                reason_detail = "no bars returned"
                if result and (result.funding is not None or result.open_interest is not None):
                    reason_detail = "klines empty but metrics present"
                errors.append(f"{pid}: {reason_detail}")
            except (ProviderDisabledError, ProviderHttpError) as exc:
                errors.append(f"{pid}: {exc}")
            except Exception as exc:
                errors.append(f"{pid}: {exc}")

        snap = _snapshot_fallback(conn, symbol)
        if snap:
            set_active_provider("snapshot_db", conn if persist_state else None)
            snap.errors = errors
            logger.warning("data source snapshot fallback %s", symbol)
            _save_provider_state_g03(conn if persist_state else None)
            return snap

        _save_provider_state_g03(conn if persist_state else None)
        return SymbolMarketDataG01(symbol=symbol.upper(), errors=errors)
    finally:
        _allow_provider_state_persist = prev_persist
        if not persist_state:
            _bind_ops_conn(None)


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
    primary_label = PROVIDER_DISPLAY.get(PROVIDER_CHAIN_ORDER[0], "Bybit")
    lines.extend([
        primary_label,
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
        f"Active provider {get_active_provider_display(conn)}",
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


# ---------------------------------------------------------------------------
# Phase G.0.3 — Provider status / cooldown
# ---------------------------------------------------------------------------


@dataclass
class ProviderStatusRowG03:
    provider_id: str
    display: str
    state: str
    funding: str | None = None
    oi: str | None = None
    candles: str | None = None
    latency_ms: float | None = None
    disable_code: int | None = None


def probe_provider_status_g03(
    conn: Any,
    provider_id: str,
    *,
    symbol: str = "BTC",
    limit: int = 12,
) -> ProviderStatusRowG03:
    _load_provider_state_g03(conn)
    display = PROVIDER_DISPLAY.get(provider_id, provider_id)
    disabled, _ = is_provider_disabled(provider_id)
    if disabled:
        return ProviderStatusRowG03(
            provider_id=provider_id,
            display=display,
            state="DISABLED",
            disable_code=_provider_disable_code.get(provider_id),
        )
    if provider_id == "snapshot_db":
        snap = _snapshot_fallback(conn, symbol)
        ok = bool(snap and (snap.bars or snap.price is not None))
        return ProviderStatusRowG03(
            provider_id=provider_id,
            display=display,
            state="ACTIVE" if ok else "UNAVAILABLE",
            candles="OK" if snap and snap.bars else "FAIL",
        )

    data = fetch_provider_market_data_g03(conn, provider_id, symbol, limit=limit)
    if data and data.bars:
        return ProviderStatusRowG03(
            provider_id=provider_id,
            display=display,
            state="ACTIVE",
            funding="OK" if data.funding is not None else "—",
            oi="OK" if data.open_interest is not None else "—",
            candles="OK",
            latency_ms=data.latency_ms,
        )
    return ProviderStatusRowG03(
        provider_id=provider_id,
        display=display,
        state="UNAVAILABLE",
    )


def format_provider_status_g03(conn: Any, *, symbol: str = "BTC") -> str:
    _load_provider_state_g03(conn)
    _bind_ops_conn(conn)
    active = get_active_provider_id()
    lines = ["Provider Status", ""]
    for pid in (*PROVIDER_CHAIN_ORDER, "snapshot_db"):
        row = probe_provider_status_g03(conn, pid, symbol=symbol)
        lines.append("Provider")
        lines.append("")
        lines.append(row.display)
        if row.disable_code is not None:
            lines.append("")
            lines.append(str(row.disable_code))
        if row.funding is not None:
            lines.extend(["", "Funding", row.funding])
        if row.oi is not None:
            lines.extend(["", "OI", row.oi])
        if row.candles is not None:
            lines.extend(["", "Candles", row.candles])
        if row.latency_ms is not None:
            lines.extend(["", "Latency", f"{row.latency_ms}ms"])
        lines.extend(["", row.state])
        if pid == active and row.state == "ACTIVE":
            lines.append("(current)")
        lines.extend(["", "----------", ""])
    _save_provider_state_g03(conn)
    return "\n".join(lines).rstrip()


def probe_active_provider_quick_g03(conn: Any) -> str:
    _load_provider_state_g03(conn)
    pid = get_active_provider_id()
    disabled, reason = is_provider_disabled(pid)
    if disabled:
        code = _provider_disable_code.get(pid)
        return f"DISABLED ({code})" if code else f"DISABLED ({reason})"
    data = fetch_provider_market_data_g03(conn, pid, "BTC", limit=3)
    if data and data.bars:
        return f"OK ({data.latency_ms}ms)"
    return "FAIL"


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


# ---------------------------------------------------------------------------
# Phase G.0.2 — Environment / API diagnostics
# ---------------------------------------------------------------------------


@dataclass
class EndpointProbeG02:
    name: str
    method: str
    url: str
    curl: str
    dns_ok: bool = False
    dns_ms: float | None = None
    dns_error: str | None = None
    tcp_ok: bool = False
    tcp_ms: float | None = None
    tcp_error: str | None = None
    https_ok: bool = False
    latency_ms: float | None = None
    http_status: int | None = None
    json_ok: bool = False
    json_error: str | None = None
    error_class: str | None = None
    error_detail: str | None = None
    body_preview: str | None = None


def _curl_command(*, method: str, url: str, params: dict | None = None, payload: dict | None = None) -> str:
    if method == "POST":
        body = json.dumps(payload or {}, separators=(",", ":"))
        return (
            f"curl -sS -m 15 -X POST '{url}' "
            f"-H 'Content-Type: application/json' -d '{body}'"
        )
    if params:
        sep = "&" if "?" in url else "?"
        return f"curl -sS -m 15 '{url}{sep}{urlencode(params)}'"
    return f"curl -sS -m 15 '{url}'"


def _classify_http_error(status: int | None, exc: Exception | None = None) -> tuple[str, str]:
    if status == 403:
        return "403 Forbidden", f"HTTP {status}"
    if status == 418:
        return "418 I'm a teapot (rate limit)", f"HTTP {status}"
    if status == 451:
        return "451 Unavailable For Legal Reasons", f"HTTP {status}"
    if status == 429:
        return "429 Too Many Requests", f"HTTP {status}"
    if status and status >= 500:
        return f"HTTP {status} Server Error", f"HTTP {status}"
    if exc is None:
        return "Unknown", "no exception"
    if isinstance(exc, requests.exceptions.Timeout):
        return "Timeout", str(exc)
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSL error", str(exc)
    if isinstance(exc, json.JSONDecodeError):
        return "Invalid JSON", str(exc)
    if isinstance(exc, requests.exceptions.ConnectionError):
        msg = str(exc).lower()
        if "name or service not known" in msg or "nodename nor servname" in msg or "getaddrinfo failed" in msg:
            return "DNS error", str(exc)
        if "connection refused" in msg:
            return "Connection refused", str(exc)
        return "Connection error", str(exc)
    if isinstance(exc, requests.exceptions.HTTPError):
        code = exc.response.status_code if exc.response is not None else status
        return _classify_http_error(code)[0], str(exc)
    return type(exc).__name__, str(exc)


def probe_endpoint_g02(
    *,
    name: str,
    url: str,
    method: str = "GET",
    params: dict | None = None,
    payload: dict | None = None,
    timeout: tuple[float, float] = _TIMEOUT,
) -> EndpointProbeG02:
    probe = EndpointProbeG02(
        name=name,
        method=method,
        url=url,
        curl=_curl_command(method=method, url=url, params=params, payload=payload),
    )
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    t0 = time.time()
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        probe.dns_ok = True
        probe.dns_ms = round((time.time() - t0) * 1000, 1)
    except OSError as exc:
        probe.dns_error = str(exc)
        probe.error_class, probe.error_detail = "DNS error", str(exc)
        return probe

    t1 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout[0]):
            probe.tcp_ok = True
            probe.tcp_ms = round((time.time() - t1) * 1000, 1)
    except OSError as exc:
        probe.tcp_error = str(exc)
        err_cls, _ = _classify_http_error(None, exc)
        probe.error_class = err_cls if err_cls != "Unknown" else "Connection refused"
        probe.error_detail = str(exc)
        return probe

    t2 = time.time()
    try:
        if method == "POST":
            resp = requests.post(url, json=payload or {}, timeout=timeout)
        else:
            resp = requests.get(url, params=params or {}, timeout=timeout)
        probe.latency_ms = round((time.time() - t2) * 1000, 1)
        probe.http_status = resp.status_code
        if resp.status_code >= 400:
            probe.error_class, probe.error_detail = _classify_http_error(resp.status_code)
            probe.body_preview = (resp.text or "")[:200]
            return probe
        probe.https_ok = True
        try:
            data = resp.json()
            probe.json_ok = True
            probe.body_preview = json.dumps(data)[:200]
        except json.JSONDecodeError as exc:
            probe.json_error = str(exc)
            probe.error_class, probe.error_detail = _classify_http_error(None, exc)
            probe.body_preview = (resp.text or "")[:200]
    except Exception as exc:
        probe.latency_ms = round((time.time() - t2) * 1000, 1)
        probe.error_class, probe.error_detail = _classify_http_error(probe.http_status, exc)
    return probe


def _api_test_endpoints_g02(*, symbol: str = "BTC") -> list[EndpointProbeG02]:
    from bot.research.futures.config import BINANCE_FUTURES_API, BINANCE_SPOT_API

    pair = symbol_pair(symbol)
    ts = int(time.time()) * 1000
    hl_payload = {
        "type": "candleSnapshot",
        "req": {"coin": symbol.upper(), "interval": "5m", "startTime": ts - 3600000, "endTime": ts},
    }
    return [
        probe_endpoint_g02(
            name="Binance Futures (klines)",
            url=f"{BINANCE_FUTURES_API}/fapi/v1/klines",
            params={"symbol": pair, "interval": "5m", "limit": 3},
        ),
        probe_endpoint_g02(
            name="Binance Futures (funding)",
            url=f"{BINANCE_FUTURES_API}/fapi/v1/fundingRate",
            params={"symbol": pair, "limit": 1},
        ),
        probe_endpoint_g02(
            name="Binance Futures (openInterest)",
            url=f"{BINANCE_FUTURES_API}/fapi/v1/openInterest",
            params={"symbol": pair},
        ),
        probe_endpoint_g02(
            name="Binance Spot (klines)",
            url=f"{BINANCE_SPOT_API}/api/v3/klines",
            params={"symbol": pair, "interval": "5m", "limit": 3},
        ),
        probe_endpoint_g02(
            name="Bybit (kline)",
            url=f"{BYBIT_API}/v5/market/kline",
            params={"category": "linear", "symbol": pair, "interval": "5", "limit": 3},
        ),
        probe_endpoint_g02(
            name="Bybit (tickers)",
            url=f"{BYBIT_API}/v5/market/tickers",
            params={"category": "linear", "symbol": pair},
        ),
        probe_endpoint_g02(
            name="OKX (candles)",
            url=f"{OKX_API}/api/v5/market/candles",
            params={"instId": _okx_inst(symbol), "bar": "5m", "limit": 3},
        ),
        probe_endpoint_g02(
            name="OKX (funding-rate)",
            url=f"{OKX_API}/api/v5/public/funding-rate",
            params={"instId": _okx_inst(symbol)},
        ),
        probe_endpoint_g02(
            name="Hyperliquid (candles)",
            url=HYPERLIQUID_API,
            method="POST",
            payload=hl_payload,
        ),
        probe_endpoint_g02(
            name="Fear & Greed",
            url=FEAR_GREED_API,
            params={"limit": 1},
        ),
    ]


def _probe_fetch_result_g02(name: str, fn) -> tuple[str, str | None, SymbolMarketDataG01 | None]:
    try:
        result = fn()
        if result and result.bars:
            return name, None, result
        detail = "no bars returned"
        if result and result.errors:
            detail = "; ".join(result.errors)
        return name, detail, result
    except Exception as exc:
        err_cls, detail = _classify_http_error(None, exc)
        return name, f"{err_cls}: {detail}", None


def probe_fallback_chain_g02(conn: Any, *, symbol: str = "BTC") -> list[tuple[str, str | None, SymbolMarketDataG01 | None]]:
    provider = BinanceMarketProvider()
    ts = int(time.time())
    limit = 12
    return [
        _probe_fetch_result_g02("bybit", lambda: _fetch_bybit(symbol, end_ts=ts, limit=limit)),
        _probe_fetch_result_g02("okx", lambda: _fetch_okx(symbol, limit=limit)),
        _probe_fetch_result_g02("hyperliquid", lambda: _fetch_hyperliquid(symbol, end_ts=ts, limit=limit)),
        _probe_fetch_result_g02("binance_futures", lambda: _fetch_binance_futures(provider, symbol, end_ts=ts, limit=limit)),
        _probe_fetch_result_g02("binance_spot", lambda: _fetch_binance_spot(provider, symbol, end_ts=ts, limit=limit)),
        _probe_fetch_result_g02("snapshot_db", lambda: _snapshot_fallback(conn, symbol)),
    ]


def format_endpoint_probe_g02(probe: EndpointProbeG02) -> list[str]:
    ok = probe.https_ok and probe.json_ok
    lines = [
        probe.name,
        "PASS" if ok else "FAIL",
        "",
        "DNS",
        "OK" if probe.dns_ok else "FAIL",
        f"{probe.dns_ms}ms" if probe.dns_ms is not None else (probe.dns_error or ""),
        "",
        "TCP",
        "OK" if probe.tcp_ok else "FAIL",
        f"{probe.tcp_ms}ms" if probe.tcp_ms is not None else (probe.tcp_error or ""),
        "",
        "HTTPS",
        "OK" if probe.https_ok else "FAIL",
        "",
        "Latency",
        f"{probe.latency_ms}ms" if probe.latency_ms is not None else "—",
        "",
        "HTTP status",
        str(probe.http_status if probe.http_status is not None else "—"),
        "",
        "JSON parsed",
        "OK" if probe.json_ok else "FAIL",
    ]
    if probe.error_class:
        lines.extend(["", "Error class", probe.error_class])
    if probe.error_detail:
        lines.extend(["", "Error detail", probe.error_detail])
    if probe.json_error:
        lines.extend(["", "JSON error", probe.json_error])
    if probe.body_preview and not ok:
        lines.extend(["", "Body preview", probe.body_preview[:200]])
    lines.extend(["", "curl", "", probe.curl])
    return lines


def format_api_test_g02(conn: Any, *, symbol: str = "BTC") -> str:
    parts: list[str] = ["API Test", "", f"Symbol {symbol.upper()}", ""]
    for probe in _api_test_endpoints_g02(symbol=symbol):
        parts.extend(format_endpoint_probe_g02(probe))
        parts.extend(["", "---", ""])

    parts.extend(["Fallback chain (fetch functions)", ""])
    chain = probe_fallback_chain_g02(conn, symbol=symbol)
    any_ok = False
    for name, err, data in chain:
        if data and data.bars:
            parts.extend([
                name,
                "PASS",
                f"bars={len(data.bars)} source={data.source}",
                f"funding={data.funding} oi={data.open_interest}",
                "",
            ])
            any_ok = True
        else:
            parts.extend([name, "FAIL", err or "unavailable", ""])
    combined = fetch_symbol_market_data_g01(conn, symbol, limit=12)
    parts.extend([
        "Combined fetch_symbol_market_data",
        "PASS" if combined.bars else "FAIL",
        f"source={combined.source} bars={len(combined.bars)}",
        "",
        "Chain errors",
        "\n".join(combined.errors) if combined.errors else "(none)",
        "",
    ])
    if not any_ok and not combined.bars:
        parts.extend([
            "Fallback diagnosis",
            "All providers failed — compare DNS/TCP/HTTPS sections above per provider.",
            "Mac mini: check geo-block (451), HTTP_PROXY, system clock, firewall.",
            "",
        ])
    return "\n".join(parts).rstrip()


def probe_binance_futures_quick_g02() -> EndpointProbeG02:
    from bot.research.futures.config import BINANCE_FUTURES_API

    return probe_endpoint_g02(
        name="Binance Futures quick",
        url=f"{BINANCE_FUTURES_API}/fapi/v1/klines",
        params={"symbol": "BTCUSDT", "interval": "5m", "limit": 2},
    )


def format_env_debug_g02(conn: Any) -> str:
    import importlib.metadata
    import os

    from bot.research.futures.config import BINANCE_FUTURES_API, BINANCE_SPOT_API
    from bot.research.market_events.db import connection_is_postgres
    from bot.research.market_events.db_config import resolve_market_events_db_config

    cfg = resolve_market_events_db_config()
    backend = "postgresql" if cfg.backend == "postgresql" else "sqlite"
    try:
        ccxt_ver = importlib.metadata.version("ccxt")
    except importlib.metadata.PackageNotFoundError:
        ccxt_ver = "not installed"

    providers = [
        "bybit (primary)",
        "okx (fallback 1)",
        "hyperliquid (fallback 2)",
        "binance_futures (fallback 3)",
        "binance_spot (fallback 4)",
        "snapshot_db (fallback 5)",
    ]

    proxy_parts = [
        f"{k}={v}" for k, v in (
            ("HTTP_PROXY", os.environ.get("HTTP_PROXY")),
            ("HTTPS_PROXY", os.environ.get("HTTPS_PROXY")),
            ("ALL_PROXY", os.environ.get("ALL_PROXY")),
            ("NO_PROXY", os.environ.get("NO_PROXY")),
        ) if v
    ]

    lines = [
        "Environment Debug",
        "",
        "Backend",
        backend,
        "",
        "Python version",
        sys.version.replace("\n", " "),
        "",
        "OS",
        f"{platform.system()} {platform.release()} ({platform.machine()})",
        "",
        "requests version",
        requests.__version__,
        "",
        "ccxt version",
        ccxt_ver,
        "",
        "Database",
        cfg.url if backend == "postgresql" else str(cfg.sqlite_path),
        f"source: {cfg.config_source}",
        "",
        "Connection type",
        "PostgresBackend" if connection_is_postgres(conn) else "sqlite3",
        "",
        "Data providers",
        "\n".join(providers),
        "",
        "URLs",
        "",
        "Binance Futures",
        BINANCE_FUTURES_API,
        "",
        "Binance Spot",
        BINANCE_SPOT_API,
        "",
        "Bybit",
        BYBIT_API,
        "",
        "OKX",
        OKX_API,
        "",
        "Hyperliquid",
        HYPERLIQUID_API,
        "",
        "Fear & Greed",
        FEAR_GREED_API,
        "",
        "HTTP proxy",
        ", ".join(proxy_parts) if proxy_parts else "none",
        "",
        "SSL certs",
        requests.certs.where(),
        "",
        "Git hint",
        "Run: git rev-parse HEAD  (expect 44791f3 on both machines)",
    ]
    return "\n".join(lines)
