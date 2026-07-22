"""S51 — Confidence calibration from historical S48 outcomes."""

from __future__ import annotations

from typing import Any


def _bucket(conf: float) -> str:
    if conf < 50:
        return "low"
    if conf < 70:
        return "mid"
    return "high"


def historical_hit_rates(
    outcomes: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Per confidence bucket: win_rate and sample size."""
    buckets: dict[str, list[bool]] = {"low": [], "mid": [], "high": []}
    for o in outcomes:
        try:
            conf = float(o.get("confidence") if o.get("confidence") is not None else 50)
        except (TypeError, ValueError):
            conf = 50.0
        win = float(o.get("pnl_usd") or 0) > 0 or float(o.get("r_multiple") or 0) > 0
        buckets[_bucket(conf)].append(win)
    out: dict[str, dict[str, float]] = {}
    for name, rows in buckets.items():
        n = len(rows)
        wr = (sum(1 for x in rows if x) / n) if n else 0.0
        out[name] = {"n": float(n), "win_rate": round(wr * 100.0, 1)}
    return out


def calibrate_confidence(
    raw_confidence: float,
    *,
    outcomes: list[dict[str, Any]] | None = None,
    min_samples: int = 8,
) -> float:
    """
    Shrink raw confidence toward historically realized win-rate for its bucket.
    With few samples, return clipped raw confidence.
    """
    raw = max(0.0, min(100.0, float(raw_confidence)))
    if not outcomes:
        return round(raw, 1)

    rates = historical_hit_rates(outcomes)
    b = _bucket(raw)
    stats = rates.get(b) or {"n": 0.0, "win_rate": 50.0}
    n = int(stats["n"])
    if n < min_samples:
        return round(raw, 1)

    realized = float(stats["win_rate"])
    # Blend: more history → trust realized more
    w = min(0.65, 0.25 + n / 80.0)
    calibrated = (1.0 - w) * raw + w * realized
    # Keep some separation by raw confidence
    if raw >= 70:
        calibrated = max(calibrated, realized)
    return round(max(5.0, min(95.0, calibrated)), 1)


def calibrate_from_repository(
    raw_confidence: float,
    *,
    repo: Any | None = None,
) -> float:
    if repo is None:
        from bot.research.ai_analyst.signal_consistency.repository import get_repository
        repo = get_repository()
    outcomes = repo.list_outcomes(limit=500)
    return calibrate_confidence(raw_confidence, outcomes=outcomes)
