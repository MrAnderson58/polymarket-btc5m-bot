"""F.0 AI JSON schema — strict structured response."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class F0AnalysisResponse:
    bias: str
    confidence: float
    continuation_probability: float
    reversal_probability: float
    summary_ru: str
    summary_en: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias,
            "confidence": self.confidence,
            "continuation_probability": self.continuation_probability,
            "reversal_probability": self.reversal_probability,
            "summary_ru": self.summary_ru,
            "summary_en": self.summary_en,
        }


VALID_BIAS = frozenset({"FADE", "CONTINUATION", "NEUTRAL", "WAIT"})


def parse_f0_response(raw: str | dict[str, Any]) -> F0AnalysisResponse | None:
    data = raw if isinstance(raw, dict) else None
    if data is None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    bias = str(data.get("bias", "NEUTRAL")).upper()
    if bias not in VALID_BIAS:
        bias = "NEUTRAL"
    try:
        conf = float(data.get("confidence", 0.5))
        cont = float(data.get("continuation_probability", 0.5))
        rev = float(data.get("reversal_probability", 0.5))
    except (TypeError, ValueError):
        return None
    return F0AnalysisResponse(
        bias=bias,
        confidence=max(0.0, min(1.0, conf)),
        continuation_probability=max(0.0, min(1.0, cont)),
        reversal_probability=max(0.0, min(1.0, rev)),
        summary_ru=str(data.get("summary_ru", ""))[:500],
        summary_en=str(data.get("summary_en", ""))[:500],
    )
