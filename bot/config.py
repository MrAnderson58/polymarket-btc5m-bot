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
ENABLED_STRATEGIES: frozenset[str] = frozenset(
    name.strip()
    for name in os.getenv("ENABLED_STRATEGIES", _DEFAULT_ENABLED_STRATEGIES).split(",")
    if name.strip()
)

_DEFAULT_ENABLED_STRATEGIES_V2 = "YES_B,YES_C,NO_C"
ENABLED_STRATEGIES_V2: frozenset[str] = frozenset(
    name.strip()
    for name in os.getenv("ENABLED_STRATEGIES_V2", _DEFAULT_ENABLED_STRATEGIES_V2).split(",")
    if name.strip()
)

ER_V2_ENTRY_WINDOW_SEC = int(os.getenv("ER_V2_ENTRY_WINDOW_SEC", "30"))
ER_V2_GRACE_PERIOD_SEC = int(os.getenv("ER_V2_GRACE_PERIOD_SEC", "30"))
ER_V2_TRAILING_STOP_PCT = float(os.getenv("ER_V2_TRAILING_STOP_PCT", "5"))
ER_V2_STOP_LOSS_PCT = float(os.getenv("ER_V2_STOP_LOSS_PCT", "-10"))
ER_V2_TIME_STOP_SEC = int(os.getenv("ER_V2_TIME_STOP_SEC", "90"))

_DEFAULT_ENABLED_STRATEGIES_V3 = "YES_B,YES_C,NO_C"
ENABLED_STRATEGIES_V3: frozenset[str] = frozenset(
    name.strip()
    for name in os.getenv("ENABLED_STRATEGIES_V3", _DEFAULT_ENABLED_STRATEGIES_V3).split(",")
    if name.strip()
)

ER_V3_POLL_INTERVAL_SEC = float(os.getenv("ER_V3_POLL_INTERVAL_SEC", "0.5"))
ER_V3_ENTRY_WINDOW_SEC = int(os.getenv("ER_V3_ENTRY_WINDOW_SEC", "30"))
ER_V3_TRAILING_GRACE_SEC = int(os.getenv("ER_V3_TRAILING_GRACE_SEC", "30"))
ER_V3_TRAILING_STOP_PCT = float(os.getenv("ER_V3_TRAILING_STOP_PCT", "5"))
ER_V3_STOP_LOSS_PCT = float(os.getenv("ER_V3_STOP_LOSS_PCT", "-10"))
ER_V3_TIME_STOP_SEC = int(os.getenv("ER_V3_TIME_STOP_SEC", "90"))
