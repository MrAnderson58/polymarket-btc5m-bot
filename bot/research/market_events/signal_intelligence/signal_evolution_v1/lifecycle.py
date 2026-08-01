"""Signal lifecycle state machine: BIRTH → GROWTH → PEAK → DECAY → DEAD."""

from __future__ import annotations

from typing import Any


def classify_lifecycle(
    *,
    n: int,
    age_days: float | None,
    rolling: dict[str, Any],
    decay: dict[str, Any],
    survival: dict[str, Any],
) -> str:
    last50 = (rolling.get("last_50") or {})
    last100 = (rolling.get("last_100") or {})
    all_m = rolling.get("all") or {}
    ev50 = last50.get("ev")
    ev100 = last100.get("ev")
    ev_all = all_m.get("ev")
    slope = decay.get("slope")
    half = decay.get("half_life_days")
    surv = float(survival.get("survival_prob") or 0.0)
    edge_today = decay.get("edge_today")

    if n < 20:
        return "BIRTH"

    # DEAD: recent edge non-positive with enough sample, or survival collapsed
    recent_ev = ev50 if ev50 is not None else ev100
    if n >= 40 and recent_ev is not None and recent_ev <= 0 and surv < 0.35:
        return "DEAD"
    if n >= 60 and edge_today is not None and edge_today <= 0 and (ev_all or 0) <= 0:
        return "DEAD"

    # DECAY: negative slope / short half-life / today << history
    decaying = False
    if slope is not None and slope < -0.01:
        decaying = True
    if half is not None and 0 < half < 45 and (ev_all or 0) > 0 and (recent_ev or 0) < (ev_all or 0):
        decaying = True
    e0, e30 = decay.get("edge_today"), decay.get("edge_30d_ago")
    if e0 is not None and e30 is not None and e30 > 0 and e0 < 0.5 * e30:
        decaying = True
    if decaying and n >= 30:
        return "DECAY"

    # PEAK: strong recent + flat/ mild slope + high survival
    wr50 = last50.get("wr")
    if (
        n >= 40
        and recent_ev is not None
        and recent_ev > 0
        and (wr50 or 0) >= 0.55
        and surv >= 0.55
        and (slope is None or abs(slope) <= 0.02)
    ):
        return "PEAK"

    # GROWTH: improving slope / young improving edge
    if slope is not None and slope > 0.005 and (recent_ev or 0) > 0:
        return "GROWTH"
    if age_days is not None and age_days < 60 and (recent_ev or 0) > 0 and n < 80:
        return "GROWTH"
    if (recent_ev or 0) > 0:
        return "GROWTH"

    return "DECAY" if n >= 30 else "BIRTH"


__all__ = ["classify_lifecycle"]
