"""Narrative label detection for news text (S45 quality categories)."""

from __future__ import annotations

import re

# Canonical S45 categories (multi-label allowed; never force "General" when empty → "").
NARRATIVE_LABELS: tuple[str, ...] = (
    "ETF",
    "Stablecoins",
    "Macro",
    "Fed",
    "Exchange",
    "DeFi",
    "L2",
    "Memecoins",
    "Regulation",
    "AI",
    "Mining",
    "Institutional",
    "RWA",
    "Security",
    "Geopolitics",
    "Liquidity",
    "Derivatives",
    # Retained aliases still emitted for back-compat clustering
    "Hack",
    "Whales",
    "Inflation",
    "Rates",
)

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ETF", ("etf", "exchange-traded fund", "ishares", "grayscale bitcoin", "ibit")),
    ("Institutional", (
        "institutional", "blackrock", "fidelity", "corporate treasury",
        "microstrategy", "nation state",
    )),
    ("L2", (
        "layer 2", "layer-2", " l2 ", "arbitrum", "optimism", "base chain",
        "rollup",
    )),
    ("AI", (" artificial intelligence", " ai ", "llm", "machine learning", "openai")),
    ("DeFi", (
        "defi", "decentralized finance", "liquidity pool", "yield farming",
        "aave", "uniswap",
    )),
    ("Regulation", (
        "sec ", "cftc", "regulation", "lawsuit", "ban", "compliance", "mica",
    )),
    ("Hack", ("hack", "exploit", "breach", "stolen", "drained")),
    ("Security", (
        "security audit", "vulnerability", "cold wallet", "multisig", "hack",
        "exploit",
    )),
    ("Exchange", (
        "binance", "coinbase", "kraken", "okx", "bybit", "exchange listing",
        "hyperliquid",
    )),
    ("Whales", ("whale", "large transfer", "smart money", "accumulation wallet")),
    ("Fed", ("federal reserve", " fed ", "fomc", "powell")),
    ("Inflation", ("inflation", "cpi", "pce", "ppi")),
    ("Rates", ("interest rate", "rate cut", "rate hike", "basis points", "us10y")),
    ("Macro", (
        "macro", "treasury", "gdp", "recession", "jobs report", "dxy",
        "dollar index", "oil ", "gold ",
    )),
    ("Geopolitics", (
        "war", "geopolitical", "missile", "invasion", "conflict", "sanctions",
    )),
    ("Liquidity", ("liquidity", "market depth", "thin books", "bid ask")),
    ("Derivatives", (
        "funding rate", "open interest", "perpetual", "options expiry",
        "liquidation", "long squeeze", "short squeeze",
    )),
    ("Stablecoins", ("usdt", "usdc", "stablecoin", "tether", "circle")),
    ("Memecoins", ("meme coin", "memecoin", "dogecoin", "shiba", "pepe")),
    ("RWA", ("rwa", "real world asset", "tokenized treasury")),
    ("Mining", ("mining", "hashrate", "hash rate", "miner", "difficulty")),
)


def detect_narratives(text: str) -> list[str]:
    """Return zero or more narrative labels for free text (multi-label)."""
    blob = f" {(text or '').lower()} "
    blob = re.sub(r"[^\w\s+-]", " ", blob)
    blob = f" {blob} "
    found: list[str] = []
    for label, keys in _RULES:
        if any(k in blob for k in keys):
            found.append(label)
    # Normalize Layer2 alias → L2
    if "Layer2" in found and "L2" not in found:
        found = [("L2" if x == "Layer2" else x) for x in found]
    # Prefer Security over duplicate Hack when both present (keep both if both match)
    return found


def narratives_to_text(labels: list[str]) -> str:
    """Join labels; empty → empty string (reports may say Uncategorized)."""
    cleaned = [x for x in labels if x and x != "General"]
    return ", ".join(cleaned) if cleaned else ""
