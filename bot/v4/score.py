"""Trend scoring for V4 Shadow."""

from __future__ import annotations

from bot.v4.trend_detector import TrendSignal


def compute_trend_score(signal: TrendSignal) -> tuple[float, float]:
    """
    Return (score 0-10, probability 0-1) for a detected trend.
    """
    delta_score = min(4.0, abs(signal.delta_change) / 20.0)
    ask_score = min(3.0, max(0.0, signal.ask_change) / 0.015)
    consistency_score = min(2.0, signal.consistency * 2.0)
    spread_bonus = 1.0 if signal.ask_change >= 0.02 else 0.5 if signal.ask_change >= 0.01 else 0.0

    score = min(10.0, delta_score + ask_score + consistency_score + spread_bonus)
    probability = min(1.0, score / 10.0 + signal.consistency * 0.15)
    return score, probability


def meets_entry_threshold(
    score: float,
    probability: float,
    *,
    min_score: float,
    min_probability: float,
) -> bool:
    return score >= min_score and probability >= min_probability
