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
TREND_SHOCK_DEFER_ALERT = os.getenv("ME_TREND_SHOCK_DEFER_ALERT", "false").lower() in ("1", "true", "yes")
TREND_PREMIUM_TELEGRAM = os.getenv("ME_TREND_PREMIUM_TELEGRAM", "true").lower() in ("1", "true", "yes")
TREND_SIGNAL_RANKING = os.getenv("ME_TREND_SIGNAL_RANKING", "true").lower() in ("1", "true", "yes")

F4_VISUAL_INTEL_ENABLED = os.getenv("ME_F4_VISUAL_INTEL", "true").lower() in ("1", "true", "yes")
F4_TREND_SHOCK_V2_ENABLED = os.getenv("ME_F4_TREND_SHOCK_V2", "true").lower() in ("1", "true", "yes")
F4_TELEGRAM_FORMAT = os.getenv("ME_F4_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")

F41_TELEGRAM_DEDUPE = os.getenv("ME_F41_TELEGRAM_DEDUPE", "false").lower() in ("1", "true", "yes")

F5_ENABLED = os.getenv("ME_F5_PROFESSIONAL_SIGNAL", "true").lower() in ("1", "true", "yes")
F5_TELEGRAM_FORMAT = os.getenv("ME_F5_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")
F5_PRIORITY_ENGINE = os.getenv("ME_F5_PRIORITY_ENGINE", "true").lower() in ("1", "true", "yes")
F5_MIN_TELEGRAM_CONFIDENCE = float(os.getenv("ME_F5_MIN_TELEGRAM_CONFIDENCE", "7.0"))
F5_TOP_N_TELEGRAM = int(os.getenv("ME_F5_TOP_N_TELEGRAM", "3"))

F6_ENABLED = os.getenv("ME_F6_TRADER_PERFORMANCE", "true").lower() in ("1", "true", "yes")
F6_MIN_SIGNALS = int(os.getenv("ME_F6_MIN_SIGNALS", "10"))
F6_WR_HIGH = float(os.getenv("ME_F6_WR_HIGH", "0.82"))
F6_WR_LOW = float(os.getenv("ME_F6_WR_LOW", "0.38"))

F7_ENABLED = os.getenv("ME_F7_MARKET_INTELLIGENCE", "true").lower() in ("1", "true", "yes")
F7_TELEGRAM_FORMAT = os.getenv("ME_F7_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")
F7_MIN_MARKET_SCORE = float(os.getenv("ME_F7_MIN_MARKET_SCORE", "55"))
F7_MIN_FINAL_CONFIDENCE = float(os.getenv("ME_F7_MIN_FINAL_CONFIDENCE", "7.5"))

F72_ENABLED = os.getenv("ME_F72_SIGNAL_OUTCOME", "true").lower() in ("1", "true", "yes")
F72_CHECK_INTERVAL_SEC = int(os.getenv("ME_F72_CHECK_INTERVAL_SEC", "30"))
F72_MORNING_HOUR_UTC = int(os.getenv("ME_F72_MORNING_HOUR_UTC", "6"))

F73_ENABLED = os.getenv("ME_F73_NEAR_MISS", "true").lower() in ("1", "true", "yes")
F73_QUIET_MARKET_HOURS = int(os.getenv("ME_F73_QUIET_MARKET_HOURS", "12"))

G1_ENABLED = os.getenv("ME_G1_LIQUIDITY_TREND", "true").lower() in ("1", "true", "yes")
G1_MIN_REVERSAL_PROB = float(os.getenv("ME_G1_MIN_REVERSAL_PROB", "0.65"))

G2_ENABLED = os.getenv("ME_G2_CLAUDE_RESEARCH", "true").lower() in ("1", "true", "yes")
G2_MIN_CONFIDENCE = float(os.getenv("ME_G2_MIN_CONFIDENCE", "7.0"))
G2_MIN_MARKET_SCORE = float(os.getenv("ME_G2_MIN_MARKET_SCORE", "60"))
G2_MIN_G1_REVERSAL_PROB = float(os.getenv("ME_G2_MIN_G1_REVERSAL_PROB", "0.65"))
G2_TELEGRAM_FORMAT = os.getenv("ME_G2_TELEGRAM_FORMAT", "true").lower() in ("1", "true", "yes")
G2_DAILY_REQUEST_LIMIT = int(os.getenv("ME_G2_DAILY_REQUEST_LIMIT", "30"))
PROMPT_VERSION_G2 = "g2_research_v3"
G2_PROMPT_MAX_INPUT_TOKENS = int(os.getenv("ME_G2_PROMPT_MAX_INPUT_TOKENS", "1500"))

PROMPT_VERSION_F0 = "f0_signal_v1"

G3_ENABLED = os.getenv("ME_G3_LIVE_SIGNAL", "true").lower() in ("1", "true", "yes")
G3_RECORDER_INTERVAL_SEC = int(os.getenv("ME_G3_RECORDER_INTERVAL_SEC", "60"))
G3_SNAPSHOT_RETENTION_DAYS = int(os.getenv("ME_G3_SNAPSHOT_RETENTION_DAYS", "365"))
G3_MIN_CONFIDENCE = float(os.getenv("ME_G3_MIN_CONFIDENCE", "7.5"))
G3_MIN_MARKET_SCORE = float(os.getenv("ME_G3_MIN_MARKET_SCORE", "65"))
G3_MIN_LIQUIDITY_PROB = float(os.getenv("ME_G3_MIN_LIQUIDITY_PROB", "0.70"))
G3_MIN_RISK_REWARD = float(os.getenv("ME_G3_MIN_RISK_REWARD", "2.5"))
G3_MAX_SIGNALS_PER_DAY = int(os.getenv("ME_G3_MAX_SIGNALS_PER_DAY", "10"))
G3_DAILY_REPORT_HOUR_LOCAL = int(os.getenv("ME_G3_DAILY_REPORT_HOUR", "9"))
G3_MODEL_VERSION = "g3_v1"
G31_ENABLED = os.getenv("ME_G31_CANDIDATE_PIPELINE", "true").lower() in ("1", "true", "yes")
G31_IDLE_SIGNAL_HOURS = int(os.getenv("ME_G31_IDLE_SIGNAL_HOURS", "12"))
G31_DEFAULT_UNIVERSE = (
    "BTC", "ETH", "SOL", "MANTA", "SUI", "DOGE", "XRP", "BNB", "TON", "ADA",
    "LINK", "AVAX", "MATIC", "APT", "ARB", "OP", "PEPE", "WIF", "NEAR", "INJ",
)
G31_SIGNAL_SYMBOLS = tuple(
    s.strip().upper() for s in os.getenv("ME_G31_UNIVERSE_SYMBOLS", "").split(",") if s.strip()
)

MTF_DETECTORS = {
    "SHOCK_M5": {"window_minutes": 5, "min_return_pct": 1.2, "min_atr_multiple": 1.5},
    "SHOCK_M10": {"window_minutes": 10, "min_return_pct": 1.5, "min_atr_multiple": 1.6},
    "SHOCK_M15": {"window_minutes": 15, "min_return_pct": 1.8, "min_atr_multiple": 1.7},
    "SHOCK_M30": {"window_minutes": 30, "min_return_pct": 2.5, "min_atr_multiple": 2.0},
}

EXHAUSTION_MIN_CONSECUTIVE = int(os.getenv("ME_F0_EXHAUSTION_MIN_CANDLES", "4"))
EXHAUSTION_MIN_SCORE = float(os.getenv("ME_F0_EXHAUSTION_MIN_SCORE", "55"))

RESOLVER_VENUES = ("bybit", "binance", "okx")
