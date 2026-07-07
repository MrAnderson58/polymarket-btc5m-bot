import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
GAMMA_API = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com")
BINANCE_API = os.getenv("BINANCE_API", "https://api.binance.com")
BTC_SYMBOL = os.getenv("BTC_SYMBOL", "BTCUSDT")
BTC_PRICE_CACHE_TTL_SEC = float(os.getenv("BTC_PRICE_CACHE_TTL_SEC", "2.0"))

POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "2"))
STRATEGY_WINDOW_SEC = int(os.getenv("STRATEGY_WINDOW_SEC", "45"))
STRIKE_THRESHOLD_USD = float(os.getenv("STRIKE_THRESHOLD_USD", "150"))
TRADE_SIZE_USDC = float(os.getenv("TRADE_SIZE_USDC", "10.0"))

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "trades.db"))

BTC_5M_SLUG_PREFIX = "btc-updown-5m"
WINDOW_SECONDS = 300
EARLY_REVERSION_WINDOW_SEC = int(os.getenv("EARLY_REVERSION_WINDOW_SEC", "30"))
EARLY_REVERSION_POSITION_SIZE_USDC = float(
    os.getenv("EARLY_REVERSION_POSITION_SIZE_USDC", "2.10")
)

_DEFAULT_ENABLED_STRATEGIES = "NO_C"


def _parse_enabled_strategies(
    env_key: str,
    *,
    inherit_from: str | None = "ENABLED_STRATEGIES",
) -> frozenset[str]:
    raw = os.getenv(env_key)
    if raw is None and inherit_from is not None:
        raw = os.getenv(inherit_from)
    if raw is None:
        raw = _DEFAULT_ENABLED_STRATEGIES
    return frozenset(
        name.strip()
        for name in raw.split(",")
        if name.strip()
    )


def format_enabled_strategies(strategies: frozenset[str]) -> str:
    order = ("NO_C", "YES_B", "YES_C", "YES_A", "NO_A", "NO_B")
    known = [name for name in order if name in strategies]
    extra = sorted(name for name in strategies if name not in order)
    combined = known + extra
    return ",".join(combined) if combined else "(none)"


def _parse_bool(env_key: str, *, default: bool = False) -> bool:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLED_STRATEGIES = _parse_enabled_strategies("ENABLED_STRATEGIES", inherit_from=None)
ENABLED_STRATEGIES_V2 = _parse_enabled_strategies("ENABLED_STRATEGIES_V2")
ENABLED_STRATEGIES_V25 = _parse_enabled_strategies("ENABLED_STRATEGIES_V25")
ENABLED_STRATEGIES_V3 = _parse_enabled_strategies("ENABLED_STRATEGIES_V3")

ENABLE_V1 = _parse_bool("ENABLE_V1", default=False)
ENABLE_V2 = _parse_bool("ENABLE_V2", default=True)
ENABLE_V25 = _parse_bool("ENABLE_V25", default=False)
ENABLE_V3 = _parse_bool("ENABLE_V3", default=False)
ENABLE_V4_SHADOW = _parse_bool("ENABLE_V4_SHADOW", default=False)
ENABLE_YES_C_SHADOW = _parse_bool("ENABLE_YES_C_SHADOW", default=True)
ENABLE_NO_C_FILTER_SHADOW = _parse_bool("ENABLE_NO_C_FILTER_SHADOW", default=True)
ENABLE_LATE_WINDOW = _parse_bool("ENABLE_LATE_WINDOW", default=False)
ENABLE_TRAILING_STOP = _parse_bool("ENABLE_TRAILING_STOP", default=True)

_EXIT_MODE_RAW = os.getenv("EXIT_MODE", "trailing").strip().lower()
if _EXIT_MODE_RAW not in {"fixed", "trailing"}:
    raise ValueError(
        f"Invalid EXIT_MODE={_EXIT_MODE_RAW!r}; expected 'fixed' or 'trailing'"
    )
EXIT_MODE = _EXIT_MODE_RAW

TRAILING_ACTIVATION_PROFIT = float(
    os.getenv(
        "TRAILING_ACTIVATION_PROFIT",
        os.getenv("TRAIL_ACTIVATION_DELTA", "0.03"),
    )
)
TRAILING_OFFSET = float(
    os.getenv(
        "TRAILING_OFFSET",
        os.getenv("TRAIL_STOP_DELTA", "0.01"),
    )
)
# Backward-compatible aliases
TRAIL_ACTIVATION_DELTA = TRAILING_ACTIVATION_PROFIT
TRAIL_STOP_DELTA = TRAILING_OFFSET

ER_V2_ENTRY_WINDOW_SEC = int(os.getenv("ER_V2_ENTRY_WINDOW_SEC", "30"))
ER_ENTRY_PRICE_OFFSET = float(os.getenv("ER_ENTRY_PRICE_OFFSET", "0"))
ER_V2_GRACE_PERIOD_SEC = int(os.getenv("ER_V2_GRACE_PERIOD_SEC", "30"))
ER_V2_TRAILING_STOP_PCT = float(os.getenv("ER_V2_TRAILING_STOP_PCT", "5"))
ER_V2_STOP_LOSS_PCT = float(os.getenv("ER_V2_STOP_LOSS_PCT", "-10"))
ER_V2_TIME_STOP_SEC = int(os.getenv("ER_V2_TIME_STOP_SEC", "90"))

ER_V25_GRACE_PERIOD_SEC = int(os.getenv("ER_V25_GRACE_PERIOD_SEC", "15"))

ER_V3_POLL_INTERVAL_SEC = float(os.getenv("ER_V3_POLL_INTERVAL_SEC", "0.5"))
ER_V3_ENTRY_WINDOW_SEC = int(os.getenv("ER_V3_ENTRY_WINDOW_SEC", "30"))
ER_V3_TRAILING_GRACE_SEC = int(os.getenv("ER_V3_TRAILING_GRACE_SEC", "30"))
ER_V3_TRAILING_STOP_PCT = float(os.getenv("ER_V3_TRAILING_STOP_PCT", "5"))
ER_V3_STOP_LOSS_PCT = float(os.getenv("ER_V3_STOP_LOSS_PCT", "-10"))
ER_V3_TIME_STOP_SEC = int(os.getenv("ER_V3_TIME_STOP_SEC", "90"))

ER_SUMMARY_INTERVAL_SEC = float(os.getenv("ER_SUMMARY_INTERVAL_SEC", "300"))

V4_POLL_INTERVAL_SEC = float(os.getenv("V4_POLL_INTERVAL_SEC", "1"))
# MTF HTF snapshot collector — decoupled from main POLL_INTERVAL_SEC to avoid blocking V4
MTF_POLL_INTERVAL_SEC = float(os.getenv("MTF_POLL_INTERVAL_SEC", "60"))
V4_OBSERVE_SECONDS = int(os.getenv("V4_OBSERVE_SECONDS", "120"))
V4_MIN_PROBABILITY = float(os.getenv("V4_MIN_PROBABILITY", "0.70"))
V4_MIN_SCORE = float(os.getenv("V4_MIN_SCORE", "8"))
V4_PULLBACK = float(os.getenv("V4_PULLBACK", "0.02"))

_TRADING_MODE_RAW = os.getenv("TRADING_MODE", "paper").strip().lower()
VALID_TRADING_MODES = frozenset({"paper", "dry_run", "live"})
if _TRADING_MODE_RAW not in VALID_TRADING_MODES:
    raise ValueError(
        f"Invalid TRADING_MODE={_TRADING_MODE_RAW!r}; expected one of {sorted(VALID_TRADING_MODES)}"
    )
TRADING_MODE = _TRADING_MODE_RAW

POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "").strip() or None
POLY_PROXY_WALLET = os.getenv("POLY_PROXY_WALLET", "").strip() or None
POLY_SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))

MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
MAX_DAILY_LOSS_USDC = float(os.getenv("MAX_DAILY_LOSS_USDC", "10.0"))

LIVE_EXIT_ENABLED = os.getenv("LIVE_EXIT_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

LIVE_ENABLED = _parse_bool("LIVE_ENABLED", default=True)
LIVE_MODE = os.getenv("LIVE_MODE", "micro").strip().lower()
PORTFOLIO_STARTING_BALANCE_USDC = float(os.getenv("PORTFOLIO_STARTING_BALANCE_USDC", "100.0"))
PORTFOLIO_DAILY_LOSS_LIMIT_USDC = float(os.getenv("PORTFOLIO_DAILY_LOSS_LIMIT_USDC", "5.0"))
CONSECUTIVE_STOPS_DAILY_PAUSE = int(os.getenv("CONSECUTIVE_STOPS_DAILY_PAUSE", "5"))
CIRCUIT_BREAKER_STOPS = int(os.getenv("CIRCUIT_BREAKER_STOPS", "3"))
CIRCUIT_BREAKER_PAUSE_SEC = int(os.getenv("CIRCUIT_BREAKER_PAUSE_SEC", str(30 * 60)))

if LIVE_MODE == "micro":
    EARLY_REVERSION_POSITION_SIZE_USDC = min(EARLY_REVERSION_POSITION_SIZE_USDC, 1.0)


def is_live_trading_enabled() -> bool:
    return TRADING_MODE == "live"


def is_dry_run_mode() -> bool:
    return TRADING_MODE == "dry_run"


def is_paper_mode() -> bool:
    return TRADING_MODE == "paper"


def is_live_exit_enabled() -> bool:
    return LIVE_EXIT_ENABLED


def effective_entry_threshold(entry_threshold: float) -> float:
    return entry_threshold + ER_ENTRY_PRICE_OFFSET
