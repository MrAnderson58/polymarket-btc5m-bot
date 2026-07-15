"""S2.0 Market Agent — market-data only, no Claude."""

from __future__ import annotations

import logging
from typing import Any

from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    SymbolMarketDataG01,
    fetch_symbol_market_data_g01,
)
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5
from bot.research.market_events.signal_intelligence.trade_geometry_s12 import (
    shock_direction_for_trade_side,
    validate_trade_geometry_s12,
)
from bot.research.market_events.signal_intelligence.trade_plan_f71 import compute_trade_plan_f71

logger = logging.getLogger(__name__)


def _atr_pct(bars: list[Any], period: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    window = bars[-(period + 1) :] if len(bars) > period else bars
    trs: list[float] = []
    for i in range(1, len(window)):
        h = float(window[i].high)
        l = float(window[i].low)
        pc = float(window[i - 1].close)
        tr = max(h - l, abs(h - pc), abs(l - pc))
        if pc > 0:
            trs.append(tr / pc * 100.0)
    if not trs:
        return None
    return round(sum(trs) / len(trs), 4)


def _trend_label(bars: list[Any]) -> tuple[str, float]:
    """Return (UP|DOWN|FLAT, strength 0..1) from recent closes."""
    if len(bars) < 5:
        return "FLAT", 0.0
    closes = [float(b.close) for b in bars[-12:]]
    first = closes[0]
    last = closes[-1]
    if first <= 0:
        return "FLAT", 0.0
    chg = (last - first) / first * 100.0
    ups = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    consistency = ups / (len(closes) - 1)
    strength = min(1.0, abs(chg) / 2.5)
    if chg > 0.35 and consistency >= 0.55:
        return "UP", round(max(strength, consistency - 0.3), 3)
    if chg < -0.35 and consistency <= 0.45:
        return "DOWN", round(max(strength, (1.0 - consistency) - 0.3), 3)
    return "FLAT", round(strength * 0.4, 3)


def _liquidations_proxy(conn: Any, symbol: str, funding: float | None) -> float | None:
    _ = symbol  # snapshots are book-level; symbol kept for future per-asset liq feeds
    try:
        row = conn.execute(
            """
            SELECT liquidations FROM market_snapshots_g3
            WHERE liquidations IS NOT NULL
            ORDER BY snapshot_ts DESC LIMIT 1
            """,
        ).fetchone()
        if row and row["liquidations"] is not None:
            return float(row["liquidations"])
    except Exception:
        pass
    if funding is not None:
        return round(abs(funding) * 1e6, 2)
    return None


def _default_rr(*, atr_pct: float | None, confidence: float) -> RiskRewardF5:
    stop = max(0.8, (atr_pct or 1.2) * 0.9)
    tp1 = max(1.0, stop * 1.1)
    tp2 = max(1.6, stop * 2.0)
    tp3 = max(2.2, stop * 2.8)
    rr = round(tp2 / stop, 2) if stop else 2.0
    return RiskRewardF5(
        risk_reward=rr,
        tp1_prob=55.0,
        tp2_prob=35.0,
        tp3_prob=20.0,
        tp1_pct=round(tp1, 2),
        tp2_pct=round(tp2, 2),
        tp3_pct=round(tp3, 2),
        stop_pct=round(stop, 2),
    )


def run_market_agent_s20(
    conn: Any,
    symbol: str,
    *,
    market_data: SymbolMarketDataG01 | None = None,
) -> dict[str, Any]:
    """Produce a market-only trade signal JSON (no Claude)."""
    sym = symbol.upper().replace("USDT", "").strip() or "BTC"
    data = market_data or fetch_symbol_market_data_g01(
        conn, sym, limit=60, persist_state=False,
    )
    bars = list(data.bars or [])
    price = float(data.price) if data.price else (float(bars[-1].close) if bars else None)
    funding = data.funding
    oi = data.open_interest
    volume = data.volume_24h
    if volume is None and bars:
        volume = sum(float(b.volume) for b in bars[-12:])
    atr = _atr_pct(bars)
    trend, trend_strength = _trend_label(bars)
    liq = _liquidations_proxy(conn, sym, funding)

    reasons: list[str] = []
    score = 0.0  # positive → LONG, negative → SHORT

    if trend == "UP":
        score += 1.2 + trend_strength
        reasons.append(f"Trend UP (strength {trend_strength:.2f}) on {data.source or 'market'} candles")
    elif trend == "DOWN":
        score -= 1.2 + trend_strength
        reasons.append(f"Trend DOWN (strength {trend_strength:.2f}) on {data.source or 'market'} candles")
    else:
        reasons.append("Trend FLAT — weak directional bias from price structure")

    if funding is not None:
        if funding > 0.0002:
            score -= 0.55
            reasons.append(f"Funding elevated (+{funding:.6f}) — crowded longs, fade bias")
        elif funding < -0.0002:
            score += 0.55
            reasons.append(f"Funding negative ({funding:.6f}) — crowded shorts, bounce bias")
        else:
            reasons.append(f"Funding neutral ({funding:.6f})")

    if oi is not None and bars and len(bars) >= 2:
        # Rising OI with UP trend reinforces; with DOWN reinforces short
        if trend == "UP":
            score += 0.25
            reasons.append(f"Open interest present ({oi:,.0f}) with uptrend — trend continuation lean")
        elif trend == "DOWN":
            score -= 0.25
            reasons.append(f"Open interest present ({oi:,.0f}) with downtrend — trend continuation lean")
        else:
            reasons.append(f"Open interest {oi:,.0f}")

    if volume is not None:
        reasons.append(f"Volume window ≈ {volume:,.0f}")
        if atr is not None and atr > 1.5:
            score *= 1.05
            reasons.append(f"ATR {atr:.2f}% — elevated volatility supports RR width")

    if liq is not None:
        reasons.append(f"Liquidations proxy {liq:,.0f}")
        if liq > 0 and trend == "DOWN":
            score -= 0.15
        elif liq > 0 and trend == "UP":
            score += 0.15

    if price is None or price <= 0:
        return {
            "direction": "FLAT",
            "confidence": 0.0,
            "rr": 0.0,
            "entry": None,
            "stop": None,
            "tp1": None,
            "tp2": None,
            "reasons": reasons + ["No price available — cannot form levels"],
            "meta": {
                "symbol": sym,
                "source": data.source,
                "funding": funding,
                "open_interest": oi,
                "volume": volume,
                "atr_pct": atr,
                "liquidations": liq,
                "trend": trend,
                "errors": list(data.errors or []),
            },
        }

    if score > 0.15:
        direction = "LONG"
    elif score < -0.15:
        direction = "SHORT"
    else:
        # Mild default: use funding / last candle
        if bars and float(bars[-1].close) >= float(bars[-2].close):
            direction = "LONG"
            reasons.append("Near-flat score — last candle green defaults LONG")
        else:
            direction = "SHORT"
            reasons.append("Near-flat score — last candle red defaults SHORT")

    conf = min(0.95, max(0.35, 0.45 + abs(score) * 0.18 + trend_strength * 0.2))
    rr_model = _default_rr(atr_pct=atr, confidence=conf)
    plan = compute_trade_plan_f71(
        price=price,
        shock_direction=shock_direction_for_trade_side(direction),
        risk_reward=rr_model,
        final_confidence=conf * 10.0,
    )
    geom = validate_trade_geometry_s12(
        direction=direction,
        entry=plan.entry,
        sl=plan.sl,
        tp1=plan.tp1,
        tp2=plan.tp2,
    )
    if not geom.ok:
        logger.warning("S2.0 market agent geometry invalid: %s", geom.errors)
        reasons.append(f"Geometry check failed: {'; '.join(geom.errors)}")

    return {
        "direction": direction,
        "confidence": round(conf, 3),
        "rr": round(plan.risk_reward, 2),
        "entry": plan.entry,
        "stop": plan.sl,
        "tp1": plan.tp1,
        "tp2": plan.tp2,
        "reasons": reasons[:8],
        "meta": {
            "symbol": sym,
            "source": data.source,
            "price": price,
            "funding": funding,
            "open_interest": oi,
            "volume": volume,
            "atr_pct": atr,
            "liquidations": liq,
            "trend": trend,
            "trend_strength": trend_strength,
            "geometry_ok": geom.ok,
            "errors": list(data.errors or []),
        },
    }
