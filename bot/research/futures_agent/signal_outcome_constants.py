"""Phase D.1 constants — historical signal outcome engine."""

from __future__ import annotations

ENGINE_VERSION = "d1.0"

INTERVAL_1M_SEC = 60
ENTRY_TTL_SEC = 24 * 3600
MAX_EVAL_SEC = 24 * 3600

MARKOUT_HORIZONS_SEC: dict[str, int] = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "12h": 12 * 60 * 60,
    "24h": 24 * 60 * 60,
}

DATA_QUALITY_COMPLETE_THRESHOLD = 0.95
BOOTSTRAP_SEED = 42
BOOTSTRAP_SAMPLES = 1000

SAMPLE_INSUFFICIENT = 30
SAMPLE_LOW = 100
SAMPLE_MODERATE = 300

EXIT_POLICIES = ("P1", "P2", "P3", "P4", "P5", "P6")

DEFAULT_FEE_BPS = 0.0
DEFAULT_SLIPPAGE_BPS = 0.0

LEVERAGE_SCENARIOS = (2, 3, 5, 10)
