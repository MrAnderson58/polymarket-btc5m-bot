"""Phase F.1 — fixed confidence weights (sum = 10.0)."""

from __future__ import annotations

CONFIDENCE_WEIGHTS: dict[str, float] = {
    "historical_similarity": 1.5,
    "volatility": 1.0,
    "atr": 1.0,
    "volume": 1.0,
    "funding": 0.8,
    "open_interest": 0.7,
    "telegram_context": 1.2,
    "news": 0.8,
    "ai_agreement": 2.0,
}

ENTRY_WAIT_R2 = "WAIT_R2"
ENTRY_WAIT_R3 = "WAIT_R3"
ENTRY_SCALE_IN_25 = "SCALE_IN_25"
ENTRY_READY = "READY_TO_ENTER"

VALID_ENTRY_RECOMMENDATIONS = frozenset({
    ENTRY_WAIT_R2, ENTRY_WAIT_R3, ENTRY_SCALE_IN_25, ENTRY_READY,
})

PROMPT_VERSION_F1 = "f1_telegram_v1"
