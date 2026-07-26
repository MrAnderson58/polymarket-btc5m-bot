"""Phase E.1 configuration — fixed thresholds, not optimized."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "market_events.db"

MARKET_EVENTS_DATABASE_PATH = Path(
    os.getenv("MARKET_EVENTS_DATABASE_PATH", str(DEFAULT_DB_PATH)),
)

# Core universe (always monitored when --universe core).
CORE_SYMBOLS = (
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX", "SUI",
)

POLL_INTERVAL_SEC = float(os.getenv("ME_POLL_INTERVAL_SEC", "1.0"))
PRICE_HISTORY_SEC = int(os.getenv("ME_PRICE_HISTORY_SEC", "300"))
SHOCK_DEDUP_WINDOW_SEC = int(os.getenv("ME_SHOCK_DEDUP_WINDOW_SEC", "300"))

# Fixed shock detector thresholds (% move, not optimized).
# Baseline / production profile — do not change without an explicit migration plan.
SHOCK_THRESHOLDS = {
    "SHOCK_A": {"window_sec": 30, "min_abs_return_pct": 1.5},
    "SHOCK_B": {"window_sec": 60, "min_abs_return_pct": 2.0},
    "SHOCK_C": {"window_sec": 180, "min_abs_return_pct": 3.0},
    "SHOCK_D": {"window_sec": 60, "min_abs_return_pct": 1.5, "min_volume_zscore": 2.0},
    "SHOCK_E": {"window_sec": 60, "min_relative_return_pct": 1.0},
}

# Shadow A/B profiles. Production pipeline always uses SHOCK_THRESHOLDS (baseline).
PROFILE_BASELINE = "baseline"
PROFILE_ADAPTIVE_V1 = "adaptive_v1"

# Adaptive shadow thresholds — research only; never activate paper/live pipeline.
ADAPTIVE_V1_THRESHOLDS = {
    "SHOCK_A": {"window_sec": 30, "min_abs_return_pct": 0.30},
    "SHOCK_B": {"window_sec": 60, "min_abs_return_pct": 0.50},
    "SHOCK_C": {"window_sec": 180, "min_abs_return_pct": 0.80},
    "SHOCK_D": {"window_sec": 60, "min_abs_return_pct": 0.50, "min_volume_zscore": 2.0},
    "SHOCK_E": {"window_sec": 60, "min_relative_return_pct": 0.30},
}

SHOCK_PROFILE_THRESHOLDS: dict[str, dict[str, dict[str, float]]] = {
    PROFILE_BASELINE: SHOCK_THRESHOLDS,
    PROFILE_ADAPTIVE_V1: ADAPTIVE_V1_THRESHOLDS,
}

# Shadow persistence: accepted rows always; rejects flushed on this interval (sec).
SHADOW_REJECT_FLUSH_SEC = int(os.getenv("ME_SHADOW_REJECT_FLUSH_SEC", "60"))
SHADOW_ENABLED = os.getenv("ME_ADAPTIVE_SHADOW_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# Reversal confirmation (fixed).
REVERSAL_CONFIGS = {
    "R1": {"reclaim_pct_of_shock": 0.35},
    "R2": {"local_reclaim_bars": 3},
    "R3": {"velocity_decay_ratio": 0.5, "min_reversal_return_pct": 0.15},
    "R4": {"volume_climax_zscore": 2.0, "reclaim_pct": 0.25},
    "R5": {"survival_sec": 30},
}

# Paper exit policies (fixed).
EXIT_POLICIES = {
    "EXIT_A": {"hard_stop_pct": 1.0, "tp_pct": 0.5},
    "EXIT_B": {"hard_stop_pct": 1.0, "be_trigger_pct": 0.3, "tp_pct": 0.8},
    "EXIT_C": {"hard_stop_pct": 1.0, "be_trigger_pct": 0.3, "trail_trigger_pct": 0.6, "trail_pct": 0.4},
    "EXIT_D": {"hard_stop_pct": 1.0, "partial_tp_pct": 0.5, "partial_frac": 0.5, "trail_pct": 0.5},
    "EXIT_E": {"hard_stop_pct": 1.2, "be_trigger_pct": 0.4, "trail_pct": 0.8},
}

DEFAULT_FEE_BPS = float(os.getenv("ME_PAPER_FEE_BPS", "10"))
DEFAULT_SLIPPAGE_BPS = float(os.getenv("ME_PAPER_SLIPPAGE_BPS", "10"))
DEFAULT_HEARTBEAT_SEC = int(os.getenv("ME_HEARTBEAT_SEC", "60"))

# Telegram alerts (opt-in, default off).
# ME_TELEGRAM_ALERTS_ENABLED, ME_ALERT_SHOCK, ME_ALERT_REVERSAL, ME_ALERT_PAPER_UPDATES
# ME_ALERT_CHAT_ID, ME_ALERT_AI_COMMENTARY — see alert_config.py

# AI analyst shadow (opt-in, default off).
# ME_AI_ANALYST_ENABLED, ME_AI_PROVIDER, ME_AI_MODEL, ME_AI_API_KEY — see ai_analyst/config.py

# Context link windows (seconds before event).
CONTEXT_WINDOWS_SEC = {
    "TELEGRAM_SIGNAL": 6 * 3600,
    "TRADER_THESIS": 24 * 3600,
    "MARKET_COMMENTARY": 12 * 3600,
    "NEWS": 2 * 3600,
    "WHALE_FLOW": 24 * 3600,
    "POLYMARKET_STATE": 30 * 60,
}
