"""OKX public market API (minimal, read-only)."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

OKX_API = "https://www.okx.com"


def check_symbol_exists(symbol: str, *, timeout: float = 8.0) -> bool:
    """Check if USDT-SWAP exists for base coin (e.g. BTC -> BTC-USDT-SWAP)."""
    inst = f"{symbol.upper()}-USDT-SWAP"
    try:
        resp = requests.get(
            f"{OKX_API}/api/v5/market/ticker",
            params={"instId": inst},
            timeout=timeout,
        )
        data = resp.json()
        return bool(data.get("code") == "0" and data.get("data"))
    except requests.RequestException as exc:
        logger.debug("okx symbol check failed: %s", exc)
        return False


def fetch_ticker_metrics(symbol: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
    inst = f"{symbol.upper()}-USDT-SWAP"
    try:
        resp = requests.get(
            f"{OKX_API}/api/v5/market/ticker",
            params={"instId": inst},
            timeout=timeout,
        )
        data = resp.json()
        if data.get("code") != "0" or not data.get("data"):
            return None
        row = data["data"][0]
        bid = float(row.get("bidPx") or 0)
        ask = float(row.get("askPx") or 0)
        spread_bps = ((ask - bid) / ask * 10000.0) if ask > 0 and bid > 0 else None
        return {
            "venue": "okx",
            "symbol": inst,
            "last_price": float(row.get("last") or 0),
            "volume_24h": float(row.get("vol24h") or 0),
            "spread_bps": spread_bps,
        }
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.debug("okx ticker fetch failed: %s", exc)
        return None
