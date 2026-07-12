"""Phase F.5 — risk/reward and TP probability estimates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskRewardF5:
    risk_reward: float
    tp1_prob: float
    tp2_prob: float
    tp3_prob: float
    tp1_pct: float
    tp2_pct: float
    tp3_pct: float
    stop_pct: float


def compute_risk_reward_f5(
    *,
    expected_target_pct: float,
    expected_stop_pct: float,
    reversal_probability: float,
    dynamic_confidence: float,
    historical_reversal_rate: float,
) -> RiskRewardF5:
    stop = max(expected_stop_pct, 0.5)
    tp1 = max(expected_target_pct * 0.45, 0.8)
    tp2 = max(expected_target_pct * 0.75, 1.2)
    tp3 = max(expected_target_pct, 1.8)
    rr = round(tp2 / stop, 1) if stop else 0.0

    rev = reversal_probability if reversal_probability <= 1.0 else reversal_probability / 100.0
    base = min(0.95, max(0.15, rev * 0.55 + historical_reversal_rate * 0.35))
    conf_boost = min(0.15, (dynamic_confidence - 5.0) * 0.025)

    tp1_prob = round(min(0.92, base + conf_boost) * 100, 0)
    tp2_prob = round(min(0.85, base * 0.78 + conf_boost * 0.5) * 100, 0)
    tp3_prob = round(min(0.65, base * 0.55) * 100, 0)

    return RiskRewardF5(
        risk_reward=rr,
        tp1_prob=tp1_prob,
        tp2_prob=tp2_prob,
        tp3_prob=tp3_prob,
        tp1_pct=round(tp1, 2),
        tp2_pct=round(tp2, 2),
        tp3_pct=round(tp3, 2),
        stop_pct=round(stop, 2),
    )
