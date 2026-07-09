"""Configurable TradFi activation tiers — measured API data, not blind enable."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.basis_monitor import compute_basis_bps, compute_spread_bps
from bot.research.market_events.instrument_types import (
    ACTIVATION_INACTIVE,
    ACTIVATION_PAPER_ACTIVE,
    ACTIVATION_WATCH,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_INDEX,
    SESSION_UNDERLYING_CLOSED,
    SESSION_US_REGULAR,
    SESSION_WEEKEND,
)
from bot.research.market_events.reference_provider import (
    REFERENCE_PROVIDER_BYBIT_INDEX,
    resolve_bybit_reference,
)
from bot.research.market_events.venue_bybit import BybitTicker


@dataclass
class ActivationRules:
    min_turnover_watch_usd: float = 1_000_000
    min_turnover_paper_usd: float = 5_000_000
    max_spread_bps_paper: float = 5.0
    max_spread_bps_watch: float = 20.0
    max_basis_bps_sanity: float = 500.0
    require_index_price: bool = True
    max_quote_age_sec: float = 120.0
    min_observation_polls: int = 2
    paper_requires_us_regular: bool = True


@dataclass
class ActivationDecision:
    tier: str
    observe_enabled: int
    paper_enabled: int
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


DEFAULT_RULES = ActivationRules()


def evaluate_activation(
    *,
    asset_class: str,
    ticker: BybitTicker | None,
    session_regime: str,
    rules: ActivationRules = DEFAULT_RULES,
    prior_metadata: dict[str, Any] | None = None,
    poll_ts: int | None = None,
    allow_paper_promotion: bool = False,
) -> ActivationDecision:
    """Return PAPER_ACTIVE / WATCH / INACTIVE from measured quote quality."""
    if asset_class == ASSET_CLASS_CRYPTO:
        return ActivationDecision(
            tier=ACTIVATION_PAPER_ACTIVE,
            observe_enabled=1,
            paper_enabled=1,
            reasons=["crypto_core"],
        )

    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    prior = prior_metadata or {}
    obs_polls = int(prior.get("observation_poll_count") or 0) + 1
    metrics["observation_poll_count"] = obs_polls

    if ticker is None:
        return ActivationDecision(
            tier=ACTIVATION_INACTIVE,
            observe_enabled=0,
            paper_enabled=0,
            reasons=["no_ticker"],
            metrics=metrics,
        )

    ref = resolve_bybit_reference(index_price=ticker.index_price, mark_price=ticker.mark_price)
    spread = compute_spread_bps(ticker.bid, ticker.ask, ticker.last_price)
    basis = compute_basis_bps(ticker.last_price, ref.price)
    quote_age = max(0, (poll_ts or ticker.ts) - ticker.ts)

    metrics.update({
        "turnover_24h": ticker.turnover_24h,
        "spread_bps": spread,
        "basis_bps": basis,
        "quote_age_sec": quote_age,
        "reference_provider": ref.provider,
        "reference_same_venue": ref.same_venue_as_trade,
        "session_regime": session_regime,
    })

    if ticker.turnover_24h < rules.min_turnover_watch_usd:
        reasons.append(f"turnover_below_watch={ticker.turnover_24h:.0f}")
        return ActivationDecision(
            tier=ACTIVATION_INACTIVE, observe_enabled=0, paper_enabled=0,
            reasons=reasons, metrics=metrics,
        )

    if rules.require_index_price and ref.price is None:
        reasons.append("missing_index_price")
        return ActivationDecision(
            tier=ACTIVATION_INACTIVE, observe_enabled=0, paper_enabled=0,
            reasons=reasons, metrics=metrics,
        )

    if basis is not None and abs(basis) > rules.max_basis_bps_sanity:
        reasons.append(f"basis_insane={basis:.1f}bps")
        return ActivationDecision(
            tier=ACTIVATION_INACTIVE, observe_enabled=0, paper_enabled=0,
            reasons=reasons, metrics=metrics,
        )

    if quote_age > rules.max_quote_age_sec:
        reasons.append(f"stale_quote_age={quote_age:.0f}s")
        return ActivationDecision(
            tier=ACTIVATION_INACTIVE, observe_enabled=0, paper_enabled=0,
            reasons=reasons, metrics=metrics,
        )

    tier = ACTIVATION_WATCH
    reasons.append("turnover_meets_watch")

    if spread is not None and spread <= rules.max_spread_bps_watch:
        reasons.append(f"spread_ok_watch={spread:.2f}bps")
    elif spread is not None:
        reasons.append(f"spread_wide={spread:.2f}bps")

    paper_ok = (
        ticker.turnover_24h >= rules.min_turnover_paper_usd
        and spread is not None
        and spread <= rules.max_spread_bps_paper
        and obs_polls >= rules.min_observation_polls
    )

    if asset_class in (ASSET_CLASS_EQUITY, ASSET_CLASS_INDEX) and rules.paper_requires_us_regular:
        if session_regime != SESSION_US_REGULAR:
            reasons.append(f"session_not_us_regular={session_regime}")
            paper_ok = False

    if paper_ok and allow_paper_promotion:
        tier = ACTIVATION_PAPER_ACTIVE
        reasons.append("paper_rules_passed")
    elif paper_ok:
        reasons.append("paper_eligible_but_enable_tradfi_not_set")

    observe = 1 if tier in (ACTIVATION_WATCH, ACTIVATION_PAPER_ACTIVE) else 0
    paper = 1 if tier == ACTIVATION_PAPER_ACTIVE else 0
    return ActivationDecision(tier=tier, observe_enabled=observe, paper_enabled=paper, reasons=reasons, metrics=metrics)


def merge_activation_metadata(existing: dict[str, Any] | None, decision: ActivationDecision) -> str:
    meta = dict(existing or {})
    meta["activation_tier"] = decision.tier
    meta["activation_reasons"] = decision.reasons
    meta["activation_metrics"] = decision.metrics
    meta["observation_poll_count"] = decision.metrics.get("observation_poll_count", 0)
    meta["reference_provider"] = decision.metrics.get("reference_provider", REFERENCE_PROVIDER_BYBIT_INDEX)
    meta["reference_same_venue"] = decision.metrics.get("reference_same_venue", True)
    return json.dumps(meta)


def is_shock_eligible(
    *,
    asset_class: str,
    session_regime: str,
    quote_ok: bool,
    rejection_reason: str | None,
) -> tuple[bool, str | None]:
    if not quote_ok:
        return False, rejection_reason or "quote_rejected"
    if asset_class in (ASSET_CLASS_EQUITY, ASSET_CLASS_INDEX):
        if session_regime in (SESSION_UNDERLYING_CLOSED, SESSION_WEEKEND):
            return False, f"underlying_closed_session={session_regime}"
    return True, None
