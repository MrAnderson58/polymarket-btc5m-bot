"""Phase F.2 Task F — cross-asset correlation engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.instrument_types import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_INDEX,
)

VERDICT_BROKEN = "CORRELATION_BROKEN"
VERDICT_CONFIRMED = "MARKET_CONFIRMED"
VERDICT_NEUTRAL = "NEUTRAL"

CRYPTO_PEERS = ("BTC", "ETH", "TOTAL3")
COMMODITY_PEERS = ("DXY", "SILVER", "OIL")
NASDAQ_PEERS = ("SP500_PROXY", "NASDAQ100_PROXY", "VIX_PROXY")

# Aliases for symbols not in DB — map to proxies we can query
PEER_ALIASES = {
    "SPX": "SP500_PROXY",
    "QQQ": "NASDAQ100_PROXY",
    "VIX": "VIX_PROXY",
    "GOLD": "GOLD",
    "XAU": "GOLD",
}


@dataclass(frozen=True)
class CorrelationResult:
    verdict: str
    peers: list[dict[str, Any]]
    asset_class: str


def _recent_return(conn: Any, symbol: str, window_sec: int = 900) -> float | None:
    row = conn.execute(
        """
        SELECT return_pct FROM market_events
        WHERE symbol = ? AND event_ts >= ?
        ORDER BY event_ts DESC LIMIT 1
        """,
        (symbol, int(__import__("time").time()) - window_sec * 4),
    ).fetchone()
    if row:
        return float(row["return_pct"])
    from bot.research.market_events.signal_intelligence.candles import load_recent_candles
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=6)
    if len(bars) < 2:
        return None
    n = max(1, window_sec // 300)
    if len(bars) <= n:
        return None
    start, end = bars[-n - 1].close, bars[-1].close
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _resolve_peers(symbol: str, asset_class: str) -> tuple[str, ...]:
    sym = PEER_ALIASES.get(symbol, symbol)
    if asset_class == ASSET_CLASS_CRYPTO or sym not in ("GOLD", "SILVER", "OIL", "SP500_PROXY", "NASDAQ100_PROXY"):
        if sym in ("BTC", "ETH"):
            return ("ETH", "TOTAL3") if sym == "BTC" else ("BTC", "TOTAL3")
        return CRYPTO_PEERS
    if asset_class == ASSET_CLASS_COMMODITY or sym in ("GOLD", "SILVER", "OIL"):
        return COMMODITY_PEERS
    if asset_class in (ASSET_CLASS_EQUITY, ASSET_CLASS_INDEX) or "PROXY" in sym:
        return NASDAQ_PEERS
    return CRYPTO_PEERS


def _asset_class(conn: Any, symbol: str) -> str:
    row = conn.execute(
        """
        SELECT asset_class FROM market_events_instruments
        WHERE canonical_asset = ? AND active = 1 LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row:
        return str(row["asset_class"])
    if symbol in ("GOLD", "SILVER", "OIL"):
        return ASSET_CLASS_COMMODITY
    if symbol in ("SP500_PROXY", "NASDAQ100_PROXY", "TSLA", "NVDA", "AAPL"):
        return ASSET_CLASS_EQUITY
    return ASSET_CLASS_CRYPTO


def analyze_correlation(
    conn: Any,
    *,
    symbol: str,
    shock_return_pct: float,
) -> CorrelationResult:
    asset_class = _asset_class(conn, symbol)
    peers = _resolve_peers(symbol, asset_class)
    peer_data: list[dict[str, Any]] = []
    aligned = 0
    checked = 0

    for peer in peers:
        if peer == symbol:
            continue
        if peer == "TOTAL3":
            ret = _median_crypto_return(conn)
            label = "TOTAL3"
        else:
            ret = _recent_return(conn, peer)
            label = peer
        if ret is None:
            peer_data.append({"peer": label, "return_pct": None, "aligned": None})
            continue
        checked += 1
        same = (ret >= 0) == (shock_return_pct >= 0)
        magnitude_ok = abs(ret) >= max(0.3, abs(shock_return_pct) * 0.25)
        ok = same and magnitude_ok
        if ok:
            aligned += 1
        peer_data.append({
            "peer": label,
            "return_pct": round(ret, 2),
            "aligned": ok,
        })

    shock_move = abs(shock_return_pct)
    if checked == 0:
        verdict = VERDICT_NEUTRAL
    elif shock_move >= 2.0 and aligned == 0:
        verdict = VERDICT_BROKEN
    elif aligned >= max(1, checked // 2):
        verdict = VERDICT_CONFIRMED
    elif shock_move >= 1.5 and aligned == 0:
        verdict = VERDICT_BROKEN
    else:
        verdict = VERDICT_NEUTRAL

    return CorrelationResult(verdict=verdict, peers=peer_data, asset_class=asset_class)


def _median_crypto_return(conn: Any) -> float | None:
    rows = conn.execute(
        """
        SELECT return_pct FROM market_events
        WHERE classification = 'ASSET_SPECIFIC' AND event_ts >= ?
        ORDER BY event_ts DESC LIMIT 20
        """,
        (int(__import__("time").time()) - 3600,),
    ).fetchall()
    if not rows:
        return None
    vals = sorted(float(r["return_pct"]) for r in rows)
    return vals[len(vals) // 2]
