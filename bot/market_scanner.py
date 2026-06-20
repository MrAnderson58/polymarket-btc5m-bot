"""Discover active Polymarket BTC 5-minute markets and fetch order book prices."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from py_clob_client.client import ClobClient

from bot.config import (
    BTC_5M_SLUG_PREFIX,
    CHAIN_ID,
    CLOB_HOST,
    GAMMA_API,
    WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)


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


def get_token_quotes(client: ClobClient, token_id: str) -> TokenQuotes:
    """Fetch best bid (SELL) and best ask (BUY) for a token."""
    bid = None
    ask = None
    try:
        bid = _extract_price(client.get_price(token_id, side="SELL"))
        ask = _extract_price(client.get_price(token_id, side="BUY"))
    except Exception as exc:
        logger.warning("Failed to fetch quotes for token %s: %s", token_id, exc)
    return TokenQuotes(token_id=token_id, bid=bid, ask=ask)


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

    yes_idx = 0
    no_idx = 1
    for idx, outcome in enumerate(outcomes):
        label = str(outcome).strip().lower()
        if label in {"up", "yes"}:
            yes_idx = idx
        elif label in {"down", "no"}:
            no_idx = idx

    client = _get_clob_client()
    yes_quotes = get_token_quotes(client, token_ids[yes_idx])
    no_quotes = get_token_quotes(client, token_ids[no_idx])

    end_ts = window_start_ts + WINDOW_SECONDS

    return Btc5mMarket(
        slug=event.get("slug") or market.get("slug") or _build_slug(window_start_ts),
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
    """
    window_start = _current_window_start()
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


def get_best_bid_ask(market: Btc5mMarket) -> dict[str, float | None]:
    """Return best bid/ask for YES and NO tokens."""
    return {
        "yes_bid": market.yes_quotes.bid,
        "yes_ask": market.yes_quotes.ask,
        "no_bid": market.no_quotes.bid,
        "no_ask": market.no_quotes.ask,
    }
