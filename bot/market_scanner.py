"""Discover active Polymarket BTC 5-minute markets and fetch order book prices."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.exceptions import PolyApiException

from bot.config import (
    BTC_5M_SLUG_PREFIX,
    CHAIN_ID,
    CLOB_HOST,
    GAMMA_API,
    WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)

INACTIVE_TOKEN_TTL_SEC = 300
QUOTE_CACHE_TTL_SEC = 1.0
_inactive_token_cache: dict[str, float] = {}
_inactive_cache_window_start: int | None = None
_quote_cache: dict[str, tuple[dict[str, float | None], float]] = {}


def clear_quote_cache() -> None:
    _quote_cache.clear()


def _clear_inactive_token_cache() -> None:
    _inactive_token_cache.clear()


def _maybe_clear_inactive_cache_for_window(window_start: int) -> None:
    global _inactive_cache_window_start
    if window_start != _inactive_cache_window_start:
        _clear_inactive_token_cache()
        _inactive_cache_window_start = window_start


def _prune_inactive_token_cache(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    expired = [token_id for token_id, until in _inactive_token_cache.items() if until <= ts]
    for token_id in expired:
        del _inactive_token_cache[token_id]


def _is_token_inactive(token_id: str) -> bool:
    _prune_inactive_token_cache()
    until = _inactive_token_cache.get(token_id)
    return until is not None and until > time.time()


def _mark_token_inactive(token_id: str) -> None:
    _inactive_token_cache[token_id] = time.time() + INACTIVE_TOKEN_TTL_SEC
    logger.info(
        "Token marked inactive for %ss (no orderbook) | token_id=%s",
        INACTIVE_TOKEN_TTL_SEC,
        token_id,
    )


def _poly_error_text(exc: PolyApiException) -> str:
    msg = exc.error_msg
    if isinstance(msg, dict):
        return str(msg.get("error", "")).lower()
    return str(msg).lower()


def _is_orderbook_missing_error(exc: Exception) -> bool:
    if not isinstance(exc, PolyApiException):
        return False
    if exc.status_code is not None and exc.status_code not in (None, 404):
        return False
    return "no orderbook" in _poly_error_text(exc)


def is_invalid_token_error(exc: Exception) -> bool:
    if not isinstance(exc, PolyApiException):
        return False
    text = _poly_error_text(exc)
    if exc.status_code == 400 and "invalid token" in text:
        return True
    return "invalid token id" in text


def is_permanent_exit_error_message(error: str | None) -> bool:
    if not error:
        return False
    text = error.lower()
    return (
        "invalid token" in text
        or "no orderbook" in text
        or "exit blocked: token has no clob orderbook" in text
    )


@dataclass(frozen=True)
class TokenQuotes:
    token_id: str
    bid: float | None
    ask: float | None


@dataclass(frozen=True)
class Btc5mMarket:
    slug: str
    title: str
    condition_id: str
    window_start_ts: int
    end_ts: int
    yes_token_id: str
    no_token_id: str
    yes_outcome: str
    no_outcome: str
    yes_quotes: TokenQuotes
    no_quotes: TokenQuotes
    strike_price: float | None = None

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.end_ts - time.time())


def _current_window_start() -> int:
    now = int(time.time())
    return (now // WINDOW_SECONDS) * WINDOW_SECONDS


def _build_slug(window_start_ts: int) -> str:
    return f"{BTC_5M_SLUG_PREFIX}-{window_start_ts}"


def _parse_json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return []


def _get_clob_client() -> ClobClient:
    return ClobClient(CLOB_HOST, chain_id=CHAIN_ID)


def _extract_price(response: Any) -> float | None:
    if response is None:
        return None
    if isinstance(response, dict):
        raw = response.get("price")
    else:
        raw = getattr(response, "price", None)
    if raw in (None, ""):
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _fetch_side_price(client: ClobClient, token_id: str, side: str) -> float | None:
    if _is_token_inactive(token_id):
        return None
    try:
        return _extract_price(client.get_price(token_id, side=side))
    except Exception as exc:
        if _is_orderbook_missing_error(exc):
            _mark_token_inactive(token_id)
            return None
        raise


def get_token_quotes(client: ClobClient, token_id: str) -> TokenQuotes:
    """Fetch best bid (SELL) and best ask (BUY) for a token."""
    if _is_token_inactive(token_id):
        return TokenQuotes(token_id=token_id, bid=None, ask=None)

    bid = None
    ask = None
    try:
        bid = _fetch_side_price(client, token_id, "SELL")
        if not _is_token_inactive(token_id):
            ask = _fetch_side_price(client, token_id, "BUY")
    except Exception as exc:
        logger.warning("Failed to fetch quotes for token %s: %s", token_id, exc)
    return TokenQuotes(token_id=token_id, bid=bid, ask=ask)


def _outcome_token_indices(outcomes: list) -> tuple[int, int]:
    yes_idx = 0
    no_idx = 1
    for idx, outcome in enumerate(outcomes):
        label = str(outcome).strip().lower()
        if label in {"up", "yes"}:
            yes_idx = idx
        elif label in {"down", "no"}:
            no_idx = idx
    return yes_idx, no_idx


def _token_ids_from_gamma_event(event: dict[str, Any]) -> tuple[str, str] | None:
    markets = event.get("markets") or []
    if not markets:
        return None

    market = markets[0]
    outcomes = _parse_json_list(market.get("outcomes"))
    token_ids = _parse_json_list(market.get("clobTokenIds"))
    if len(outcomes) < 2 or len(token_ids) < 2:
        return None

    yes_idx, no_idx = _outcome_token_indices(outcomes)
    return str(token_ids[yes_idx]), str(token_ids[no_idx])


def get_token_ids_for_market_slug(slug: str) -> tuple[str, str] | None:
    """Resolve YES/NO token IDs for a market slug via Gamma (includes closed markets)."""
    try:
        event = _fetch_event_by_slug(slug)
    except requests.RequestException as exc:
        logger.warning("Gamma API error resolving tokens for slug %s: %s", slug, exc)
        return None
    if not event:
        return None
    return _token_ids_from_gamma_event(event)


def get_token_id_for_market_slug_and_side(slug: str, side: str) -> str | None:
    token_ids = get_token_ids_for_market_slug(slug)
    if token_ids is None:
        return None
    yes_token_id, no_token_id = token_ids
    return yes_token_id if side == "YES" else no_token_id


def is_exit_token_tradable(token_id: str) -> bool:
    """Return False when CLOB cannot accept orders for this token (no orderbook / invalid)."""
    if _is_token_inactive(token_id):
        return False

    client = _get_clob_client()
    try:
        price = _fetch_side_price(client, token_id, "SELL")
        if price is not None:
            return True
        return not _is_token_inactive(token_id)
    except Exception as exc:
        if is_invalid_token_error(exc) or _is_orderbook_missing_error(exc):
            _mark_token_inactive(token_id)
            return False
        logger.warning(
            "Exit token tradability check inconclusive for %s: %s",
            token_id,
            exc,
        )
        return True


def _fetch_event_by_slug(slug: str) -> dict[str, Any] | None:
    response = requests.get(
        f"{GAMMA_API}/events",
        params={"slug": slug},
        timeout=5,
    )
    response.raise_for_status()
    events = response.json()
    if not events:
        return None
    return events[0]


def _window_start_from_slug(slug: str) -> int | None:
    suffix = slug.rsplit("-", 1)[-1]
    if suffix.isdigit():
        return int(suffix)
    return None


def _market_from_event(event: dict[str, Any], window_start_ts: int) -> Btc5mMarket | None:
    if not event.get("active") or event.get("closed"):
        return None

    markets = event.get("markets") or []
    if not markets:
        return None

    market = markets[0]
    if not market.get("active") or market.get("closed"):
        return None

    outcomes = _parse_json_list(market.get("outcomes"))
    token_ids = _parse_json_list(market.get("clobTokenIds"))
    if len(outcomes) < 2 or len(token_ids) < 2:
        return None

    yes_idx, no_idx = _outcome_token_indices(outcomes)

    client = _get_clob_client()
    yes_quotes = get_token_quotes(client, token_ids[yes_idx])
    no_quotes = get_token_quotes(client, token_ids[no_idx])

    slug = event.get("slug") or market.get("slug") or _build_slug(window_start_ts)
    slug_start = _window_start_from_slug(slug)
    if slug_start is not None:
        window_start_ts = slug_start

    end_ts = window_start_ts + WINDOW_SECONDS
    quotes = _quote_dict(yes_quotes, no_quotes)
    _store_quote_cache(slug, quotes)
    return Btc5mMarket(
        slug=slug,
        title=event.get("title") or market.get("question") or "",
        condition_id=market.get("conditionId", ""),
        window_start_ts=window_start_ts,
        end_ts=end_ts,
        yes_token_id=token_ids[yes_idx],
        no_token_id=token_ids[no_idx],
        yes_outcome=str(outcomes[yes_idx]),
        no_outcome=str(outcomes[no_idx]),
        yes_quotes=yes_quotes,
        no_quotes=no_quotes,
    )


def find_active_btc_5m_market() -> Btc5mMarket | None:
    """
    Find the current active Bitcoin 5-minute market.

    Markets are addressed deterministically by 5-minute Unix window start.
    Falls back to the previous window if the current one is not yet indexed.
    Prefers the market whose Polymarket window actually contains the current time.
    """
    now = int(time.time())
    window_start = _current_window_start()
    _maybe_clear_inactive_cache_for_window(window_start)
    found: list[Btc5mMarket] = []

    for candidate_start in (window_start, window_start - WINDOW_SECONDS):
        slug = _build_slug(candidate_start)
        try:
            event = _fetch_event_by_slug(slug)
        except requests.RequestException as exc:
            logger.warning("Gamma API error for slug %s: %s", slug, exc)
            continue
        if not event:
            continue
        market = _market_from_event(event, candidate_start)
        if market and market.seconds_remaining > 0:
            found.append(market)

    for market in found:
        if market.window_start_ts <= now < market.end_ts:
            logger.info(
                "Active market | slug=%s window_start_ts=%s end_ts=%s now=%s",
                market.slug,
                market.window_start_ts,
                market.end_ts,
                now,
            )
            return market

    if found:
        market = found[0]
        logger.warning(
            "No in-window market; using fallback | slug=%s window_start_ts=%s end_ts=%s now=%s",
            market.slug,
            market.window_start_ts,
            market.end_ts,
            now,
        )
        return market
    return None


def get_strike_price_from_market(market: Btc5mMarket) -> int:
    """Return the window start timestamp used as strike reference."""
    return market.window_start_ts


def get_market_end_time(market: Btc5mMarket) -> int:
    """Return market end time as Unix timestamp."""
    return market.end_ts


def get_yes_no_token_ids(market: Btc5mMarket) -> tuple[str, str]:
    """Return YES (Up) and NO (Down) token IDs."""
    return market.yes_token_id, market.no_token_id


def _quote_dict(yes_quotes: TokenQuotes, no_quotes: TokenQuotes) -> dict[str, float | None]:
    return {
        "yes_bid": yes_quotes.bid,
        "yes_ask": yes_quotes.ask,
        "no_bid": no_quotes.bid,
        "no_ask": no_quotes.ask,
    }


def _store_quote_cache(slug: str, quotes: dict[str, float | None]) -> None:
    _quote_cache[slug] = (quotes, time.monotonic())


def _fetch_best_bid_ask(market: Btc5mMarket) -> dict[str, float | None]:
    client = _get_clob_client()
    yes_quotes = get_token_quotes(client, market.yes_token_id)
    no_quotes = get_token_quotes(client, market.no_token_id)
    return _quote_dict(yes_quotes, no_quotes)


def get_best_bid_ask(market: Btc5mMarket) -> dict[str, float | None]:
    """Return best bid/ask for YES and NO tokens (1s TTL cache per market slug)."""
    now = time.monotonic()
    cached = _quote_cache.get(market.slug)
    if cached is not None:
        quotes, fetched_at = cached
        if now - fetched_at < QUOTE_CACHE_TTL_SEC:
            logger.info("Quote cache HIT | slug=%s", market.slug)
            return quotes

    logger.info("Quote cache MISS | slug=%s", market.slug)
    quotes = _fetch_best_bid_ask(market)
    _store_quote_cache(market.slug, quotes)
    return quotes
