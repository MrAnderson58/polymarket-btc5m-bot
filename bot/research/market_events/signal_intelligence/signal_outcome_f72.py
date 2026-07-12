"""Phase F.7.2 — signal outcome engine: track TP/SL lifecycle."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import F72_ENABLED
from bot.research.market_events.signal_intelligence.trade_plan_f71 import (
    TradePlanF71,
    build_trade_plan_for_event,
)

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "ACTIVE"
STATUS_TP1 = "TP1_HIT"
STATUS_TP2 = "TP2_HIT"
STATUS_CLOSED = "CLOSED"

EXIT_TP1 = "TP1"
EXIT_TP2 = "TP2"
EXIT_TP3 = "TP3"
EXIT_SL = "SL"

_TABLE = "market_events_signal_outcomes_f72"


@dataclass(frozen=True)
class SignalOutcomeF72:
    event_id: int
    symbol: str
    trade_side: str
    status: str
    entry: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    entry_time: int
    position_remaining_pct: float
    signal_score: float
    market_score: float


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _load_pattern_context(conn: Any, event_id: int) -> dict[str, Any]:
    pattern: dict[str, Any] = {}
    trend = conn.execute(
        "SELECT stage, funding, open_interest_delta FROM market_events_trend_shock_v2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if trend:
        pattern["trend_stage"] = trend["stage"]
        pattern["funding"] = trend["funding"]
        pattern["oi_delta"] = trend["open_interest_delta"]
    f2 = conn.execute(
        "SELECT funding_regime, oi_regime FROM market_events_signal_reports_f2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if f2:
        pattern["funding_regime"] = f2["funding_regime"]
        pattern["oi_regime"] = f2["oi_regime"]
    intel = conn.execute(
        "SELECT dominance_regime, long_trend_stage FROM market_events_market_intelligence_f7 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if intel:
        pattern["dominance_regime"] = intel["dominance_regime"]
        pattern["long_trend_stage"] = intel["long_trend_stage"]
    return pattern


def create_active_signal_f72(conn: Any, *, event_id: int) -> SignalOutcomeF72 | None:
    """Create ACTIVE signal record after Telegram send."""
    if not F72_ENABLED:
        return None

    existing = conn.execute(
        f"SELECT 1 FROM {_TABLE} WHERE event_id = ?", (event_id,),
    ).fetchone()
    if existing:
        return load_signal_outcome_f72(conn, event_id)

    row = conn.execute("SELECT symbol, direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    from bot.research.market_events.signal_intelligence.market_intel_f7 import load_market_intelligence_f7
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import load_professional_signal_f5

    f5 = load_professional_signal_f5(conn, event_id)
    f7 = load_market_intelligence_f7(conn, event_id)
    if not f5:
        return None

    symbol = str(row["symbol"])
    shock_dir = str(row["direction"] or "DOWN")
    conf = f7.final_confidence if f7 else f5.dynamic_confidence
    mscore = f7.market_score if f7 else 0.0

    plan = build_trade_plan_for_event(
        conn,
        event_id=event_id,
        symbol=symbol,
        shock_direction=shock_dir,
        risk_reward=f5.risk_reward,
        final_confidence=conf,
    )
    if not plan:
        return None

    from bot.research.market_events.signal_intelligence.trader_performance_f6 import resolve_channel_for_event
    channel = resolve_channel_for_event(conn, event_id)
    pattern = _load_pattern_context(conn, event_id)
    now = int(time.time())

    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_TABLE} (
          event_id, symbol, trade_side, status, entry, tp1, tp2, tp3, sl,
          entry_time, risk_reward, position_remaining_pct, signal_score, market_score,
          author_channel, pattern_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, symbol, plan.trade_side, STATUS_ACTIVE,
            plan.entry, plan.tp1, plan.tp2, plan.tp3, plan.sl,
            now, plan.risk_reward, conf, mscore,
            channel, json.dumps(pattern, ensure_ascii=False), now, now,
        ),
    )
    return load_signal_outcome_f72(conn, event_id)


def load_signal_outcome_f72(conn: Any, event_id: int) -> SignalOutcomeF72 | None:
    row = conn.execute(f"SELECT * FROM {_TABLE} WHERE event_id = ?", (event_id,)).fetchone()
    if not row:
        return None
    return SignalOutcomeF72(
        event_id=int(row["event_id"]),
        symbol=str(row["symbol"]),
        trade_side=str(row["trade_side"]),
        status=str(row["status"]),
        entry=float(row["entry"]),
        tp1=float(row["tp1"]),
        tp2=float(row["tp2"]),
        tp3=float(row["tp3"]),
        sl=float(row["sl"]),
        entry_time=int(row["entry_time"]),
        position_remaining_pct=float(row["position_remaining_pct"]),
        signal_score=float(row["signal_score"]),
        market_score=float(row["market_score"]),
    )


def _resolve_price(feed: Any, symbol: str) -> float | None:
    if feed is None:
        return None
    state = feed.get_state(symbol)
    if state and state.last_price:
        return float(state.last_price)
    return None


def _tp_hit(is_long: bool, price: float, level: float) -> bool:
    return price >= level if is_long else price <= level


def _sl_hit(is_long: bool, price: float, sl: float) -> bool:
    return price <= sl if is_long else price >= sl


def _close_signal(
    conn: Any,
    *,
    row: Any,
    exit_price: float,
    exit_reason: str,
    now: int,
) -> None:
    entry = float(row["entry"])
    is_long = row["trade_side"] == "LONG"
    pnl = _pnl_pct(entry, exit_price, is_long=is_long)
    holding = now - int(row["entry_time"])

    conn.execute(
        f"""
        UPDATE {_TABLE} SET
          status = ?, exit_price = ?, exit_time = ?, exit_reason = ?,
          pnl_pct = ?, holding_seconds = ?, position_remaining_pct = 0,
          last_price = ?, last_check_time = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (
            STATUS_CLOSED, exit_price, now, exit_reason, round(pnl, 3),
            holding, exit_price, now, now, int(row["event_id"]),
        ),
    )

    from bot.research.market_events.signal_intelligence.signal_followup_f72 import (
        send_result_telegram_f72,
    )
    from bot.research.market_events.signal_intelligence.signal_learning_f72 import (
        apply_signal_learning_f72,
    )

    send_result_telegram_f72(conn, event_id=int(row["event_id"]))
    apply_signal_learning_f72(conn, event_id=int(row["event_id"]))


def _update_extremes(conn: Any, row: Any, price: float, now: int) -> None:
    entry = float(row["entry"])
    is_long = row["trade_side"] == "LONG"
    pnl = _pnl_pct(entry, price, is_long=is_long)
    max_dd = max(float(row["max_drawdown_pct"]), -min(0, pnl))
    max_profit = max(float(row["max_profit_pct"]), max(0, pnl))
    conn.execute(
        f"""
        UPDATE {_TABLE} SET max_drawdown_pct = ?, max_profit_pct = ?,
          last_price = ?, last_check_time = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (round(max_dd, 3), round(max_profit, 3), price, now, now, int(row["event_id"])),
    )


def _handle_tp_hit(
    conn: Any,
    *,
    row: Any,
    tp_level: str,
    price: float,
    now: int,
) -> None:
    from bot.research.market_events.signal_intelligence.signal_followup_f72 import (
        send_tp_followup_f72,
    )

    entry = float(row["entry"])
    is_long = row["trade_side"] == "LONG"
    pnl = _pnl_pct(entry, price, is_long=is_long)
    remaining = {"TP1": 75.0, "TP2": 50.0}.get(tp_level, 0.0)
    new_status = {"TP1": STATUS_TP1, "TP2": STATUS_TP2}.get(tp_level, STATUS_CLOSED)
    time_col = {"TP1": "tp1_hit_time", "TP2": "tp2_hit_time", "TP3": "tp3_hit_time"}[tp_level]

    if tp_level == "TP3":
        _close_signal(conn, row=row, exit_price=price, exit_reason=EXIT_TP3, now=now)
        send_tp_followup_f72(
            conn, event_id=int(row["event_id"]), tp_level=tp_level,
            pnl_pct=pnl, holding_sec=now - int(row["entry_time"]), remaining_pct=0,
        )
        return

    conn.execute(
        f"""
        UPDATE {_TABLE} SET status = ?, position_remaining_pct = ?,
          {time_col} = ?, last_price = ?, last_check_time = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (new_status, remaining, now, price, now, now, int(row["event_id"])),
    )
    send_tp_followup_f72(
        conn, event_id=int(row["event_id"]), tp_level=tp_level,
        pnl_pct=pnl, holding_sec=now - int(row["entry_time"]), remaining_pct=remaining,
    )


def tick_active_signals_f72(
    conn: Any,
    *,
    feed: Any,
    now: int | None = None,
    prices: dict[str, float] | None = None,
) -> int:
    """Check all ACTIVE signals against live prices. Returns number ticked."""
    if not F72_ENABLED:
        return 0

    now = now or int(time.time())
    rows = conn.execute(
        f"SELECT * FROM {_TABLE} WHERE status != ?",
        (STATUS_CLOSED,),
    ).fetchall()
    ticked = 0

    for row in rows:
        symbol = str(row["symbol"])
        price = (prices or {}).get(symbol) or _resolve_price(feed, symbol)
        if price is None:
            continue
        ticked += 1
        _update_extremes(conn, row, price, now)

        is_long = row["trade_side"] == "LONG"
        status = str(row["status"])

        if _sl_hit(is_long, price, float(row["sl"])):
            from bot.research.market_events.signal_intelligence.signal_followup_f72 import (
                send_sl_followup_f72,
            )
            send_sl_followup_f72(conn, event_id=int(row["event_id"]), price=price)
            _close_signal(conn, row=row, exit_price=price, exit_reason=EXIT_SL, now=now)
            continue

        levels = [
            (EXIT_TP3, float(row["tp3"]), status in (STATUS_ACTIVE, STATUS_TP1, STATUS_TP2)),
            (EXIT_TP2, float(row["tp2"]), status in (STATUS_ACTIVE, STATUS_TP1)),
            (EXIT_TP1, float(row["tp1"]), status == STATUS_ACTIVE),
        ]
        for tp_name, level, allowed in levels:
            if allowed and _tp_hit(is_long, price, level):
                _handle_tp_hit(conn, row=row, tp_level=tp_name, price=price, now=now)
                break

    return ticked
