"""MTF research quote fetching — correct bid/ask orientation vs market_scanner.

Polymarket CLOB ``/price`` semantics (verified against midpoint/spread):
  side=BUY  → executable bid (price received when selling / best bid)
  side=SELL → executable ask (price paid when buying / best ask)

``market_scanner.get_token_quotes`` assigns SELL→bid and BUY→ask (reversed).
MTF research uses this module only; execution is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MtfTokenQuotes:
    token_id: str
    bid: float | None
    ask: float | None


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


def _fetch_side_price(client: Any, token_id: str, side: str) -> float | None:
    try:
        return _extract_price(client.get_price(token_id, side=side))
    except Exception as exc:
        logger.debug("MTF price fetch failed token=%s side=%s: %s", token_id[:12], side, exc)
        return None


def get_mtf_token_quotes(client: Any, token_id: str) -> MtfTokenQuotes:
    """Fetch best bid/ask with correct orientation for MTF research."""
    bid = _fetch_side_price(client, token_id, "BUY")
    ask = _fetch_side_price(client, token_id, "SELL")
    if bid is not None and ask is not None and bid > ask:
        bid, ask = ask, bid
    return MtfTokenQuotes(token_id=token_id, bid=bid, ask=ask)


def fetch_mtf_market_quotes(slug: str) -> dict[str, Any] | None:
    """Resolve token IDs and fetch YES/NO quotes for an HTF market slug."""
    try:
        from bot.market_scanner import get_token_ids_for_market_slug, _get_clob_client

        token_ids = get_token_ids_for_market_slug(slug)
        if not token_ids:
            return None
        client = _get_clob_client()
        yes_q = get_mtf_token_quotes(client, token_ids[0])
        no_q = get_mtf_token_quotes(client, token_ids[1])
        return {
            "yes_bid": yes_q.bid,
            "yes_ask": yes_q.ask,
            "no_bid": no_q.bid,
            "no_ask": no_q.ask,
        }
    except Exception as exc:
        logger.warning("MTF quote fetch failed for %s: %s", slug, exc)
        return None
