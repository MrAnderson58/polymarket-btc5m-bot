"""Canonical bid/ask semantics for research paths.

Polymarket CLOB ``/price`` (verified in bot/research/mtf/quotes.py):
  side=BUY  → best bid (sell into book)
  side=SELL → best ask (buy from book)

``market_scanner.get_token_quotes`` maps SELL→bid and BUY→ask (reversed).
Execution and v4_shadow_observations persistence use market_scanner unchanged.

Research loaders call ``normalize_observation_quotes`` so simulators see
bid < ask and spread = ask - bid >= 0 without mutating live execution.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuotePair:
    bid: float | None
    ask: float | None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return float(self.ask) - float(self.bid)

    @property
    def is_reversed(self) -> bool:
        if self.bid is None or self.ask is None:
            return False
        return float(self.bid) > float(self.ask)


def normalize_quote_pair(bid: float | None, ask: float | None) -> QuotePair:
    """Return canonical bid<=ask; swap when scanner labels are reversed."""
    if bid is None or ask is None:
        return QuotePair(bid=bid, ask=ask)
    b, a = float(bid), float(ask)
    if b > a:
        return QuotePair(bid=a, ask=b)
    return QuotePair(bid=b, ask=a)


def observation_quotes_reversed(obs: dict) -> bool:
    """True if YES or NO side shows bid > ask in raw stored row."""
    for bid_key, ask_key in (("yes_bid", "yes_ask"), ("no_bid", "no_ask")):
        bid, ask = obs.get(bid_key), obs.get(ask_key)
        if bid is not None and ask is not None and float(bid) > float(ask):
            return True
    return False


def normalize_observation_quotes(obs: dict) -> dict:
    """Return copy with canonical bid/ask labels and YES spread column."""
    out = dict(obs)
    yes = normalize_quote_pair(out.get("yes_bid"), out.get("yes_ask"))
    no = normalize_quote_pair(out.get("no_bid"), out.get("no_ask"))
    out["yes_bid"] = yes.bid
    out["yes_ask"] = yes.ask
    out["no_bid"] = no.bid
    out["no_ask"] = no.ask
    if yes.spread is not None:
        out["spread"] = yes.spread
    return out


def normalize_observation_path(observations: list[dict]) -> list[dict]:
    return [normalize_observation_quotes(o) for o in observations]
