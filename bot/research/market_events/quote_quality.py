"""TradFi quote freshness and spread validation."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.market_events.activation_rules import ActivationRules, DEFAULT_RULES
from bot.research.market_events.basis_monitor import compute_basis_bps, compute_spread_bps
from bot.research.market_events.reference_provider import resolve_bybit_reference
from bot.research.market_events.venue_bybit import BybitTicker


@dataclass
class QuoteQuality:
    ok: bool
    rejection_reason: str | None
    spread_bps: float | None
    basis_bps: float | None
    quote_age_sec: float
    reference_provider: str
    same_venue_reference: bool


def evaluate_bybit_quote(
    ticker: BybitTicker | None,
    *,
    poll_ts: int,
    rules: ActivationRules = DEFAULT_RULES,
) -> QuoteQuality:
    if ticker is None:
        return QuoteQuality(
            ok=False, rejection_reason="no_ticker", spread_bps=None, basis_bps=None,
            quote_age_sec=0.0, reference_provider="", same_venue_reference=True,
        )

    ref = resolve_bybit_reference(index_price=ticker.index_price, mark_price=ticker.mark_price)
    spread = compute_spread_bps(ticker.bid, ticker.ask, ticker.last_price)
    basis = compute_basis_bps(ticker.last_price, ref.price)
    quote_age = max(0.0, float(poll_ts - ticker.ts))

    if rules.require_index_price and ref.price is None:
        return QuoteQuality(
            ok=False, rejection_reason="missing_index_price", spread_bps=spread,
            basis_bps=basis, quote_age_sec=quote_age,
            reference_provider=ref.provider, same_venue_reference=ref.same_venue_as_trade,
        )
    if quote_age > rules.max_quote_age_sec:
        return QuoteQuality(
            ok=False, rejection_reason=f"stale_quote_age={quote_age:.0f}s", spread_bps=spread,
            basis_bps=basis, quote_age_sec=quote_age,
            reference_provider=ref.provider, same_venue_reference=ref.same_venue_as_trade,
        )
    if basis is not None and abs(basis) > rules.max_basis_bps_sanity:
        return QuoteQuality(
            ok=False, rejection_reason=f"basis_insane={basis:.1f}bps", spread_bps=spread,
            basis_bps=basis, quote_age_sec=quote_age,
            reference_provider=ref.provider, same_venue_reference=ref.same_venue_as_trade,
        )
    if spread is not None and spread > rules.max_spread_bps_watch:
        return QuoteQuality(
            ok=False, rejection_reason=f"spread_wide={spread:.2f}bps", spread_bps=spread,
            basis_bps=basis, quote_age_sec=quote_age,
            reference_provider=ref.provider, same_venue_reference=ref.same_venue_as_trade,
        )

    return QuoteQuality(
        ok=True, rejection_reason=None, spread_bps=spread, basis_bps=basis,
        quote_age_sec=quote_age, reference_provider=ref.provider,
        same_venue_reference=ref.same_venue_as_trade,
    )
