"""Narrative label detection for news text."""

from __future__ import annotations

import re

NARRATIVE_LABELS: tuple[str, ...] = (
    "ETF",
    "Institutional Adoption",
    "Layer2",
    "AI",
    "DeFi",
    "Regulation",
    "Hack",
    "Security",
    "Exchange",
    "Whales",
    "Fed",
    "Inflation",
    "Rates",
    "Macro",
    "War",
    "Liquidation",
    "Stablecoins",
    "Memecoins",
    "RWA",
    "Token Unlock",
)

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ETF", ("etf", "exchange-traded fund", "ishares", "grayscale bitcoin")),
    ("Institutional Adoption", (
        "institutional", "blackrock", "fidelity", "corporate treasury",
        "microstrategy", "nation state",
    )),
    ("Layer2", ("layer 2", "layer-2", "l2", "arbitrum", "optimism", "base chain", "rollup")),
    ("AI", (" artificial intelligence", " ai ", "llm", "machine learning", "openai")),
    ("DeFi", ("defi", "decentralized finance", "liquidity pool", "yield farming", "aave", "uniswap")),
    ("Regulation", ("sec ", "cftc", "regulation", "lawsuit", "ban", "compliance", "miCA")),
    ("Hack", ("hack", "exploit", "breach", "stolen", "drained")),
    ("Security", ("security audit", "vulnerability", "cold wallet", "multisig")),
    ("Exchange", ("binance", "coinbase", "kraken", "okx", "bybit", "exchange listing")),
    ("Whales", ("whale", "large transfer", "smart money", "accumulation wallet")),
    ("Fed", ("federal reserve", " fed ", "fomc", "powell")),
    ("Inflation", ("inflation", "cpi", "pce")),
    ("Rates", ("interest rate", "rate cut", "rate hike", "basis points")),
    ("Macro", ("macro", "treasury", "gdp", "recession", "jobs report")),
    ("War", ("war", "geopolitical", "missile", "invasion", "conflict")),
    ("Liquidation", ("liquidation", "long squeeze", "short squeeze", "cascading liq")),
    ("Stablecoins", ("usdt", "usdc", "stablecoin", "tether", "circle")),
    ("Memecoins", ("meme coin", "memecoin", "dogecoin", "shiba", "pepe")),
    ("RWA", ("rwa", "real world asset", "tokenized treasury")),
    ("Token Unlock", ("token unlock", "vesting unlock", "cliff unlock", "unlock schedule")),
)


def detect_narratives(text: str) -> list[str]:
    """Return zero or more narrative labels for free text."""
    blob = f" {(text or '').lower()} "
    # Normalize punctuation for word-ish matching
    blob = re.sub(r"[^\w\s+-]", " ", blob)
    blob = f" {blob} "
    found: list[str] = []
    for label, keys in _RULES:
        if any(k in blob for k in keys):
            found.append(label)
    return found


def narratives_to_text(labels: list[str]) -> str:
    return ", ".join(labels) if labels else "General"
