"""S46.1 — live market quotes for context enrichment (requests-only, no collector changes)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; polymarket-ai-analyst/1.0; +research)"
_TIMEOUT = 15.0

# Yahoo Finance chart symbols
_YAHOO = {
    "btc": "BTC-USD",
    "spx": "^GSPC",
    "nasdaq": "^NDX",
    "qqq": "QQQ",
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",  # 10Y yield %
    "us02y": "2YY=F",  # 2Y yield futures proxy %
    "gold": "GC=F",
    "oil": "CL=F",
}


def _trend(change: float | None, *, eps: float = 1e-6) -> str:
    if change is None:
        return "Neutral"
    if change > eps:
        return "Bullish"
    if change < -eps:
        return "Bearish"
    return "Neutral"


def _metric(
    value: float | None,
    prev: float | None,
    *,
    source: str,
    unit: str = "",
    asof_ts: int | None = None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    change = None
    change_pct = None
    if prev is not None and prev != 0:
        change = round(float(value) - float(prev), 6)
        change_pct = round(100.0 * (float(value) - float(prev)) / float(prev), 4)
    out: dict[str, Any] = {
        "value": round(float(value), 6),
        "change_24h": round(change, 6) if change is not None else 0.0,
        "trend": _trend(change),
        "source": source,
    }
    if change_pct is not None:
        out["change_24h_pct"] = change_pct
    if unit:
        out["unit"] = unit
    if asof_ts:
        out["asof_ts"] = int(asof_ts)
    if prev is not None:
        out["prev"] = round(float(prev), 6)
    return out


def fetch_yahoo_series(symbol: str) -> tuple[float | None, float | None, int | None]:
    """Return (last, prev_close_or_prior_day, asof_ts)."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?interval=1d&range=10d"
    )
    try:
        resp = requests.get(
            url,
            timeout=_TIMEOUT,
            headers={"User-Agent": _UA, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        result = ((data.get("chart") or {}).get("result") or [None])[0]
        if not result:
            return None, None, None
        meta = result.get("meta") or {}
        indicators = (result.get("indicators") or {}).get("quote") or [{}]
        bar = indicators[0] if indicators else {}
        closes = [c for c in (bar.get("close") or []) if c is not None]
        last = meta.get("regularMarketPrice")
        if last is None and closes:
            last = closes[-1]
        prev = None
        if len(closes) >= 2:
            prev = closes[-2]
        elif meta.get("chartPreviousClose") is not None:
            prev = meta.get("chartPreviousClose")
        asof = meta.get("regularMarketTime") or int(time.time())
        return (
            float(last) if last is not None else None,
            float(prev) if prev is not None else None,
            int(asof) if asof else None,
        )
    except Exception as exc:
        logger.warning("yahoo fetch failed symbol=%s: %s", symbol, exc)
        return None, None, None


def fetch_live_macro_quotes() -> dict[str, dict[str, Any]]:
    """Fetch SPX/Nasdaq/VIX/DXY/yields/gold/oil as normalized metrics."""
    out: dict[str, dict[str, Any]] = {}
    units = {
        "btc": "USD",
        "us10y": "%",
        "us02y": "%",
        "vix": "index",
        "dxy": "index",
        "spx": "index",
        "nasdaq": "index",
        "qqq": "USD",
        "gold": "USD/oz",
        "oil": "USD/bbl",
    }
    for key, symbol in _YAHOO.items():
        last, prev, asof = fetch_yahoo_series(symbol)
        metric = _metric(
            last,
            prev,
            source=f"yahoo:{symbol}",
            unit=units.get(key, ""),
            asof_ts=asof,
        )
        if metric:
            out[key] = metric
    return out


def _parse_farside_daily_flows(html: str) -> list[float]:
    m = re.search(r"totalData\s*=\s*\[([^\]]+)\]", html)
    if not m:
        return []
    try:
        cumul = [float(x) for x in m.group(1).split(",") if x.strip()]
    except Exception:
        return []
    if not cumul:
        return []
    daily = [cumul[0]]
    for i in range(1, len(cumul)):
        daily.append(cumul[i] - cumul[i - 1])
    return daily


def fetch_etf_netflows() -> dict[str, Any]:
    """
    BTC/ETH US spot ETF net flows from Farside (USD millions).
    Returns normalized numeric block (not news headlines).
    """
    result: dict[str, Any] = {
        "unit": "USD_millions",
        "source": "farside.co.uk",
        "asof_ts": int(time.time()),
    }
    for asset, path in (("btc_etf", "btc"), ("eth_etf", "eth")):
        try:
            resp = requests.get(
                f"https://farside.co.uk/{path}/",
                timeout=_TIMEOUT,
                headers={"User-Agent": _UA},
            )
            resp.raise_for_status()
            daily = _parse_farside_daily_flows(resp.text)
            if not daily:
                continue
            # Drop trailing zeros (weekend / unpublished)
            trimmed = list(daily)
            while trimmed and abs(trimmed[-1]) < 1e-9 and len(trimmed) > 5:
                trimmed.pop()
            last = trimmed[-1] if trimmed else None
            d5 = round(sum(trimmed[-5:]), 2) if len(trimmed) >= 1 else None
            d30 = round(sum(trimmed[-30:]), 2) if len(trimmed) >= 1 else None
            result[asset] = {
                "netflow_1d": round(float(last), 2) if last is not None else None,
                "netflow_5d": d5,
                "netflow_30d": d30,
                "trend": _trend(d5),
                "source": f"farside:{path}",
            }
        except Exception as exc:
            logger.warning("farside %s failed: %s", path, exc)
    return result


def metric_from_values(
    value: float | None,
    prev: float | None,
    *,
    source: str,
    unit: str = "",
    asof_ts: int | None = None,
) -> dict[str, Any] | None:
    return _metric(value, prev, source=source, unit=unit, asof_ts=asof_ts)


def trend_from_change(change: float | None, *, eps: float = 1e-6) -> str:
    return _trend(change, eps=eps)


def fetch_all_live_enrichment() -> dict[str, Any]:
    """One-shot enrichment payload for context_builder (always live; no cache)."""
    t0 = time.perf_counter()
    fetched_at = int(time.time())
    quotes = fetch_live_macro_quotes()
    etf = fetch_etf_netflows()
    return {
        "quotes": quotes,
        "etf": etf,
        "fetched_at": fetched_at,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
    }


def refresh_market_data() -> dict[str, Any]:
    """Explicit live refresh — call before /report context build."""
    return fetch_all_live_enrichment()
