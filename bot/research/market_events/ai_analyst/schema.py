"""Structured AI analysis output validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.ai_analyst.config import (
    VALID_BIAS,
    VALID_INTERPRETATIONS,
    VALID_STATUS,
)


@dataclass
class StructuredAnalysis:
    event_id: int
    symbol: str
    analysis_status: str
    movement_interpretation: str
    reversal_bias: str
    confidence: float
    supporting_factors: list[str] = field(default_factory=list)
    contradicting_factors: list[str] = field(default_factory=list)
    relevant_context_ids: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    what_would_change_view: list[str] = field(default_factory=list)
    short_commentary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "analysis_status": self.analysis_status,
            "movement_interpretation": self.movement_interpretation,
            "reversal_bias": self.reversal_bias,
            "confidence": self.confidence,
            "supporting_factors": self.supporting_factors,
            "contradicting_factors": self.contradicting_factors,
            "relevant_context_ids": self.relevant_context_ids,
            "risk_notes": self.risk_notes,
            "what_would_change_view": self.what_would_change_view,
            "short_commentary": self.short_commentary,
        }


def _as_str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if x is not None and str(x).strip()]


def parse_structured_analysis(raw: dict[str, Any], *, event_id: int, symbol: str) -> StructuredAnalysis | None:
    try:
        status = str(raw.get("analysis_status", "FAILED")).upper()
        if status not in VALID_STATUS:
            status = "FAILED"
        interp = str(raw.get("movement_interpretation", "UNKNOWN")).upper()
        if interp not in VALID_INTERPRETATIONS:
            interp = "UNKNOWN"
        bias = str(raw.get("reversal_bias", "NO_VIEW")).upper()
        if bias not in VALID_BIAS:
            bias = "NO_VIEW"
        conf = float(raw.get("confidence", 0.0))
        conf = max(0.0, min(1.0, conf))
        return StructuredAnalysis(
            event_id=int(raw.get("event_id", event_id)),
            symbol=str(raw.get("symbol", symbol)),
            analysis_status=status,
            movement_interpretation=interp,
            reversal_bias=bias,
            confidence=conf,
            supporting_factors=_as_str_list(raw.get("supporting_factors")),
            contradicting_factors=_as_str_list(raw.get("contradicting_factors")),
            relevant_context_ids=_as_str_list(raw.get("relevant_context_ids")),
            risk_notes=_as_str_list(raw.get("risk_notes")),
            what_would_change_view=_as_str_list(raw.get("what_would_change_view")),
            short_commentary=str(raw.get("short_commentary", ""))[:2000],
        )
    except (TypeError, ValueError):
        return None


def parse_model_response(text: str, *, event_id: int, symbol: str) -> StructuredAnalysis | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            raw = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    return parse_structured_analysis(raw, event_id=event_id, symbol=symbol)
