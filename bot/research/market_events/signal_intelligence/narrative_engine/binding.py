"""S45 — weighted event→asset binding (no generic-crypto → BTC dump)."""

from __future__ import annotations

import re
from typing import Any

from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    detect_symbols,
    source_quality,
    watched_symbols,
)

# Entity / theme → preferred assets (never "crypto ⇒ BTC").
_ENTITY_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("circle", "usdc", "centre consortium"), ("ETH", "AAVE")),
    (("tether", "usdt"), ("BTC", "TRX")),
    (("stablecoin", "stable coin"), ("ETH", "AAVE", "XRP")),
    (("solana staking", "sol staking", "stake sol"), ("SOL",)),
    (("mica", "mi ca", "markets in crypto-assets"), ("XRP", "BNB", "ETH")),
    (("blackrock", "ibit", "spot bitcoin etf", "bitcoin etf"), ("BTC",)),
    (("ethereum etf", "ether etf", "eth etf"), ("ETH",)),
    (("hyperliquid", " hype "), ("HYPE",)),
    (("arbitrum", " arb "), ("ARB", "ETH")),
    (("optimism", " op mainnet"), ("OP", "ETH")),
    (("aave",), ("AAVE", "ETH")),
    (("chainlink", " link "), ("LINK",)),
    (("avalanche", " avax "), ("AVAX",)),
    (("dogecoin", " doge "), ("DOGE",)),
    (("ripple", " xrp ", "sec v ripple"), ("XRP",)),
    (("binance", " bnb "), ("BNB",)),
    (("cardano", " ada "), ("ADA",)),
    (("sui ", " sui."), ("SUI",)),
    (("injective", " inj "), ("INJ",)),
    (("toncoin", " telegram ton"), ("TON",)),
    (("ethena", " ena "), ("ENA", "ETH")),
    (("cosmos", " atom "), ("ATOM",)),
    (("layer 2", "layer-2", " l2 ", "rollup"), ("ETH", "ARB", "OP")),
    (("defi", "uniswap", "liquidity pool"), ("ETH", "AAVE", "SOL")),
    (("memecoin", "meme coin", "pepe"), ("DOGE", "SOL")),
)

_THEME_ASSET_HINTS: dict[str, tuple[str, ...]] = {
    "ETF": ("BTC", "ETH"),
    "Stablecoins": ("ETH", "AAVE", "XRP"),
    "DeFi": ("ETH", "AAVE", "SOL"),
    "L2": ("ETH", "ARB", "OP"),
    "Layer2": ("ETH", "ARB", "OP"),
    "Memecoins": ("DOGE", "SOL"),
    "Regulation": ("XRP", "BNB", "BTC"),
    "Exchange": ("BNB", "HYPE"),
    "Mining": ("BTC",),
    "Institutional": ("BTC", "ETH"),
    "RWA": ("ETH", "LINK"),
    "AI": ("SOL", "LINK"),
}

BIND_MIN_SCORE = 0.45


def _norm(text: str) -> str:
    t = f" {(text or '').lower()} "
    t = re.sub(r"[^\w\s+-]", " ", t)
    return f" {t} "


def entity_symbols(text: str) -> list[str]:
    """Map known entities/themes to concrete tickers."""
    blob = _norm(text)
    out: list[str] = []
    for keys, syms in _ENTITY_MAP:
        if any(k in blob for k in keys):
            for s in syms:
                if s not in out:
                    out.append(s)
    return out


def classify_source_type(source: str) -> str:
    s = (source or "").lower()
    if s.startswith("tg:") or "telegram" in s or "t.me" in s:
        return "telegram"
    if s.startswith("x:") or "twitter" in s or "nitter" in s:
        return "twitter"
    if s.startswith("macro:") or s in {
        "fed", "cpi", "ppi", "dxy", "us10y", "gold", "oil", "nfp",
    }:
        return "macro"
    if "polymarket" in s:
        return "polymarket"
    return "rss"


def score_asset_binding(
    *,
    symbol: str,
    title: str,
    body: str,
    source: str = "",
    narratives: list[str] | None = None,
    category: str = "",
) -> float:
    """Weighted relevance of an event to a single asset (0..1+)."""
    sym = symbol.upper()
    blob = _norm(f"{title}\n{body}")
    title_l = _norm(title)
    score = 0.0

    # Ticker / alias in title (strong)
    detected_title = {s.upper() for s in detect_symbols(title)}
    if sym in detected_title:
        score += 0.55
    # Ticker in body
    detected_body = {s.upper() for s in detect_symbols(f"{title}\n{body}")}
    if sym in detected_body and sym not in detected_title:
        score += 0.30
    elif sym in detected_body:
        score += 0.10

    # Entity recognition
    ents = entity_symbols(f"{title}\n{body}")
    if sym in ents:
        score += 0.40

    # Source quality soft boost (trusted sources count more when already bound)
    q = source_quality(source)
    if score > 0:
        score += 0.08 * q

    # Category / narrative thematic match
    narrs = list(narratives or [])
    if category:
        narrs.append(category)
    for n in narrs:
        hints = _THEME_ASSET_HINTS.get(n) or ()
        if sym in hints:
            score += 0.22
            break

    # Exact symbol token in blob
    if re.search(rf"\b{re.escape(sym.lower())}\b", blob):
        score += 0.15

    return round(min(1.5, score), 3)


def bind_event_assets(
    *,
    title: str,
    body: str = "",
    source: str = "",
    narratives: list[str] | None = None,
    existing_symbols: list[str] | None = None,
    watch: list[str] | set[str] | None = None,
    min_score: float = BIND_MIN_SCORE,
    max_assets: int = 4,
) -> list[str]:
    """
    Return ranked watchlist symbols for an event.
    Does NOT fall back to BTC merely because the text is 'about crypto'.
    """
    watch_set = {str(s).upper() for s in (watch or watched_symbols())}
    scores: dict[str, float] = {}

    for sym in existing_symbols or []:
        su = str(sym).upper()
        if su in watch_set:
            scores[su] = max(scores.get(su, 0.0), 0.70)

    for sym in watch_set:
        sc = score_asset_binding(
            symbol=sym,
            title=title,
            body=body,
            source=source,
            narratives=narratives,
        )
        if sc >= min_score:
            scores[sym] = max(scores.get(sym, 0.0), sc)

    # Entity-only hints even if slightly below threshold when explicit entity hit
    for sym in entity_symbols(f"{title}\n{body}"):
        if sym in watch_set:
            scores[sym] = max(scores.get(sym, 0.0), BIND_MIN_SCORE)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s for s, sc in ranked[:max_assets] if sc >= min_score * 0.9]


def prepare_article_symbols(row: dict[str, Any]) -> list[str]:
    title = str(row.get("title") or "")
    body = str(row.get("body") or row.get("summary") or "")
    existing = row.get("symbols")
    if isinstance(existing, str):
        existing_list = [s.strip().upper() for s in existing.split(",") if s.strip()]
    elif isinstance(existing, list):
        existing_list = [str(s).upper() for s in existing if s]
    else:
        existing_list = []
    return bind_event_assets(
        title=title,
        body=body,
        source=str(row.get("source") or ""),
        narratives=list(row.get("narratives") or []),
        existing_symbols=existing_list,
    )
