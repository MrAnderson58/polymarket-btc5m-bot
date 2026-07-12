"""Phase F.7 Task C — BTC dominance, ETH dominance, TOTAL3 regime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles

REGIME_ALT_SEASON = "ALT SEASON"
REGIME_BTC_ROTATION = "BTC ROTATION"
REGIME_RISK_OFF = "RISK OFF"
REGIME_RISK_ON = "RISK ON"
REGIME_CAPITAL_INTO_BTC = "CAPITAL INTO BTC"


@dataclass(frozen=True)
class DominanceContextF7:
    regime: str
    btc_d_proxy: float
    eth_d_proxy: float
    total3_return: float
    btc_return: float
    eth_return: float
    summary: str


def _return_pct(conn: Any, symbol: str, bars: int = 12) -> float:
    candles = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=bars + 2)
    if len(candles) < 2 or candles[0].close <= 0:
        return 0.0
    return (candles[-1].close / candles[0].close - 1.0) * 100.0


def _relative_strength(btc_ret: float, eth_ret: float, total3_ret: float) -> tuple[float, float]:
    """Proxy BTC.D / ETH.D from relative performance."""
    btc_d = 50.0 + (btc_ret - eth_ret) * 2.5
    eth_d = 50.0 + (eth_ret - total3_ret) * 2.0
    return max(20.0, min(80.0, btc_d)), max(15.0, min(65.0, eth_d))


def classify_dominance(
    conn: Any,
    *,
    shock_symbol: str,
) -> DominanceContextF7:
    btc_ret = _return_pct(conn, "BTC")
    eth_ret = _return_pct(conn, "ETH")
    total3_ret = _return_pct(conn, "TOTAL3")
    btc_d, eth_d = _relative_strength(btc_ret, eth_ret, total3_ret)

    alt_outperform = total3_ret > btc_ret + 0.8
    btc_leads = btc_ret > eth_ret + 0.5 and btc_ret > total3_ret
    risk_off = btc_ret < -1.5 and eth_ret < -1.5 and total3_ret < -2.0
    risk_on = btc_ret > 0.5 and eth_ret > 0.3 and total3_ret > 0.5

    if risk_off:
        regime = REGIME_RISK_OFF
        summary = "Risk-off — капитал уходит из рисковых активов"
    elif alt_outperform and total3_ret > 1.0:
        regime = REGIME_ALT_SEASON
        summary = "Alt season — альты опережают BTC"
    elif btc_leads and btc_ret > 0:
        regime = REGIME_CAPITAL_INTO_BTC
        summary = "Капитал уходит в BTC"
    elif btc_ret > 0 and eth_ret < 0:
        regime = REGIME_BTC_ROTATION
        summary = "Ротация в BTC"
    elif risk_on:
        regime = REGIME_RISK_ON
        summary = "Risk-on — рынок поддерживает риск"
    else:
        regime = REGIME_BTC_ROTATION
        summary = "Смешанный режим доминации"

    sym_upper = shock_symbol.upper().replace("USDT", "")
    if sym_upper not in ("BTC", "ETH") and regime == REGIME_ALT_SEASON:
        summary += f" — благоприятно для {sym_upper}"

    return DominanceContextF7(
        regime=regime,
        btc_d_proxy=round(btc_d, 1),
        eth_d_proxy=round(eth_d, 1),
        total3_return=round(total3_ret, 2),
        btc_return=round(btc_ret, 2),
        eth_return=round(eth_ret, 2),
        summary=summary,
    )
