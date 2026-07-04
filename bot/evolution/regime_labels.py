"""Regime label normalization for evolution regime shadow."""

from __future__ import annotations

# Canonical labels used by trade_features / ai_agent.features.regime_label_from_features
CANONICAL_REGIMES = frozenset({
    "Strong Uptrend",
    "News Spike",
    "Panic",
    "Mean Reversion",
    "Range",
    "Low Liquidity",
})

# Aliases from alternate classifiers (bidirectional, uppercase, snake_case)
REGIME_ALIASES: dict[str, str] = {
    "STRONG_UPTREND": "Strong Uptrend",
    "STRONG MOMENTUM": "Strong Uptrend",
    "STRONG_MOMENTUM": "Strong Uptrend",
    "NEWS_SPIKE": "News Spike",
    "NEWS SPIKE": "News Spike",
    "PANIC": "Panic",
    "MEAN_REVERSION": "Mean Reversion",
    "MEAN REVERSION": "Mean Reversion",
    "RANGE": "Range",
    "LOW_LIQUIDITY": "Low Liquidity",
    "LOW LIQUIDITY": "Low Liquidity",
    "CHOP": "Range",
    "REVERSAL": "Range",
    "NORMAL": "Range",
    "MOMENTUM": "Range",
}


def normalize_regime_label(label: str | None) -> str:
    """Map variant regime strings to canonical trade_features labels."""
    if not label:
        return ""
    text = str(label).strip()
    if text in CANONICAL_REGIMES:
        return text
    key = text.upper().replace(" ", "_")
    if key in REGIME_ALIASES:
        return REGIME_ALIASES[key]
    alt = text.replace("_", " ")
    if alt in CANONICAL_REGIMES:
        return alt
    return text


def regime_in_filter(label: str | None, filter_regimes: list[str]) -> bool:
    """Check if a trade regime matches any filter regime after normalization."""
    normalized = normalize_regime_label(label)
    normalized_filters = {normalize_regime_label(r) for r in filter_regimes}
    return normalized in normalized_filters
