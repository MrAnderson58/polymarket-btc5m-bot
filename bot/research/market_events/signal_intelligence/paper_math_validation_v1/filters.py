"""Entry / stop filters for Paper Mathematics Validation V1 (research-only)."""

from __future__ import annotations

from typing import Any

# Entry thresholds (ALL must be true)
MIN_CONFIDENCE = 0.75
MIN_REALITY = 80.0
MIN_HIST_WR = 70.0          # percent
MIN_HIST_PF = 1.80
MIN_TIMELINE = 0.60
MIN_FINGERPRINT = 0.30
MIN_RANK = ("A+", "A")
MIN_SAMPLE = 100
MATCH_FLOOR = 0.50          # replay / dna / brain support floor


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _pct_or_01(v: Any) -> float | None:
    """Normalize WR that may be 0–1 or 0–100."""
    x = _f(v)
    if x is None:
        return None
    if x <= 1.5:
        return x * 100.0
    return x


def is_match(score: Any, *, rules: bool = False) -> bool:
    if rules:
        try:
            return int(score or 0) >= 1
        except Exception:
            return False
    x = _f(score)
    if x is None:
        return False
    return x >= MATCH_FLOOR


def brain_supports(candidate: dict[str, Any]) -> bool:
    """Brain supports direction: brain score present and above floor."""
    b = candidate.get("brain")
    if b is None:
        b = candidate.get("brain_score")
    return is_match(b)


def regime_blocked(candidate: dict[str, Any]) -> str | None:
    raw = str(
        candidate.get("regime")
        or candidate.get("current_regime")
        or candidate.get("market_regime")
        or ""
    ).upper()
    if "REGIME_EXPLORE" in raw or raw == "EXPLORE":
        return "REGIME_EXPLORE"
    if raw in ("UNKNOWN", "UNK", "N/A"):
        return "unknown_regime"
    return None


def unknown_module(candidate: dict[str, Any], key: str) -> bool:
    v = candidate.get(key)
    if v is None:
        return True
    if isinstance(v, str) and v.strip().upper() in ("", "UNKNOWN", "UNK"):
        return True
    return False


def stop_reasons(candidate: dict[str, Any], *, reality_score: float | None) -> list[str]:
    """Immediate reject reasons (STOP FILTER)."""
    reasons: list[str] = []
    rb = regime_blocked(candidate)
    if rb:
        reasons.append(rb)
    if unknown_module(candidate, "fingerprint_similarity") and candidate.get("fingerprint") is None:
        # unknown fingerprint if both missing
        if candidate.get("fingerprint_similarity") is None:
            reasons.append("unknown_fingerprint")
    if candidate.get("timeline_similarity") is None:
        reasons.append("unknown_timeline")
    sample = candidate.get("sample_size")
    if sample is None:
        sample = candidate.get("n_historical") or candidate.get("historical_n")
    sf = _f(sample)
    if sf is None or sf < MIN_SAMPLE:
        reasons.append(f"historical_sample<{MIN_SAMPLE}")
    # confidence drop: confidence present but below entry (soft stop when collapsing)
    conf = _f(candidate.get("confidence"))
    if conf is not None and conf < 0.50:
        reasons.append("confidence_drop")
    b = candidate.get("brain")
    if b is None:
        b = candidate.get("brain_score")
    bf = _f(b)
    if bf is not None and bf < MATCH_FLOOR:
        reasons.append("brain_disagreement")
    if reality_score is not None and float(reality_score) < MIN_REALITY:
        reasons.append("reality_FAIL")
    elif reality_score is None:
        reasons.append("reality_FAIL")
    return reasons


def entry_check(
    candidate: dict[str, Any],
    *,
    reality_score: float | None,
    duplicate: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """
    Returns (allowed, fail_reasons, almost_rejected).
    almost_rejected = near-miss reasons that almost blocked entry.
    """
    fails: list[str] = []
    almost: list[str] = []

    # STOP first
    stops = stop_reasons(candidate, reality_score=reality_score)
    # De-noise: if not a TRADE candidate, decision gate dominates stop noise
    if str(candidate.get("decision") or "") != "TRADE":
        fails.append("decision!=TRADE")
        # keep only hard regime/reality stops
        for s in stops:
            if s in ("REGIME_EXPLORE", "unknown_regime", "reality_FAIL"):
                fails.append(s)
    else:
        fails.extend(stops)

    if str(candidate.get("decision") or "") == "TRADE":
        pass  # decision ok
    elif "decision!=TRADE" not in fails:
        fails.append("decision!=TRADE")

    rank = str(candidate.get("decision_rank") or candidate.get("rank") or "")
    if rank not in MIN_RANK:
        fails.append(f"rank<{MIN_RANK[1]}")
    elif rank == "A":
        almost.append("rank_is_A_not_A+")

    conf = _f(candidate.get("confidence"))
    if conf is None or conf < MIN_CONFIDENCE:
        fails.append(f"confidence<{MIN_CONFIDENCE}")
    elif conf < 0.80:
        almost.append("confidence_near_floor")

    if not brain_supports(candidate):
        if "brain_disagreement" not in fails:
            fails.append("brain_not_support")

    rs = _f(reality_score)
    if rs is None or rs < MIN_REALITY:
        if "reality_FAIL" not in fails:
            fails.append(f"reality_score<{MIN_REALITY}")
    elif rs < 85:
        almost.append("reality_near_floor")

    wr = _pct_or_01(candidate.get("historical_wr"))
    if wr is None or wr < MIN_HIST_WR:
        fails.append(f"historical_wr<{MIN_HIST_WR}")
    elif wr < 75:
        almost.append("historical_wr_near_floor")

    pf = _f(candidate.get("historical_pf"))
    if pf is None or pf < MIN_HIST_PF:
        fails.append(f"historical_pf<{MIN_HIST_PF}")
    elif pf < 2.0:
        almost.append("historical_pf_near_floor")

    ev = _f(candidate.get("historical_ev"))
    if ev is None or ev <= 0:
        fails.append("historical_ev<=0")

    tl = _f(candidate.get("timeline_similarity"))
    if tl is None or tl < MIN_TIMELINE:
        fails.append(f"timeline_similarity<{MIN_TIMELINE}")
    elif tl < 0.70:
        almost.append("timeline_near_floor")

    fp = _f(candidate.get("fingerprint_similarity"))
    if fp is None or fp < MIN_FINGERPRINT:
        fails.append(f"fingerprint_similarity<{MIN_FINGERPRINT}")

    if not is_match(candidate.get("replay")):
        fails.append("replay!=MATCH")
    if not is_match(candidate.get("dna")):
        fails.append("dna!=MATCH")
    if not is_match(candidate.get("rules"), rules=True):
        fails.append("rules!=MATCH")

    if regime_blocked(candidate):
        # already in stops; ensure present
        pass

    if duplicate:
        fails.append("book_duplicate")

    # de-dupe fails preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in fails:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return (len(uniq) == 0, uniq, almost)


def book_c_allows(ok_entry: bool, fails: list[str]) -> bool:
    """Mathematical Elite: full entry pass required (no discretionary)."""
    return bool(ok_entry)


def book_d_allows(
    ok_entry: bool,
    fails: list[str],
    *,
    duplicate: bool,
    feature_store_ok: bool = True,
) -> bool:
    """Strict Mathematics reference: entry pass + never duplicate + Feature Store required."""
    if not feature_store_ok:
        return False
    if duplicate or "book_duplicate" in fails:
        return False
    return bool(ok_entry)


__all__ = [
    "MATCH_FLOOR",
    "MIN_CONFIDENCE",
    "MIN_FINGERPRINT",
    "MIN_HIST_PF",
    "MIN_HIST_WR",
    "MIN_RANK",
    "MIN_REALITY",
    "MIN_SAMPLE",
    "MIN_TIMELINE",
    "book_c_allows",
    "book_d_allows",
    "brain_supports",
    "entry_check",
    "is_match",
    "regime_blocked",
    "stop_reasons",
]
