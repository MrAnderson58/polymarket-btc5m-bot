"""Paper Decision Books A/B/C routing (research-only, <1 ms)."""

from __future__ import annotations

from typing import Any

BOOK_A = "paper_baseline"
BOOK_B = "paper_decision"
BOOK_C = "paper_high_confidence"

BOOK_LABELS = {
    BOOK_A: "Book A",
    BOOK_B: "Book B",
    BOOK_C: "Book C",
}

ALL_BOOKS = (BOOK_A, BOOK_B, BOOK_C)

# Book C thresholds (0–1 similarity scale)
C_MIN_CONFIDENCE = 0.70
C_MIN_TIMELINE_SIM = 0.70
C_MIN_FINGERPRINT_SIM = 0.60
C_MIN_RULES_MATCHED = 1


def _sim_01(scores: dict[str, Any], module: str) -> float | None:
    s = scores.get(module) or {}
    if s.get("similarity_pct") is not None:
        try:
            return round(float(s["similarity_pct"]) / 100.0, 4)
        except Exception:
            return None
    if s.get("confidence") is not None:
        try:
            return round(float(s["confidence"]), 4)
        except Exception:
            return None
    return None


def extract_module_signals(decision: dict[str, Any]) -> dict[str, Any]:
    """Fast extract of module signals from decide_one result."""
    scores = decision.get("scores") or {}
    rules = scores.get("rules") or {}
    rules_matched = 1 if rules.get("pass") and not rules.get("blocked") else 0
    if rules.get("blocked"):
        rules_matched = 0
    return {
        "confidence": float(decision.get("confidence") or 0.0),
        "timeline_similarity": _sim_01(scores, "timeline"),
        "fingerprint_similarity": _sim_01(scores, "fingerprint"),
        "dna": _sim_01(scores, "dna"),
        "rules": rules_matched,
        "edge": _sim_01(scores, "edge"),
        "replay": _sim_01(scores, "replay"),
        "brain": _sim_01(scores, "brain"),
        "causality": _sim_01(scores, "causality"),
        "decision": decision.get("decision"),
        "direction": decision.get("direction"),
    }


def book_a_accepts(_decision: dict[str, Any], *, signals: dict[str, Any] | None = None) -> bool:
    """Baseline — current behaviour: accept every candidate."""
    return True


def book_b_accepts(decision: dict[str, Any], *, signals: dict[str, Any] | None = None) -> bool:
    """Only Decision == TRADE."""
    return str(decision.get("decision") or "") == "TRADE"


def book_c_accepts(decision: dict[str, Any], *, signals: dict[str, Any] | None = None) -> bool:
    """TRADE + high-confidence filters."""
    if str(decision.get("decision") or "") != "TRADE":
        return False
    sig = signals or extract_module_signals(decision)
    conf = float(sig.get("confidence") or 0.0)
    tl = sig.get("timeline_similarity")
    fp = sig.get("fingerprint_similarity")
    rules = int(sig.get("rules") or 0)
    if conf < C_MIN_CONFIDENCE:
        return False
    if tl is None or float(tl) < C_MIN_TIMELINE_SIM:
        return False
    if fp is None or float(fp) < C_MIN_FINGERPRINT_SIM:
        return False
    if rules < C_MIN_RULES_MATCHED:
        return False
    return True


BOOK_FILTERS = {
    BOOK_A: book_a_accepts,
    BOOK_B: book_b_accepts,
    BOOK_C: book_c_accepts,
}


def route_decision(decision: dict[str, Any]) -> dict[str, bool]:
    """Return {book_id: accepted} for A/B/C. Pure routing <1 ms."""
    sig = extract_module_signals(decision)
    return {
        BOOK_A: book_a_accepts(decision, signals=sig),
        BOOK_B: book_b_accepts(decision, signals=sig),
        BOOK_C: book_c_accepts(decision, signals=sig),
    }


def rejection_reasons(decision: dict[str, Any], book: str) -> list[str]:
    """Why a book rejected (for journal / review)."""
    if book == BOOK_A:
        return []
    if book == BOOK_B:
        if str(decision.get("decision") or "") == "TRADE":
            return []
        return list(decision.get("why") or decision.get("reasons") or ["NO TRADE"])
    # Book C
    if str(decision.get("decision") or "") != "TRADE":
        return list(decision.get("why") or ["NO TRADE"])
    sig = extract_module_signals(decision)
    reasons: list[str] = []
    if float(sig.get("confidence") or 0) < C_MIN_CONFIDENCE:
        reasons.append(f"confidence<{C_MIN_CONFIDENCE}")
    tl = sig.get("timeline_similarity")
    if tl is None or float(tl) < C_MIN_TIMELINE_SIM:
        reasons.append(f"timeline_similarity<{C_MIN_TIMELINE_SIM}")
    fp = sig.get("fingerprint_similarity")
    if fp is None or float(fp) < C_MIN_FINGERPRINT_SIM:
        reasons.append(f"fingerprint_similarity<{C_MIN_FINGERPRINT_SIM}")
    if int(sig.get("rules") or 0) < C_MIN_RULES_MATCHED:
        reasons.append(f"rules_matched<{C_MIN_RULES_MATCHED}")
    return reasons


__all__ = [
    "ALL_BOOKS",
    "BOOK_A",
    "BOOK_B",
    "BOOK_C",
    "BOOK_FILTERS",
    "BOOK_LABELS",
    "C_MIN_CONFIDENCE",
    "C_MIN_FINGERPRINT_SIM",
    "C_MIN_RULES_MATCHED",
    "C_MIN_TIMELINE_SIM",
    "book_a_accepts",
    "book_b_accepts",
    "book_c_accepts",
    "extract_module_signals",
    "rejection_reasons",
    "route_decision",
]
