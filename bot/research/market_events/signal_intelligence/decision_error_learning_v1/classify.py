"""Confusion matrix + error classification (research-only)."""

from __future__ import annotations

import json
from typing import Any

MODULES = (
    "replay",
    "fingerprint",
    "timeline",
    "dna",
    "rules",
    "edge",
    "brain",
    "causality",
    "confidence",
    "regime",
    "direction",
)

ERROR_CLASSES = (
    "Correct trade",
    "False Reject",
    "False Accept",
    "Late Entry",
    "Early Exit",
    "Wrong Direction",
    "Wrong Confidence",
    "Wrong Regime",
    "Wrong Replay",
    "Wrong Fingerprint",
    "Wrong Timeline",
    "Wrong DNA",
    "Wrong Rule",
)


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _is_confidence_summary(r: str) -> bool:
    rl = r.lower().strip()
    return rl.startswith("confidence ") and rl.endswith("%")


def _reasons(row: dict[str, Any]) -> list[str]:
    raw = row.get("reasons_json") or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else list(raw)
        reasons = [str(x) for x in data]
    except Exception:
        reasons = [str(raw)] if raw else []
    # Trailing "Confidence N%" is decide_one summary — drop only when other vetoes exist.
    non_conf = [r for r in reasons if not _is_confidence_summary(r)]
    return non_conf if non_conf else reasons


def confusion_label(*, accepted: bool, pnl: float | None) -> str:
    """TP / FP / TN / FN vs profitable ground truth (Book A outcome)."""
    if pnl is None:
        return "UNK"
    profitable = pnl > 0
    if accepted and profitable:
        return "TP"
    if accepted and not profitable:
        return "FP"
    if (not accepted) and (not profitable):
        return "TN"
    return "FN"  # rejected but profitable


def primary_module_from_reasons(reasons: list[str], row: dict[str, Any]) -> str:
    """Map rejection/acceptance reasons → primary module blame."""
    text = " ".join(reasons).lower()
    checks = [
        ("replay", "replay"),
        ("fingerprint", "fingerprint"),
        ("timeline", "timeline"),
        ("dna", "dna"),
        ("rule", "rules"),
        ("edge", "edge"),
        ("no historical", "edge"),
        ("brain", "brain"),
        ("causal", "causality"),
        ("supporting", "confidence"),
        ("confidence <", "confidence"),
        ("direction", "direction"),
        ("regime", "regime"),
    ]
    for needle, mod in checks:
        if needle in text:
            return mod
    # Standalone low-confidence veto: "Confidence 35%"
    for r in reasons:
        if _is_confidence_summary(r):
            try:
                pct = float(r.lower().split()[1].replace("%", ""))
                if pct < 50:
                    return "confidence"
            except Exception:
                return "confidence"
    scores = {
        "fingerprint": _f(row.get("fingerprint_similarity")),
        "timeline": _f(row.get("timeline_similarity")),
        "replay": _f(row.get("replay")),
        "dna": _f(row.get("dna")),
        "edge": _f(row.get("edge")),
        "brain": _f(row.get("brain")),
        "causality": _f(row.get("causality")),
        "confidence": _f(row.get("confidence")),
    }
    present = {k: v for k, v in scores.items() if v is not None}
    if present:
        return min(present, key=lambda k: present[k])
    return "unknown"


def classify_error(
    *,
    confusion: str,
    reasons: list[str],
    book_a: dict[str, Any],
    book_b: dict[str, Any],
) -> str:
    """Human-readable error class."""
    if confusion == "TP":
        return "Correct trade"
    if confusion == "TN":
        return "Correct trade"
    if confusion == "FN":
        mod = primary_module_from_reasons(reasons, book_b)
        mapping = {
            "replay": "Wrong Replay",
            "fingerprint": "Wrong Fingerprint",
            "timeline": "Wrong Timeline",
            "dna": "Wrong DNA",
            "rules": "Wrong Rule",
            "confidence": "Wrong Confidence",
            "regime": "Wrong Regime",
            "direction": "Wrong Direction",
        }
        return mapping.get(mod, "False Reject")
    if confusion == "FP":
        # Accepted loss — refine
        text = " ".join(reasons).lower()
        conf = _f(book_b.get("confidence"))
        if conf is not None and conf >= 0.7:
            return "Wrong Confidence"
        if "direction" in text:
            return "Wrong Direction"
        if "late" in text:
            return "Late Entry"
        if "early" in text or "exit" in text:
            return "Early Exit"
        return "False Accept"
    return "Correct trade"


def classify_trade(
    book_a: dict[str, Any],
    book_b: dict[str, Any],
) -> dict[str, Any]:
    """One trade error record from Book A outcome + Book B decision."""
    pnl = _f(book_a.get("pnl"))
    if pnl is None:
        pnl = _f(book_a.get("_counterfactual_pnl"))
    accepted = int(book_b.get("accepted") or 0) == 1
    # For rejected B rows, pnl may be null — use A pnl as ground truth
    if pnl is None:
        pnl = _f(book_b.get("pnl"))
    confusion = confusion_label(accepted=accepted, pnl=pnl)
    reasons = _reasons(book_b) if not accepted else _reasons(book_b) or _reasons(book_a)
    primary = primary_module_from_reasons(reasons, book_b)
    error_class = classify_error(
        confusion=confusion, reasons=reasons, book_a=book_a, book_b=book_b
    )
    modules = {
        "fingerprint": _f(book_b.get("fingerprint_similarity")),
        "timeline": _f(book_b.get("timeline_similarity")),
        "dna": _f(book_b.get("dna")),
        "rules": _f(book_b.get("rules")),
        "edge": _f(book_b.get("edge")),
        "replay": _f(book_b.get("replay")),
        "brain": _f(book_b.get("brain")),
        "causality": _f(book_b.get("causality")),
        "confidence": _f(book_b.get("confidence")),
        "historical_wr": _f(book_b.get("historical_wr")),
        "historical_pf": _f(book_b.get("historical_pf")),
        "historical_ev": _f(book_b.get("historical_ev")),
    }
    return {
        "trade_id": int(book_a.get("trade_id") or book_b.get("trade_id") or 0),
        "symbol": book_a.get("symbol") or book_b.get("symbol"),
        "opened_at": book_a.get("opened_at") or book_b.get("opened_at"),
        "confusion": confusion,
        "error_class": error_class,
        "accepted": accepted,
        "pnl": pnl,
        "direction": book_a.get("direction") or book_b.get("direction"),
        "decision": book_b.get("decision"),
        "confidence": _f(book_b.get("confidence")),
        "primary_module": primary,
        "reasons": reasons,
        "modules": modules,
        "result": book_a.get("result") or book_b.get("result"),
    }


__all__ = [
    "ERROR_CLASSES",
    "MODULES",
    "classify_error",
    "classify_trade",
    "confusion_label",
    "primary_module_from_reasons",
]
