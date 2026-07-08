"""Shared utilities for Stage 3 research pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from bot.research.futures.parser_v2 import KNOWN_TICKERS, SYMBOL_ALIASES, extract_symbol_v2

_PAIR_RE = re.compile(
    r"\b([A-Z]{2,10})(?:USDT|/USDT|-PERP)\b",
    re.IGNORECASE,
)
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{2,10})\b")
_HASH_TICKER_RE = re.compile(r"#([A-Z]{2,10})\b")
_MARKET_WIDE = frozenset({"BTC", "ETH", "CRYPTO", "MARKET"})


def normalize_content_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_content_text(text).encode("utf-8")).hexdigest()


def normalize_symbol(raw: str) -> str | None:
    token = raw.upper().lstrip("#$")
    token = token.replace("/USDT", "").replace("USDT", "").replace("-PERP", "")
    token = SYMBOL_ALIASES.get(token, token)
    if token in KNOWN_TICKERS:
        return token
    if 2 <= len(token) <= 10 and token.isalpha():
        return token
    return None


def extract_symbols(text: str, *, max_symbols: int = 8) -> list[str]:
    """Extract unique symbols from text (deterministic)."""
    found: list[str] = []
    seen: set[str] = set()
    header = text[:800]
    for pat in (_DOLLAR_TICKER_RE, _HASH_TICKER_RE, _PAIR_RE):
        for m in pat.finditer(header):
            raw = m.group(1) if m.lastindex else m.group(0)
            sym = normalize_symbol(raw)
            if sym and sym not in seen:
                seen.add(sym)
                found.append(sym)
            if len(found) >= max_symbols:
                return found
    primary, _ = extract_symbol_v2(text)
    if primary and primary not in seen:
        found.insert(0, primary)
    return found[:max_symbols]


def symbols_json(symbols: list[str]) -> str:
    return json.dumps(symbols, separators=(",", ":"))


def parse_symbols_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(s) for s in val] if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def message_ts_to_epoch(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts // 1000 if ts > 10_000_000_000 else ts
    if isinstance(value, str):
        text = value.strip()
        for parser in (
            lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
            lambda s: datetime.fromisoformat(s.replace(" ", "T")),
        ):
            try:
                dt = parser(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                continue
    return None


def is_market_wide_news(symbols: list[str], text: str) -> bool:
    if not symbols:
        tl = text.lower()
        return any(k in tl for k in ("bitcoin", "btc", "ethereum", "eth", "crypto market", "sec ", "fed "))
    return any(s in MARKET_WIDE for s in symbols)
