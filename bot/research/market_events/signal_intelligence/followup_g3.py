"""Phase G.3 — signal follow-up notifications (TP1/TP2/RESULT)."""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.telegram_g3 import (
    format_result_g3,
    format_tp_hit_g3,
)

logger = logging.getLogger(__name__)

STATUS_TP1 = "TP1_HIT"
STATUS_TP2 = "TP2_HIT"
STATUS_CLOSED = "CLOSED"


def _current_price(conn: Any, symbol: str) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if not bars:
        return None
    return float(bars[-1].close)


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _send_followup(conn: Any, *, signal_id: int, followup_type: str, message: str) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    if not alert_shock_enabled():
        return False

    row = conn.execute(
        "SELECT signal_uuid, event_id FROM market_live_signals_g3 WHERE id = ?",
        (signal_id,),
    ).fetchone()
    if not row:
        return False

    dedupe = f"g3-followup-{followup_type}-{row['signal_uuid']}"
    ok = _safe_alert(
        conn,
        event_id=int(row["event_id"] or 0),
        alert_type=ALERT_SHOCK,
        detail=dedupe,
        message=message,
        enabled=True,
    )
    insert_returning_id(
        conn,
        """
        INSERT INTO market_signal_followup_g3 (signal_id, followup_type, telegram_sent, message_text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (signal_id, followup_type, 1 if ok else 0, message, int(time.time())),
    )
    return ok


def check_signal_followups_g3(conn: Any) -> int:
    """Poll active G3 signals for TP hits and closures."""
    rows = conn.execute(
        """
        SELECT id, signal_uuid, symbol, direction, status, entry_price, tp1, tp2, tp3, sl,
               risk_reward, created_at, max_profit_pct, max_drawdown_pct
        FROM market_live_signals_g3
        WHERE status IN ('ACTIVE', 'TP1_HIT')
        ORDER BY created_at ASC
        """,
    ).fetchall()
    if not rows:
        return 0

    updates = 0
    now = int(time.time())
    for r in rows:
        price = _current_price(conn, r["symbol"])
        if not price or not r["entry_price"]:
            continue

        is_long = r["direction"] == "LONG"
        entry = float(r["entry_price"])
        pnl = _pnl_pct(entry, price, is_long=is_long)
        max_profit = max(float(r["max_profit_pct"] or 0), pnl)
        max_dd = min(float(r["max_drawdown_pct"] or 0), pnl)

        status = r["status"]
        tp1 = float(r["tp1"] or 0)
        tp2 = float(r["tp2"] or 0)
        sl = float(r["sl"] or 0)

        hit_tp1 = (is_long and price >= tp1) or (not is_long and price <= tp1)
        hit_tp2 = (is_long and price >= tp2) or (not is_long and price <= tp2)
        hit_sl = (is_long and price <= sl) or (not is_long and price >= sl)

        if status == "ACTIVE" and hit_tp1:
            msg = format_tp_hit_g3(level="TP1", symbol=r["symbol"], signal_uuid=r["signal_uuid"])
            _send_followup(conn, signal_id=int(r["id"]), followup_type="TP1", message=msg)
            conn.execute(
                "UPDATE market_live_signals_g3 SET status = ?, max_profit_pct = ?, max_drawdown_pct = ? WHERE id = ?",
                (STATUS_TP1, max_profit, max_dd, r["id"]),
            )
            updates += 1
            continue

        if status in ("ACTIVE", STATUS_TP1) and hit_tp2:
            msg = format_tp_hit_g3(level="TP2", symbol=r["symbol"], signal_uuid=r["signal_uuid"])
            _send_followup(conn, signal_id=int(r["id"]), followup_type="TP2", message=msg)
            conn.execute(
                "UPDATE market_live_signals_g3 SET status = ?, max_profit_pct = ?, max_drawdown_pct = ? WHERE id = ?",
                (STATUS_TP2, max_profit, max_dd, r["id"]),
            )
            updates += 1

        if hit_sl or (now - int(r["created_at"]) > 86400):
            holding = now - int(r["created_at"])
            msg = format_result_g3(
                symbol=r["symbol"],
                signal_uuid=r["signal_uuid"],
                pnl_pct=pnl,
                risk_reward=float(r["risk_reward"] or 0),
                holding_seconds=holding,
                max_drawdown_pct=abs(max_dd),
                max_profit_pct=max_profit,
            )
            _send_followup(conn, signal_id=int(r["id"]), followup_type="CLOSED", message=msg)
            conn.execute(
                """
                UPDATE market_live_signals_g3
                SET status = ?, closed_at = ?, pnl_pct = ?, holding_seconds = ?,
                    max_profit_pct = ?, max_drawdown_pct = ?
                WHERE id = ?
                """,
                (STATUS_CLOSED, now, pnl, holding, max_profit, max_dd, r["id"]),
            )
            try:
                from bot.research.market_events.signal_intelligence.adaptive_learning_g3 import (
                    record_weight_recommendation_g3,
                )
                record_weight_recommendation_g3(conn, signal_id=int(r["id"]), pnl_pct=pnl)
            except Exception as exc:
                logger.debug("g3 adaptive learning skipped: %s", exc)
            updates += 1

    return updates
