"""Phase F.5 — entry quality grades A+ … SKIP."""

from __future__ import annotations

GRADE_A_PLUS = "A+"
GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"
GRADE_SKIP = "SKIP"


def grade_entry_quality(
    *,
    dynamic_confidence: float,
    reversal_probability: float,
    entry_recommendation: str,
    historical_reversal_rate: float,
    entry_stage: str | None = None,
) -> str:
    rev = reversal_probability if reversal_probability <= 1.0 else reversal_probability / 100.0
    rec = (entry_recommendation or "").upper()
    stage = (entry_stage or "").upper()

    if dynamic_confidence < 5.5 or "SKIP" in rec:
        return GRADE_SKIP
    if dynamic_confidence >= 9.0 and rev >= 0.75 and historical_reversal_rate >= 0.65:
        return GRADE_A_PLUS
    if dynamic_confidence >= 8.0 and rev >= 0.65:
        return GRADE_A
    if dynamic_confidence >= 7.0 and rev >= 0.55:
        return GRADE_B
    if stage in ("READY", "ENTRY") and dynamic_confidence >= 6.5:
        return GRADE_B
    if dynamic_confidence >= 5.5:
        return GRADE_C
    return GRADE_SKIP
