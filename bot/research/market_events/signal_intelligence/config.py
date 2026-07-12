"""Phase F.0 configuration — independent from SHOCK_A–F thresholds."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

F0_ENABLED = os.getenv("ME_F0_SIGNAL_INTELLIGENCE", "true").lower() in ("1", "true", "yes")
F0_TELEGRAM_FORMAT = os.getenv("ME_TELEGRAM_F0_FORMAT", "true").lower() in ("1", "true", "yes")
F0_AI_ENABLED = os.getenv("ME_F0_AI_ENABLED", "true").lower() in ("1", "true", "yes")

F1_ENABLED = os.getenv("ME_F1_TELEGRAM_INTELLIGENCE", "true").lower() in ("1", "true", "yes")
F1_TELEGRAM_FORMAT = os.getenv("ME_F1_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")

F2_ENABLED = os.getenv("ME_F2_PROFESSIONAL_INTEL", "true").lower() in ("1", "true", "yes")
F2_TELEGRAM_FORMAT = os.getenv("ME_F2_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")

F3_TELEGRAM_FORMAT = os.getenv("ME_F3_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")

TREND_SHOCK_ENABLED = os.getenv("ME_TREND_SHOCK_ENABLED", "true").lower() in ("1", "true", "yes")
TREND_SHOCK_DEFER_ALERT = os.getenv("ME_TREND_SHOCK_DEFER_ALERT", "true").lower() in ("1", "true", "yes")
TREND_PREMIUM_TELEGRAM = os.getenv("ME_TREND_PREMIUM_TELEGRAM", "true").lower() in ("1", "true", "yes")
TREND_SIGNAL_RANKING = os.getenv("ME_TREND_SIGNAL_RANKING", "true").lower() in ("1", "true", "yes")

F4_VISUAL_INTEL_ENABLED = os.getenv("ME_F4_VISUAL_INTEL", "true").lower() in ("1", "true", "yes")
F4_TREND_SHOCK_V2_ENABLED = os.getenv("ME_F4_TREND_SHOCK_V2", "true").lower() in ("1", "true", "yes")
F4_TELEGRAM_FORMAT = os.getenv("ME_F4_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")

PROMPT_VERSION_F0 = "f0_signal_v1"

MTF_DETECTORS = {
    "SHOCK_M5": {"window_minutes": 5, "min_return_pct": 1.2, "min_atr_multiple": 1.5},
    "SHOCK_M10": {"window_minutes": 10, "min_return_pct": 1.5, "min_atr_multiple": 1.6},
    "SHOCK_M15": {"window_minutes": 15, "min_return_pct": 1.8, "min_atr_multiple": 1.7},
    "SHOCK_M30": {"window_minutes": 30, "min_return_pct": 2.5, "min_atr_multiple": 2.0},
}

EXHAUSTION_MIN_CONSECUTIVE = int(os.getenv("ME_F0_EXHAUSTION_MIN_CANDLES", "4"))
EXHAUSTION_MIN_SCORE = float(os.getenv("ME_F0_EXHAUSTION_MIN_SCORE", "55"))

RESOLVER_VENUES = ("bybit", "binance", "okx")
