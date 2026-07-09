"""Reference price provider abstraction — provenance, not external claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REFERENCE_PROVIDER_BYBIT_INDEX = "REFERENCE_PROVIDER_BYBIT_INDEX"
REFERENCE_PROVIDER_BYBIT_LAST = "REFERENCE_PROVIDER_BYBIT_LAST"
REFERENCE_PROVIDER_EXTERNAL_EQUITY = "REFERENCE_PROVIDER_EXTERNAL_EQUITY"
REFERENCE_PROVIDER_EXTERNAL_COMMODITY = "REFERENCE_PROVIDER_EXTERNAL_COMMODITY"
REFERENCE_PROVIDER_SELF = "REFERENCE_PROVIDER_SELF"

SAME_VENUE_PROVIDERS = frozenset({
    REFERENCE_PROVIDER_BYBIT_INDEX,
    REFERENCE_PROVIDER_BYBIT_LAST,
})


@dataclass(frozen=True)
class ReferenceQuote:
    price: float | None
    provider: str
    same_venue_as_trade: bool
    external_market: bool
    provenance_note: str


def resolve_bybit_reference(*, index_price: float | None, mark_price: float | None) -> ReferenceQuote:
    price = index_price or mark_price
    return ReferenceQuote(
        price=price,
        provider=REFERENCE_PROVIDER_BYBIT_INDEX,
        same_venue_as_trade=True,
        external_market=False,
        provenance_note=(
            "Bybit indexPrice/markPrice — same venue infrastructure as lastPrice; "
            "not an independent external equity/commodity feed"
        ),
    )


def resolve_crypto_reference(*, last_price: float | None) -> ReferenceQuote:
    return ReferenceQuote(
        price=last_price,
        provider=REFERENCE_PROVIDER_SELF,
        same_venue_as_trade=True,
        external_market=False,
        provenance_note="Crypto self-reference on trade venue",
    )


def basis_independence_warning(provider: str) -> str | None:
    if provider in SAME_VENUE_PROVIDERS:
        return (
            "TOKENIZED_MARKET_DISLOCATION signals using Bybit indexPrice measure "
            "intra-venue divergence, not true external-market basis"
        )
    return None


def reference_audit_report() -> dict[str, Any]:
    return {
        "current_tradfi_provider": REFERENCE_PROVIDER_BYBIT_INDEX,
        "same_venue_as_trade": True,
        "external_market_basis": False,
        "dislocation_interpretation": basis_independence_warning(REFERENCE_PROVIDER_BYBIT_INDEX),
        "future_providers": [
            REFERENCE_PROVIDER_EXTERNAL_EQUITY,
            REFERENCE_PROVIDER_EXTERNAL_COMMODITY,
        ],
    }
