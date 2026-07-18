"""Correlation + expected drawdown heuristics (presentation layer)."""

from __future__ import annotations

from bot.terminal.portfolio.models import PositionWeight


# Same-sector pairs are treated as highly correlated for Terminal advice.
_SECTOR_CORR: dict[str, float] = {
    "Crypto": 0.85,
    "Equities": 0.70,
    "ETF": 0.65,
    "Index": 0.75,
    "Commodities": 0.40,
    "FX": 0.35,
    "Other": 0.50,
}


def correlation_score(weights: tuple[PositionWeight, ...]) -> tuple[float, str]:
    """
    0…100 concentration/correlation proxy.

    High when few sectors dominate or many crypto names share risk.
    """
    if not weights:
        return 0.0, "no open risk — correlation n/a"

    by_sector: dict[str, float] = {}
    for w in weights:
        by_sector[w.sector] = by_sector.get(w.sector, 0.0) + w.weight_pct

    # Herfindahl on sector weights (0–1) → scale
    total = sum(by_sector.values()) or 1.0
    shares = [v / total for v in by_sector.values()]
    hhi = sum(s * s for s in shares)
    # Blend with dominant sector internal corr
    top_sector, top_pct = max(by_sector.items(), key=lambda kv: kv[1])
    intra = _SECTOR_CORR.get(top_sector, 0.5)
    score = 100.0 * (0.55 * hhi + 0.45 * intra * (top_pct / 100.0))
    score = round(max(0.0, min(100.0, score)), 1)

    if len(by_sector) == 1 and top_sector == "Crypto":
        note = f"high — mostly {top_sector} (intra≈{intra:.0%})"
    elif score >= 70:
        note = f"elevated — {top_sector} {top_pct:.0f}% dominates"
    elif score >= 45:
        note = f"moderate — top {top_sector} {top_pct:.0f}%"
    else:
        note = f"diversified — {len(by_sector)} sectors"
    return score, note


def expected_dd_pct(
    *,
    open_risk_pct: float | None,
    correlation: float | None,
    crypto_pct: float,
) -> float | None:
    """
    Rough expected drawdown % of equity.

    Open risk here is deployed capital, not stop loss — scale down, then
    amplify mildly by correlation / crypto concentration.
    """
    if open_risk_pct is None:
        return None
    corr = (correlation or 50.0) / 100.0
    # ~30% of deployed notionals as adverse move proxy
    base = float(open_risk_pct) * 0.30
    stress = 1.0 + 0.25 * corr + (0.12 if crypto_pct >= 60 else 0.0)
    return round(max(0.0, min(50.0, base * stress)), 1)


def open_risk_pct(open_risk: float | None, equity: float | None) -> float | None:
    if open_risk is None or equity is None or equity <= 0:
        return None
    return round(100.0 * float(open_risk) / float(equity), 1)


def cash_pct(cash: float | None, equity: float | None) -> float | None:
    if cash is None or equity is None or equity <= 0:
        return None
    return round(100.0 * float(cash) / float(equity), 1)


__all__ = [
    "cash_pct",
    "correlation_score",
    "expected_dd_pct",
    "open_risk_pct",
]
