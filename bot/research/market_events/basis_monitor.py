"""Basis and tracking-error monitoring between trade and reference prices."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BasisObservation:
    trade_price: float
    reference_price: float | None
    basis_bps: float | None
    basis_change_bps: float | None
    tracking_error_bps: float | None
    spread_bps: float | None
    lag_seconds: int | None


def compute_basis_bps(trade_price: float, reference_price: float | None) -> float | None:
    if reference_price is None or reference_price <= 0 or trade_price <= 0:
        return None
    return (trade_price / reference_price - 1.0) * 10000.0


def compute_spread_bps(bid: float | None, ask: float | None, mid: float | None = None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    m = mid if mid and mid > 0 else (bid + ask) / 2.0
    if m <= 0:
        return None
    return (ask - bid) / m * 10000.0


def observe_basis(
    *,
    trade_price: float,
    reference_price: float | None,
    prior_basis_bps: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    lag_seconds: int | None = None,
) -> BasisObservation:
    basis = compute_basis_bps(trade_price, reference_price)
    basis_chg = None
    if basis is not None and prior_basis_bps is not None:
        basis_chg = basis - prior_basis_bps
    tracking = abs(basis) if basis is not None else None
    return BasisObservation(
        trade_price=trade_price,
        reference_price=reference_price,
        basis_bps=basis,
        basis_change_bps=basis_chg,
        tracking_error_bps=tracking,
        spread_bps=compute_spread_bps(bid, ask, trade_price),
        lag_seconds=lag_seconds,
    )
