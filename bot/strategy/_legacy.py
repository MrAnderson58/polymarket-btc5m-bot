"""BTC 5m late-window momentum strategy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.config import STRATEGY_WINDOW_SEC, STRIKE_THRESHOLD_USD


class SignalSide(str, Enum):
    YES = "BUY_YES"
    NO = "BUY_NO"


@dataclass(frozen=True)
class StrategySignal:
    side: SignalSide
    btc_price: float
    strike_price: float
    delta_usd: float
    seconds_remaining: float


def evaluate(
    *,
    seconds_remaining: float,
    btc_price: float,
    strike_price: float,
) -> StrategySignal | None:
    """
    Evaluate strategy in the final window before market close.

    - <= 45 seconds remaining
    - BTC >= strike + $150 -> BUY YES
    - BTC <= strike - $150 -> BUY NO
    """
    if seconds_remaining > STRATEGY_WINDOW_SEC or seconds_remaining <= 0:
        return None

    delta = btc_price - strike_price

    if delta >= STRIKE_THRESHOLD_USD:
        return StrategySignal(
            side=SignalSide.YES,
            btc_price=btc_price,
            strike_price=strike_price,
            delta_usd=delta,
            seconds_remaining=seconds_remaining,
        )

    if delta <= -STRIKE_THRESHOLD_USD:
        return StrategySignal(
            side=SignalSide.NO,
            btc_price=btc_price,
            strike_price=strike_price,
            delta_usd=delta,
            seconds_remaining=seconds_remaining,
        )

    return None
