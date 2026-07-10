"""AI Analyst shadow layer — configuration (no secrets in repo)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

PROMPT_VERSION = "e33_shadow_v1"

AI_ENABLED = os.getenv("ME_AI_ANALYST_ENABLED", "false").lower() in ("1", "true", "yes")
AI_PROVIDER = os.getenv("ME_AI_PROVIDER", "none").strip().lower()
AI_MODEL = os.getenv("ME_AI_MODEL", "").strip()
AI_API_KEY = os.getenv("ME_AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv("ME_AI_BASE_URL", "").strip()
AI_TIMEOUT_SEC = float(os.getenv("ME_AI_TIMEOUT_SEC", "30"))
AI_MAX_RETRIES = int(os.getenv("ME_AI_MAX_RETRIES", "1"))
AI_MAX_CONTEXT_ITEMS = int(os.getenv("ME_AI_MAX_CONTEXT_ITEMS", "8"))
AI_JOBS_PER_CYCLE = int(os.getenv("ME_AI_JOBS_PER_CYCLE", "0"))

VALID_INTERPRETATIONS = frozenset({
    "NEWS_DRIVEN", "MARKET_WIDE", "ASSET_SPECIFIC", "LIQUIDITY_SWEEP",
    "SHORT_SQUEEZE", "LONG_LIQUIDATION", "UNKNOWN",
})

VALID_BIAS = frozenset({
    "FADE_FAVORED", "CONTINUATION_FAVORED", "WAIT_FOR_CONFIRMATION", "NO_VIEW",
})

VALID_STATUS = frozenset({"COMPLETE", "INSUFFICIENT_CONTEXT", "FAILED"})
