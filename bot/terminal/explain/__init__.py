"""Explainability (V7.1.3) — /why BTC."""

from bot.terminal.explain.scoring import (
    ScoreContribution,
    ScoreExplanation,
    explain_scanner_result,
    explain_score,
)
from bot.terminal.scanner import get_scanner_service


def why_symbol(symbol: str) -> ScoreExplanation | None:
    scan = get_scanner_service().by_symbol(symbol)
    if scan is None:
        return None
    return explain_scanner_result(scan)


__all__ = [
    "ScoreContribution",
    "ScoreExplanation",
    "explain_scanner_result",
    "explain_score",
    "why_symbol",
]
