"""HTF and alignment context labels."""

from __future__ import annotations

from bot.research.mtf.config import HTF_MILD_THRESHOLD, HTF_STRONG_THRESHOLD
from bot.research.mtf.models import BtcSpotContext, PolymarketTfContext, TradeContext


def htf_label_from_btc(btc: BtcSpotContext) -> str:
    r1h = btc.return_1h
    r15 = btc.return_15m
    if r1h is None:
        return "HTF_MIXED"

    if r1h >= HTF_STRONG_THRESHOLD and (r15 is None or r15 >= HTF_MILD_THRESHOLD):
        return "HTF_STRONG_UP"
    if r1h <= -HTF_STRONG_THRESHOLD and (r15 is None or r15 <= -HTF_MILD_THRESHOLD):
        return "HTF_STRONG_DOWN"
    if r1h >= HTF_MILD_THRESHOLD:
        return "HTF_UP"
    if r1h <= -HTF_MILD_THRESHOLD:
        return "HTF_DOWN"
    return "HTF_MIXED"


def _pm_direction(pm: PolymarketTfContext) -> str | None:
    if not pm.available:
        return None
    if pm.prob_direction:
        return pm.prob_direction
    if pm.prob_yes is not None:
        if pm.prob_yes >= 0.55:
            return "UP"
        if pm.prob_yes <= 0.45:
            return "DOWN"
    return "NEUTRAL"


def alignment_label(
    side: str,
    btc: BtcSpotContext,
    pm_15m: PolymarketTfContext,
    pm_1h: PolymarketTfContext,
    pm_daily: PolymarketTfContext,
) -> str:
    htf = htf_label_from_btc(btc)
    d15 = _pm_direction(pm_15m)
    d1h = _pm_direction(pm_1h)
    dd = _pm_direction(pm_daily)

    btc_up = htf in ("HTF_UP", "HTF_STRONG_UP")
    btc_down = htf in ("HTF_DOWN", "HTF_STRONG_DOWN")

    if side == "YES":
        if btc_up and d15 == "UP" and d1h == "UP":
            return "FULL_ALIGNMENT_YES"
        if btc_down and (d15 == "DOWN" or d1h == "DOWN"):
            return "5M_COUNTERTREND_YES"
        if d15 == "UP" and d1h == "DOWN":
            return "15M_REVERSAL"
        if d15 == "DOWN" and d1h == "UP":
            return "1H_REVERSAL"
        if dd == "DOWN" and d1h == "DOWN" and d15 == "UP":
            return "FULL_ALIGNMENT_YES"  # reversal setup hypothesis C
    else:  # NO
        if btc_down and d15 == "DOWN" and d1h == "DOWN":
            return "FULL_ALIGNMENT_NO"
        if btc_up and (d15 == "UP" or d1h == "UP"):
            return "5M_COUNTERTREND_NO"
        if d15 == "DOWN" and d1h == "UP":
            return "15M_REVERSAL"
        if d15 == "UP" and d1h == "DOWN":
            return "1H_REVERSAL"

    # Divergence: BTC down but PM 15m YES prob rising
    if btc_down and d15 == "UP":
        return "CONFLICT"
    if btc_up and d15 == "DOWN":
        return "CONFLICT"

    if btc_up and side == "YES":
        return "FULL_ALIGNMENT_YES"
    if btc_down and side == "NO":
        return "FULL_ALIGNMENT_NO"

    return "NEUTRAL"


def label_trade_context(ctx: TradeContext) -> TradeContext:
    ctx.htf_label = htf_label_from_btc(ctx.btc)
    ctx.alignment_label = alignment_label(
        ctx.side, ctx.btc, ctx.pm_15m, ctx.pm_1h, ctx.pm_daily,
    )
    return ctx
