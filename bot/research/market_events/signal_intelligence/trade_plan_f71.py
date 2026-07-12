"""Phase F.7.1 — concrete trade plan with prices and position sizing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.risk_reward_f5 import RiskRewardF5


@dataclass(frozen=True)
class TradePlanF71:
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    position_size_pct: float
    trade_side: str


def resolve_event_price(conn: Any, *, event_id: int, symbol: str) -> float | None:
    snap = conn.execute(
        """
        SELECT price FROM market_event_snapshots
        WHERE event_id = ? AND price IS NOT NULL
        ORDER BY offset_seconds ASC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if snap and snap["price"]:
        return float(snap["price"])

    ctx = conn.execute(
        """
        SELECT raw_json FROM market_event_exchange_context
        WHERE event_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if ctx and ctx["raw_json"]:
        try:
            raw = json.loads(ctx["raw_json"])
            lp = (raw.get("metrics") or {}).get("last_price")
            if lp:
                return float(lp)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=3)
    if bars:
        return float(bars[-1].close)
    return None


def compute_position_size_pct(*, final_confidence: float, risk_reward: float) -> float:
    if final_confidence >= 9.0:
        base = 5.0
    elif final_confidence >= 8.5:
        base = 4.5
    elif final_confidence >= 8.0:
        base = 4.0
    elif final_confidence >= 7.5:
        base = 3.0
    else:
        base = 2.0
    bonus = min(2.0, risk_reward / 5.0)
    return round(min(8.0, base + bonus), 1)


def compute_trade_plan_f71(
    *,
    price: float,
    shock_direction: str,
    risk_reward: RiskRewardF5,
    final_confidence: float,
) -> TradePlanF71:
    """Shock DOWN → LONG reversal; shock UP → SHORT fade."""
    is_long = shock_direction == "DOWN"
    trade_side = "LONG" if is_long else "SHORT"

    if is_long:
        entry = price
        sl = price * (1.0 - risk_reward.stop_pct / 100.0)
        tp1 = price * (1.0 + risk_reward.tp1_pct / 100.0)
        tp2 = price * (1.0 + risk_reward.tp2_pct / 100.0)
        tp3 = price * (1.0 + risk_reward.tp3_pct / 100.0)
    else:
        entry = price
        sl = price * (1.0 + risk_reward.stop_pct / 100.0)
        tp1 = price * (1.0 - risk_reward.tp1_pct / 100.0)
        tp2 = price * (1.0 - risk_reward.tp2_pct / 100.0)
        tp3 = price * (1.0 - risk_reward.tp3_pct / 100.0)

    pos_pct = compute_position_size_pct(
        final_confidence=final_confidence,
        risk_reward=risk_reward.risk_reward,
    )
    return TradePlanF71(
        entry=round(entry, 4),
        sl=round(sl, 4),
        tp1=round(tp1, 4),
        tp2=round(tp2, 4),
        tp3=round(tp3, 4),
        risk_reward=risk_reward.risk_reward,
        position_size_pct=pos_pct,
        trade_side=trade_side,
    )


def build_trade_plan_for_event(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    shock_direction: str,
    risk_reward: RiskRewardF5,
    final_confidence: float,
) -> TradePlanF71 | None:
    price = resolve_event_price(conn, event_id=event_id, symbol=symbol)
    if not price or price <= 0:
        return None
    return compute_trade_plan_f71(
        price=price,
        shock_direction=shock_direction,
        risk_reward=risk_reward,
        final_confidence=final_confidence,
    )
