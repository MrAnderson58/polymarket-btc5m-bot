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

POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "2"))
STRATEGY_WINDOW_SEC = int(os.getenv("STRATEGY_WINDOW_SEC", "45"))
STRIKE_THRESHOLD_USD = float(os.getenv("STRIKE_THRESHOLD_USD", "150"))
TRADE_SIZE_USDC = float(os.getenv("TRADE_SIZE_USDC", "10.0"))

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "trades.db"))

BTC_5M_SLUG_PREFIX = "btc-updown-5m"
WINDOW_SECONDS = 300
EARLY_REVERSION_WINDOW_SEC = int(os.getenv("EARLY_REVERSION_WINDOW_SEC", "30"))
EARLY_REVERSION_POSITION_SIZE_USDC = float(
    os.getenv("EARLY_REVERSION_POSITION_SIZE_USDC", "1.0")
)

_DEFAULT_ENABLED_STRATEGIES = "YES_B,YES_C,NO_C"


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


ENABLED_STRATEGIES = _parse_enabled_strategies("ENABLED_STRATEGIES", inherit_from=None)
ENABLED_STRATEGIES_V2 = _parse_enabled_strategies("ENABLED_STRATEGIES_V2")
ENABLED_STRATEGIES_V25 = _parse_enabled_strategies("ENABLED_STRATEGIES_V25")
ENABLED_STRATEGIES_V3 = _parse_enabled_strategies("ENABLED_STRATEGIES_V3")

ER_V2_ENTRY_WINDOW_SEC = int(os.getenv("ER_V2_ENTRY_WINDOW_SEC", "30"))
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


def is_live_trading_enabled() -> bool:
    return TRADING_MODE == "live"


def is_dry_run_mode() -> bool:
    return TRADING_MODE == "dry_run"


def is_paper_mode() -> bool:
    return TRADING_MODE == "paper"


def is_live_exit_enabled() -> bool:
    return LIVE_EXIT_ENABLED
