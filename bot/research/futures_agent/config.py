"""Futures Intelligence Agent configuration."""

from __future__ import annotations

import os

from bot.research.futures_agent.env_bootstrap import bootstrap_config, resolve_agent_db_config

PARSER_VERSION = "deterministic_v2"
AGENT_SCHEMA_VERSION = 6

AGENT_TABLE_ALLOWLIST = frozenset({
    "futures_agent_migrations",
    "futures_agent_inputs",
    "futures_agent_signals",
    "futures_agent_targets",
    "futures_agent_market_snapshots",
    "futures_agent_btc_context",
    "futures_agent_relative_strength",
    "futures_agent_trader_posts",
    "futures_agent_trader_theses",
    "futures_agent_trader_levels",
    "futures_agent_thesis_outcomes",
    "futures_agent_source_scores",
    "futures_agent_source_scores_v2",
    "futures_agent_research_market_data_cache",
    "futures_agent_research_signal_outcomes",
    "futures_agent_research_signal_events",
    "futures_agent_research_signal_markouts",
    "futures_agent_telegram_research_bridge",
})

INPUT_TYPE_FORWARDED = "forwarded"
INPUT_TYPE_CLI = "cli"
INPUT_TYPE_CHANNEL = "channel"
INPUT_TYPE_TELEGRAM = "telegram"

STATUS_RECEIVED = "received"
STATUS_PARSED = "parsed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected_non_signal"
STATUS_PENDING_SNAPSHOT = "pending_snapshot"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

PARSE_STATUS_SUCCESS = "SUCCESS"
PARSE_STATUS_PARTIAL = "PARTIAL"
PARSE_STATUS_FAILED = "FAILED"
PARSE_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

# Data quality statuses
DATA_QUALITY_COMPLETE = "COMPLETE"
DATA_QUALITY_PARTIAL = "PARTIAL"
DATA_QUALITY_STALE = "STALE"
DATA_QUALITY_API_UNAVAILABLE = "API_UNAVAILABLE"
DATA_QUALITY_SYMBOL_UNAVAILABLE = "SYMBOL_UNAVAILABLE"

# BTC regime labels
BTC_REGIME_STRONG_UP = "BTC_STRONG_UP"
BTC_REGIME_UP = "BTC_UP"
BTC_REGIME_MIXED = "BTC_MIXED"
BTC_REGIME_DOWN = "BTC_DOWN"
BTC_REGIME_STRONG_DOWN = "BTC_STRONG_DOWN"

# Volatility regime
VOL_REGIME_LOW = "LOW_VOL"
VOL_REGIME_NORMAL = "NORMAL_VOL"
VOL_REGIME_HIGH = "HIGH_VOL"

# Signal alignment
ALIGNMENT_ALIGNED = "ALIGNED_WITH_BTC"
ALIGNMENT_COUNTERTREND = "COUNTERTREND_BTC"
ALIGNMENT_ALT_RS = "ALT_RELATIVE_STRENGTH"
ALIGNMENT_ALT_RW = "ALT_RELATIVE_WEAKNESS"
ALIGNMENT_NEUTRAL = "BTC_NEUTRAL"

# Preliminary research labels (observe-only, not trading advice)
RESEARCH_SUPPORTIVE = "SUPPORTIVE_CONTEXT"
RESEARCH_MIXED = "MIXED_CONTEXT"
RESEARCH_COUNTERTREND = "COUNTERTREND_CONTEXT"
RESEARCH_HIGH_RISK = "HIGH_RISK_CONTEXT"
RESEARCH_INSUFFICIENT = "INSUFFICIENT_DATA"

# Signal vs market sanity (research only)
SIGNAL_MARKET_ENTRY_NEAR = "ENTRY_NEAR_MARKET"
SIGNAL_MARKET_ENTRY_PENDING = "ENTRY_PENDING"
SIGNAL_MARKET_ENTRY_ALREADY_PASSED = "ENTRY_ALREADY_PASSED"
SIGNAL_MARKET_STALE = "STALE_SIGNAL"
SIGNAL_MARKET_INVALID_PRICE = "INVALID_PRICE_CONTEXT"

CANONICAL_ALIGNMENT_LABELS = frozenset({
    ALIGNMENT_ALIGNED,
    ALIGNMENT_COUNTERTREND,
    ALIGNMENT_ALT_RS,
    ALIGNMENT_ALT_RW,
    ALIGNMENT_NEUTRAL,
})

MIN_CORRELATION_SAMPLES = 5
STALE_DATA_LAG_SECONDS = 120
RETURN_4H_MIN_CANDLES = 240

EXCHANGE_BINANCE = "binance"
CONTEXT_SYMBOL_BTC = "BTC"
CONTEXT_SYMBOL_ETH = "ETH"


def get_agent_database_url() -> str | None:
    bootstrap_config()
    return os.getenv("FUTURES_AGENT_DATABASE_URL") or None


def get_agent_sqlite_fallback_path() -> str | None:
    bootstrap_config()
    return os.getenv("FUTURES_AGENT_SQLITE_PATH") or None


def include_eth_context() -> bool:
    bootstrap_config()
    return os.getenv("FUTURES_AGENT_INCLUDE_ETH", "1").strip() not in ("0", "false", "False")


def telegram_notify_enabled() -> bool:
    bootstrap_config()
    return bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_AGENT_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    )


def get_telegram_chat_id() -> str | None:
    bootstrap_config()
    return os.getenv("TELEGRAM_AGENT_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
