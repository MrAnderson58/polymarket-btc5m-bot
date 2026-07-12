"""Phase F.7.2 — Telegram follow-ups for signal outcomes."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.telegram_f3 import format_symbol_usdt

ALERT_SIGNAL_FOLLOWUP = "SIGNAL_FOLLOWUP"


def _send(conn: Any, *, event_id: int, detail: str, message: str) -> bool:
    from bot.research.market_events.market_event_alerts import _safe_alert
    from bot.research.market_events.alert_config import alert_shock_enabled

    return _safe_alert(
        conn,
        event_id=event_id,
        alert_type=ALERT_SIGNAL_FOLLOWUP,
        detail=detail,
        message=message,
        enabled=alert_shock_enabled(),
    )


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} секунд"
    mins = seconds // 60
    if mins < 60:
        return f"{mins} минут"
    return f"{mins // 60}ч {mins % 60}м"


def send_tp_followup_f72(
    conn: Any,
    *,
    event_id: int,
    tp_level: str,
    pnl_pct: float,
    holding_sec: int,
    remaining_pct: float,
) -> bool:
    row = conn.execute(
        "SELECT symbol FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return False
    sym = format_symbol_usdt(str(row["symbol"]))
    sign = "+" if pnl_pct >= 0 else ""
    lines = [
        f"✅ {sym}",
        "",
        f"{tp_level} достигнут",
        "",
        f"{sign}{pnl_pct:.1f}%",
        "",
        "Время",
        "",
        _format_duration(holding_sec),
    ]
    if remaining_pct > 0:
        lines.extend(["", "Остаток позиции", "", f"{remaining_pct:.0f}%"])
    return _send(conn, event_id=event_id, detail=tp_level.lower(), message="\n".join(lines))


def send_sl_followup_f72(conn: Any, *, event_id: int, price: float) -> bool:
    row = conn.execute(
        """
        SELECT o.symbol, o.entry, o.entry_time, o.trade_side, e.direction
        FROM market_events_signal_outcomes_f72 o
        JOIN market_events e ON e.id = o.event_id
        WHERE o.event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return False

    entry = float(row["entry"])
    is_long = row["trade_side"] == "LONG"
    pnl = ((price / entry - 1.0) if is_long else (1.0 - price / entry)) * 100.0
    sym = format_symbol_usdt(str(row["symbol"]))
    holding = int(__import__("time").time()) - int(row["entry_time"])

    reason = "Trend продолжился"
    direction = str(row["direction"] or "")
    if direction == "DOWN" and is_long:
        reason = "Trend продолжился вниз"
    elif direction == "UP" and not is_long:
        reason = "Trend продолжился вверх"

    msg = "\n".join([
        f"❌ {sym}",
        "",
        "Stop Loss",
        "",
        f"{pnl:.1f}%",
        "",
        "Причина",
        "",
        reason,
        "",
        "Время",
        _format_duration(holding),
    ])
    return _send(conn, event_id=event_id, detail="sl", message=msg)


def send_result_telegram_f72(conn: Any, *, event_id: int) -> bool:
    row = conn.execute(
        "SELECT * FROM market_events_signal_outcomes_f72 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return False

    sym = format_symbol_usdt(str(row["symbol"]))
    author = row["author_channel"] or "—"
    msg = "\n".join([
        "RESULT",
        "",
        f"{sym}",
        "",
        "Entry",
        f"{float(row['entry']):.4f}",
        "",
        "Exit",
        f"{float(row['exit_price'] or 0):.4f}",
        "",
        "PnL",
        f"{float(row['pnl_pct'] or 0):+.2f}%",
        "",
        "Holding time",
        _format_duration(int(row["holding_seconds"] or 0)),
        "",
        "Max Drawdown",
        f"{float(row['max_drawdown_pct']):.2f}%",
        "",
        "Max Profit",
        f"{float(row['max_profit_pct']):+.2f}%",
        "",
        "RR",
        f"{float(row['risk_reward']):.1f}",
        "",
        "Author",
        str(author),
        "",
        "Signal Score",
        f"{float(row['signal_score']):.1f}",
        "",
        "Market Score",
        f"{int(round(float(row['market_score'])))}",
    ])
    return _send(conn, event_id=event_id, detail="result", message=msg)
