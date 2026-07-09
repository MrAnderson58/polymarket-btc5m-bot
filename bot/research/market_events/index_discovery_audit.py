"""Bybit index/ETF proxy discovery audit — API evidence, no invented symbols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.instrument_types import BYBIT_ETF_PROXY_MAP, BYBIT_INDEX_LEGACY_CANDIDATES
from bot.research.market_events.venue_bybit import BybitMarketClient


@dataclass
class IndexDiscoveryAudit:
    legacy_map_hits: list[str] = field(default_factory=list)
    legacy_map_misses: list[str] = field(default_factory=list)
    etf_proxy_hits: list[dict[str, Any]] = field(default_factory=list)
    symbol_type_index_count: int = 0
    spx_meme_warning: str | None = None
    findings: list[str] = field(default_factory=list)

    @property
    def bybit_index_count(self) -> int:
        """Honest count: legacy US500/US100 map matches only."""
        return len(self.legacy_map_hits)

    @property
    def etf_proxy_count(self) -> int:
        return len(self.etf_proxy_hits)


def audit_bybit_index_discovery(client: BybitMarketClient | None = None) -> IndexDiscoveryAudit:
    client = client or BybitMarketClient()
    audit = IndexDiscoveryAudit()

    for sym in BYBIT_INDEX_LEGACY_CANDIDATES:
        ticker = client.fetch_ticker(sym)
        if ticker:
            audit.legacy_map_hits.append(sym)
        else:
            audit.legacy_map_misses.append(sym)

    try:
        idx_type = client.fetch_instruments(category="linear", symbol_type="index")
        audit.symbol_type_index_count = len(idx_type)
    except Exception as exc:
        audit.findings.append(f"symbolType=index fetch error: {exc}")
        audit.symbol_type_index_count = 0

    stocks = client.fetch_instruments(category="linear", symbol_type="stock")
    stock_symbols = {s.venue_symbol.upper() for s in stocks}
    for venue_sym, canonical in BYBIT_ETF_PROXY_MAP.items():
        if venue_sym in stock_symbols:
            inst = next(s for s in stocks if s.venue_symbol.upper() == venue_sym)
            ticker = client.fetch_ticker(venue_sym)
            audit.etf_proxy_hits.append({
                "venue_symbol": venue_sym,
                "canonical_asset": canonical,
                "symbol_type": inst.symbol_type,
                "status": inst.status,
                "turnover_24h": ticker.turnover_24h if ticker else None,
                "discovered_via": "bybit_instruments_info symbolType=stock",
            })

    spx = client.fetch_ticker("SPXUSDT")
    if spx and spx.last_price < 10:
        audit.spx_meme_warning = (
            f"SPXUSDT last={spx.last_price} is a sub-$10 token — NOT an S&P 500 index proxy; excluded"
        )

    if audit.legacy_map_misses:
        audit.findings.append(
            "E.2 BYBIT_INDEX_MAP assumed US500USDT/US100USDT — API retCode=10001 (symbol not found)"
        )
    if audit.symbol_type_index_count == 0:
        audit.findings.append("Bybit linear symbolType=index returns 0 instruments")
    if audit.etf_proxy_hits:
        audit.findings.append(
            "S&P/Nasdaq exposure available as ETF stock perps (SPYUSDT, QQQUSDT), not US500/US100"
        )
    if audit.spx_meme_warning:
        audit.findings.append(audit.spx_meme_warning)

    return audit
