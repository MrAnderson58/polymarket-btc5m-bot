"""Phase F.4 — cross-check visual intel against live Signal Intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VisualCrossCheck:
    agreement_score: float
    direction_match: bool | None
    ticker_match: bool | None
    notes: list[str]
    json_payload: dict[str, Any]


def crosscheck_visual_intel(
    *,
    symbol: str,
    shock_direction: str,
    shock_return_pct: float,
    extracted_ticker: str | None,
    extracted_direction: str | None,
    chart_structure: dict[str, Any],
    market_structure_labels: list[str],
    confidence: float,
) -> VisualCrossCheck:
    notes: list[str] = []
    score = 0.5

    ticker_match = None
    if extracted_ticker:
        ticker_match = extracted_ticker.upper() == symbol.upper()
        if ticker_match:
            score += 0.15
            notes.append("Тикер совпадает с событием")
        else:
            score -= 0.1
            notes.append("Тикер на графике отличается от шока")

    direction_match = None
    if extracted_direction:
        direction_match = extracted_direction == shock_direction
        if direction_match:
            score += 0.2
            notes.append("Направление автора совпадает с импульсом")
        else:
            score -= 0.15
            notes.append("Автор ожидает другое направление")

    visual_labels = set(chart_structure.get("structure_labels") or [])
    market_labels = set(market_structure_labels or [])
    overlap = visual_labels & market_labels
    if overlap:
        score += 0.1 * len(overlap)
        notes.append(f"Структура совпадает: {', '.join(sorted(overlap))}")

    if abs(shock_return_pct) >= 3:
        score += 0.05

    score = round(min(1.0, max(0.0, score)), 2)
    if confidence >= 7:
        notes.append("Signal Intelligence confidence высокий")

    payload = {
        "symbol": symbol,
        "shock_direction": shock_direction,
        "shock_return_pct": shock_return_pct,
        "extracted_ticker": extracted_ticker,
        "extracted_direction": extracted_direction,
        "chart_structure": chart_structure,
        "market_structure_labels": market_structure_labels,
        "agreement_score": score,
        "direction_match": direction_match,
        "ticker_match": ticker_match,
    }
    return VisualCrossCheck(
        agreement_score=score,
        direction_match=direction_match,
        ticker_match=ticker_match,
        notes=notes,
        json_payload=payload,
    )
