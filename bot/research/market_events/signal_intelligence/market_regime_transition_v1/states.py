"""State labeling + lookback history for regime transitions."""

from __future__ import annotations

from typing import Any

LOOKBACKS = (1, 3, 5, 10, 20, 50)

STATES = (
    "RANGE",
    "WEAK_BULL",
    "STRONG_BULL",
    "WEAK_BEAR",
    "STRONG_BEAR",
    "UNKNOWN",
    "AUTO_CLUSTER",
)


def _f(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def canon_state(row: dict[str, Any]) -> str:
    """Map lake/journal fields → Markov state (existing data only)."""
    raw = str(row.get("regime") or row.get("market_regime") or "").upper()
    if "STRONG" in raw and ("BULL" in raw or "UP" in raw):
        return "STRONG_BULL"
    if "STRONG" in raw and ("BEAR" in raw or "DOWN" in raw):
        return "STRONG_BEAR"
    if "WEAK" in raw and ("BULL" in raw or "UP" in raw):
        return "WEAK_BULL"
    if "WEAK" in raw and ("BEAR" in raw or "DOWN" in raw):
        return "WEAK_BEAR"
    if "RANGE" in raw or "SIDE" in raw or "CHOP" in raw:
        return "RANGE"
    if "BULL" in raw or "RISK_ON" in raw or "UPTREND" in raw:
        return "WEAK_BULL"
    if "BEAR" in raw or "RISK_OFF" in raw or "DOWNTREND" in raw:
        return "WEAK_BEAR"
    if "CLUSTER" in raw or "AUTO" in raw:
        return "AUTO_CLUSTER"

    # Derive from existing indicators (no new features)
    adx = _f(row.get("adx"), None)
    trend = _f(row.get("trend"), None)
    direction = str(row.get("direction") or "").upper()
    pnl = _f(row.get("pnl"), 0.0) or 0.0
    if adx is not None and trend is not None:
        if adx < 18 and abs(trend) < 0.15:
            return "RANGE"
        if adx >= 28:
            if trend > 0.2 or (direction == "LONG" and pnl >= 0):
                return "STRONG_BULL"
            if trend < -0.2 or (direction == "SHORT" and pnl >= 0):
                return "STRONG_BEAR"
        if trend > 0.05 or (direction == "LONG" and pnl > 0):
            return "WEAK_BULL"
        if trend < -0.05 or (direction == "SHORT" and pnl > 0):
            return "WEAK_BEAR"

    # Fallback from direction × outcome (still existing fields only)
    if direction == "LONG" and pnl > 0:
        return "WEAK_BULL"
    if direction == "SHORT" and pnl > 0:
        return "WEAK_BEAR"
    if direction == "LONG" and pnl < 0:
        return "WEAK_BEAR"
    if direction == "SHORT" and pnl < 0:
        return "WEAK_BULL"
    if abs(pnl) < 1e-12:
        return "RANGE"
    return "UNKNOWN"


def outcome_token(row: dict[str, Any]) -> str:
    pnl = _f(row.get("pnl"), 0.0) or 0.0
    result = str(row.get("result") or "").upper()
    if result in ("WIN", "LOSS", "BE"):
        return "W" if result == "WIN" else ("L" if result == "LOSS" else "B")
    if abs(pnl) < 1e-12:
        return "B"
    return "W" if pnl > 0 else "L"


def direction_token(row: dict[str, Any]) -> str:
    d = str(row.get("direction") or "").upper()
    if d in ("LONG", "BUY"):
        return "LONG"
    if d in ("SHORT", "SELL"):
        return "SHORT"
    return "UNK"


def lw_pattern(rows: list[dict[str, Any]]) -> str:
    """e.g. LLLLWLL from prior outcomes."""
    return "".join(outcome_token(r) for r in rows)


def build_history_slices(
    ordered: list[dict[str, Any]],
    i: int,
    lookbacks: tuple[int, ...] = LOOKBACKS,
) -> dict[int, list[dict[str, Any]]]:
    """Previous 1/3/5/10/20/50 closed trades before index i."""
    out: dict[int, list[dict[str, Any]]] = {}
    for lb in lookbacks:
        start = max(0, i - lb)
        out[lb] = ordered[start:i]
    return out


def transition_vector(
    prior: list[dict[str, Any]],
    current: dict[str, Any],
    *,
    journal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact vector from existing lake + optional journal scores."""
    j = journal or {}
    pattern = lw_pattern(prior)
    states = [canon_state(r) for r in prior]
    return {
        "pattern": pattern,
        "prior_states": states,
        "from_state": states[-1] if states else "UNKNOWN",
        "to_state": canon_state(current),
        "prior_dirs": [direction_token(r) for r in prior],
        "current_dir": direction_token(current),
        "current_outcome": outcome_token(current),
        "symbol": current.get("symbol"),
        "hour": current.get("hour"),
        "weekday": current.get("weekday"),
        "atr": _f(current.get("atr")),
        "adx": _f(current.get("adx")),
        "rsi": _f(current.get("rsi")),
        "macd": _f(current.get("macd")),
        "funding": _f(current.get("funding")),
        "oi_delta": _f(current.get("oi_delta")),
        "volatility": _f(current.get("volatility")),
        "gate": current.get("gate"),
        "dna": j.get("dna"),
        "rules": j.get("rules"),
        "decision": j.get("decision"),
        "replay": j.get("replay"),
        "brain": j.get("brain") if j.get("brain") is not None else j.get("confidence"),
        "timeline_similarity": j.get("timeline_similarity"),
        "fingerprint_similarity": j.get("fingerprint_similarity"),
        "historical_wr": j.get("historical_wr") if j else current.get("historical_wr"),
        "historical_pf": j.get("historical_pf"),
        "historical_ev": j.get("historical_ev"),
        "pnl": _f(current.get("pnl"), 0.0),
        "trade_id": int(current.get("trade_id") or current.get("id") or 0),
        "opened_at": int(current.get("opened_at") or 0),
        "closed_at": int(current.get("closed_at") or 0),
    }


__all__ = [
    "LOOKBACKS",
    "STATES",
    "build_history_slices",
    "canon_state",
    "direction_token",
    "lw_pattern",
    "outcome_token",
    "transition_vector",
]
