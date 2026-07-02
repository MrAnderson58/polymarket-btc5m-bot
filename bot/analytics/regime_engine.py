"""Section 35 — extended market regime engine with per-regime PF."""

from __future__ import annotations

from typing import Any

from bot.analytics.intelligence_context import IntelligenceContext
from bot.er_btc_direction_stats import BTC_FLAT_THRESHOLD_USD, _exit_ts
from bot.report.analytics import _metrics, trade_pnl


def _classify_regime(trade: Any, ctx: IntelligenceContext) -> str:
    entry_ts = int(trade["entry_ts"])
    slug = str(trade["market_slug"])
    side = str(trade["side"])

    bid, ask, _dist, _sec = ctx.cache.quote_at_entry(
        market_slug=slug, side=side, entry_ts=entry_ts
    )
    btc_move_30 = ctx.cache.btc_move_at(entry_ts, 30)
    feat = ctx.feature_by_trade_id.get(int(trade["id"]))
    if btc_move_30 is None and feat and feat.get("btc_move_30s") is not None:
        btc_move_30 = float(feat["btc_move_30s"])

    btc_entry = ctx.cache.slug_btc_price_at(slug, entry_ts)
    btc_exit = ctx.cache.slug_btc_price_at(slug, _exit_ts(trade))
    trade_move = (btc_exit - btc_entry) if btc_entry is not None and btc_exit is not None else 0.0

    spread = 0.0
    if bid is not None and ask is not None:
        spread = float(ask) - float(bid)
    elif feat and feat.get("spread") is not None:
        spread = float(feat["spread"])

    if spread > 0.025:
        return "Low Liquidity"

    if btc_move_30 is not None and abs(btc_move_30) > 30:
        return "News Spike"

    if abs(trade_move) >= BTC_FLAT_THRESHOLD_USD * 3:
        return "Strong Uptrend" if trade_move > 0 else "Panic"

    if abs(trade_move) >= BTC_FLAT_THRESHOLD_USD:
        return "Weak Uptrend" if trade_move > 0 else "Expansion"

    vol = ctx.cache.btc_volatility(entry_ts, 60)
    if vol is None and feat and feat.get("volatility_60s") is not None:
        vol = float(feat["volatility_60s"])
    vol = vol or 0.0

    if vol < 5:
        return "Compression"
    if vol > 20:
        return "Expansion"
    if abs(trade_move) < BTC_FLAT_THRESHOLD_USD / 2:
        return "Mean Reversion"
    return "Range"


def build_regime_engine(
    closed: list[Any],
    *,
    ctx: IntelligenceContext,
) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for trade in closed:
        regime = _classify_regime(trade, ctx)
        buckets.setdefault(regime, []).append(trade_pnl(trade))

    regimes = {
        name: _metrics(pnls)
        for name, pnls in sorted(buckets.items(), key=lambda x: -len(x[1]))
    }
    best = (
        max(regimes.items(), key=lambda x: x[1]["profit_factor"] if x[1]["trades"] >= 5 else -1)[0]
        if regimes
        else None
    )
    worst = min(
        (r for r in regimes.items() if r[1]["trades"] >= 5),
        key=lambda x: x[1]["profit_factor"],
        default=(None, {}),
    )[0]

    return {
        "regimes": regimes,
        "best_regime": best,
        "worst_regime": worst,
    }
