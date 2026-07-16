"""Phase S4.2 — Paper Performance Tracker (observe-only, on top of S4.0/S4.1).

Virtual account: $100 start, $100 margin per trade, 20x leverage ($2,000 notional).
Does not modify Decision Engine, G3, or production trading paths.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection, market_events_readonly_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import load_recent_candles

logger = logging.getLogger(__name__)

INITIAL_CAPITAL_USD = 100.0
CAPITAL_PER_TRADE_USD = 100.0
LEVERAGE = 20
POSITION_NOTIONAL_USD = CAPITAL_PER_TRADE_USD * LEVERAGE
TIMEOUT_SECONDS = 86400
DAILY_REPORT_HOUR = 22

STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"

EXIT_TP1 = "TP1"
EXIT_TP2 = "TP2"
EXIT_STOP = "STOP"
EXIT_TIMEOUT = "TIMEOUT"

_TRADES = "market_events_paper_trades_s42"
_ACCOUNT = "market_events_paper_account_s42"
_REPORTS = "market_events_paper_reports_s42"
_OPS = "market_events_paper_ops_state_s42"


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def _margin_pnl_usd(price_pnl_pct: float) -> float:
    return round(CAPITAL_PER_TRADE_USD * (price_pnl_pct / 100.0) * LEVERAGE, 4)


def _result_from_pnl(price_pnl_pct: float) -> str:
    if price_pnl_pct > 0:
        return "WIN"
    if price_pnl_pct < 0:
        return "LOSS"
    return "BE"


def _current_price(conn: Any, symbol: str) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if not bars:
        return None
    return float(bars[-1].close)


def _tp_hit(is_long: bool, price: float, level: float) -> bool:
    return price >= level if is_long else price <= level


def _sl_hit(is_long: bool, price: float, sl: float) -> bool:
    return price <= sl if is_long else price >= sl


def _day_start_local(ts: int | None = None) -> int:
    ts = ts or int(time.time())
    dt = datetime.fromtimestamp(ts)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _week_start_local(ts: int | None = None) -> int:
    ts = ts or int(time.time())
    dt = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(dt.timestamp()) - dt.weekday() * 86400


def _pattern_label(pattern_json: Any) -> str | None:
    if not pattern_json:
        return None
    try:
        data = json.loads(pattern_json) if isinstance(pattern_json, str) else pattern_json
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("pattern", "name", "pattern_name", "label"):
        val = data.get(key)
        if val:
            return str(val)
    return None


def _ensure_account(conn: Any) -> float:
    row = conn.execute(f"SELECT current_equity FROM {_ACCOUNT} WHERE id = 1").fetchone()
    now = int(time.time())
    if row:
        return float(row["current_equity"])
    execute_with_retry(
        conn,
        f"""
        INSERT INTO {_ACCOUNT} (id, initial_capital, current_equity, updated_at)
        VALUES (1, ?, ?, ?)
        """,
        (INITIAL_CAPITAL_USD, INITIAL_CAPITAL_USD, now),
    )
    return INITIAL_CAPITAL_USD


def _get_ops(conn: Any, key: str) -> str | None:
    row = conn.execute(f"SELECT value FROM {_OPS} WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _set_ops(conn: Any, key: str, value: str) -> None:
    now = int(time.time())
    execute_with_retry(
        conn,
        f"INSERT OR REPLACE INTO {_OPS} (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, now),
    )


def open_paper_trades_from_s40(conn: Any, *, limit: int = 100) -> int:
    """Open paper trades for new S4.0 learning signals (observe-only)."""
    rows = conn.execute(
        """
        SELECT s.signal_type, s.signal_id, s.symbol, s.direction,
               s.entry, s.stop, s.tp1, s.tp2, s.timestamp,
               s.snapshot_decision_confidence, s.snapshot_pattern_json, s.snapshot_news_impact
        FROM market_events_signal_learning_s40_signals s
        LEFT JOIN market_events_paper_trades_s42 p
          ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
        WHERE p.id IS NULL
          AND s.entry IS NOT NULL AND s.entry > 0
          AND s.direction IN ('LONG', 'SHORT')
        ORDER BY s.timestamp ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    opened = 0
    now = int(time.time())
    for r in rows:
        execute_with_retry(
            conn,
            f"""
            INSERT OR IGNORE INTO {_TRADES} (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, decision_confidence, pattern_json, news_category,
              capital_usd, leverage, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(r["signal_type"]),
                int(r["signal_id"]),
                str(r["symbol"]),
                str(r["direction"]),
                float(r["entry"]),
                _safe_float(r["stop"]),
                _safe_float(r["tp1"]),
                _safe_float(r["tp2"]),
                int(r["timestamp"] or now),
                STATUS_OPEN,
                _safe_float(r["snapshot_decision_confidence"]),
                r["snapshot_pattern_json"],
                r["snapshot_news_impact"],
                CAPITAL_PER_TRADE_USD,
                LEVERAGE,
                now,
            ),
        )
        opened += 1
    return opened


def _close_trade(
    conn: Any,
    *,
    row: Any,
    exit_price: float,
    exit_reason: str,
    now: int,
) -> None:
    entry = float(row["entry"])
    is_long = str(row["direction"]).upper() == "LONG"
    price_pnl = _pnl_pct(entry, exit_price, is_long=is_long)
    pnl_usd = _margin_pnl_usd(price_pnl)
    holding = now - int(row["created_at"])
    mfe = max(float(row["mfe_pct"] or 0), price_pnl)
    mae = min(float(row["mae_pct"] or 0), price_pnl)

    stop = _safe_float(row["stop"]) or entry
    risk = abs(entry - stop) if abs(entry - stop) > 0 else entry * 0.01
    reward = abs(exit_price - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0

    execute_with_retry(
        conn,
        f"""
        UPDATE {_TRADES} SET
          status = ?, closed_at = ?, holding_seconds = ?,
          mfe_pct = ?, mae_pct = ?, pnl_pct = ?, pnl_usd = ?,
          result = ?, exit_reason = ?, exit_price = ?, rr_achieved = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            STATUS_CLOSED,
            now,
            holding,
            round(mfe, 4),
            round(mae, 4),
            round(price_pnl, 4),
            pnl_usd,
            _result_from_pnl(price_pnl),
            exit_reason,
            exit_price,
            rr,
            now,
            int(row["id"]),
        ),
    )

    equity = _ensure_account(conn)
    new_equity = round(equity + pnl_usd, 4)
    execute_with_retry(
        conn,
        f"UPDATE {_ACCOUNT} SET current_equity = ?, updated_at = ? WHERE id = 1",
        (new_equity, now),
    )


def tick_open_paper_trades_s42(conn: Any) -> int:
    """Update MFE/MAE and close open paper trades."""
    rows = conn.execute(
        f"SELECT * FROM {_TRADES} WHERE status = ? ORDER BY created_at ASC",
        (STATUS_OPEN,),
    ).fetchall()
    if not rows:
        return 0

    now = int(time.time())
    ticked = 0
    for row in rows:
        symbol = str(row["symbol"])
        price = _current_price(conn, symbol)
        if price is None:
            continue
        ticked += 1
        entry = float(row["entry"])
        is_long = str(row["direction"]).upper() == "LONG"
        pnl = _pnl_pct(entry, price, is_long=is_long)
        mfe = max(float(row["mfe_pct"] or 0), pnl)
        mae = min(float(row["mae_pct"] or 0), pnl)
        execute_with_retry(
            conn,
            f"UPDATE {_TRADES} SET mfe_pct = ?, mae_pct = ?, updated_at = ? WHERE id = ?",
            (round(mfe, 4), round(mae, 4), now, int(row["id"])),
        )

        sl = _safe_float(row["stop"])
        tp1 = _safe_float(row["tp1"])
        tp2 = _safe_float(row["tp2"])

        if sl is not None and _sl_hit(is_long, price, sl):
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_STOP, now=now)
            continue
        if tp2 is not None and tp2 > 0 and _tp_hit(is_long, price, tp2):
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_TP2, now=now)
            continue
        if tp1 is not None and tp1 > 0 and _tp_hit(is_long, price, tp1):
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_TP1, now=now)
            continue
        if now - int(row["created_at"]) >= TIMEOUT_SECONDS:
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_TIMEOUT, now=now)

    return ticked


def _aggregate_trades(rows: list[Any]) -> dict[str, Any]:
    if not rows:
        return {
            "signals": 0,
            "win": 0,
            "loss": 0,
            "be": 0,
            "accuracy_pct": 0.0,
            "avg_rr": 0.0,
            "avg_hold_hours": 0.0,
            "paper_pnl_usd": 0.0,
            "avg_confidence": 0.0,
            "long_n": 0,
            "short_n": 0,
            "best_trade": None,
            "worst_trade": None,
            "top_symbols": [],
            "worst_symbols": [],
            "largest_drawdown_pct": 0.0,
        }

    wins = [r for r in rows if str(r["result"]) == "WIN"]
    losses = [r for r in rows if str(r["result"]) == "LOSS"]
    pnls = [float(r["pnl_usd"] or 0) for r in rows]
    pnl_pcts = [float(r["pnl_pct"] or 0) for r in rows]
    rrs = [float(r["rr_achieved"] or 0) for r in rows if r["rr_achieved"] is not None]
    holds = [int(r["holding_seconds"] or 0) for r in rows]
    confs = [float(r["decision_confidence"] or 0) for r in rows if r["decision_confidence"] is not None]

    sym_pnl: dict[str, float] = {}
    pattern_pnl: dict[str, float] = {}
    news_pnl: dict[str, float] = {}
    for r in rows:
        sym = str(r["symbol"])
        sym_pnl[sym] = sym_pnl.get(sym, 0.0) + float(r["pnl_usd"] or 0)
        pat = _pattern_label(r.get("pattern_json"))
        if pat:
            pattern_pnl[pat] = pattern_pnl.get(pat, 0.0) + float(r["pnl_usd"] or 0)
        news = str(r.get("news_category") or "").strip()
        if news:
            news_pnl[news] = news_pnl.get(news, 0.0) + float(r["pnl_usd"] or 0)

    sorted_sym = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    best_row = max(rows, key=lambda r: float(r["pnl_pct"] or 0))
    worst_row = min(rows, key=lambda r: float(r["pnl_pct"] or 0))

    # equity curve drawdown from cumulative pnl
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    return {
        "signals": len(rows),
        "win": len(wins),
        "loss": len(losses),
        "be": len(rows) - len(wins) - len(losses),
        "accuracy_pct": round(100.0 * len(wins) / len(rows), 1) if rows else 0.0,
        "avg_rr": round(sum(rrs) / len(rrs), 2) if rrs else 0.0,
        "avg_hold_hours": round(sum(holds) / len(holds) / 3600.0, 1) if holds else 0.0,
        "paper_pnl_usd": round(sum(pnls), 2),
        "avg_confidence": round(sum(confs) / len(confs), 1) if confs else 0.0,
        "long_n": sum(1 for r in rows if str(r["direction"]).upper() == "LONG"),
        "short_n": sum(1 for r in rows if str(r["direction"]).upper() == "SHORT"),
        "flat_n": 0,
        "best_trade": {
            "symbol": best_row["symbol"],
            "direction": best_row["direction"],
            "pnl_pct": round(float(best_row["pnl_pct"] or 0), 1),
        },
        "worst_trade": {
            "symbol": worst_row["symbol"],
            "direction": worst_row["direction"],
            "pnl_pct": round(float(worst_row["pnl_pct"] or 0), 1),
        },
        "top_symbols": [s for s, _ in sorted_sym[:3]],
        "worst_symbols": [s for s, _ in sorted(sym_pnl.items(), key=lambda x: x[1])[:3]],
        "best_pattern": max(pattern_pnl.items(), key=lambda x: x[1])[0] if pattern_pnl else None,
        "worst_pattern": min(pattern_pnl.items(), key=lambda x: x[1])[0] if pattern_pnl else None,
        "best_news_category": max(news_pnl.items(), key=lambda x: x[1])[0] if news_pnl else None,
        "worst_news_category": min(news_pnl.items(), key=lambda x: x[1])[0] if news_pnl else None,
        "largest_drawdown_pct": round(max_dd / CAPITAL_PER_TRADE_USD * 100, 1) if max_dd else 0.0,
        "pnl_pcts": pnl_pcts,
    }


def _format_daily_report(*, day_key: str, agg: dict[str, Any], equity: float) -> str:
    bt = agg.get("best_trade") or {}
    wt = agg.get("worst_trade") or {}
    lines = [
        "AI PAPER REPORT",
        "",
        "Дата",
        day_key,
        "",
        "========================",
        "",
        "Signals",
        str(agg["signals"]),
        "",
        "WIN",
        str(agg["win"]),
        "",
        "LOSS",
        str(agg["loss"]),
        "",
        "Accuracy",
        f"{agg['accuracy_pct']:.1f}%",
        "",
        "Average RR",
        f"{agg['avg_rr']:.2f}",
        "",
        "Average Hold",
        f"{agg['avg_hold_hours']:.1f}h",
        "",
        "Capital per trade",
        f"${CAPITAL_PER_TRADE_USD:.0f}",
        "",
        "Leverage",
        f"{LEVERAGE}x",
        "",
        "Today's Paper PnL",
        f"{agg['paper_pnl_usd']:+.2f}",
        "",
        "Paper Account",
        f"${equity:,.2f}",
        "",
        "Best Trade",
        f"{bt.get('symbol', '—')} {bt.get('direction', '')}".strip(),
        f"{bt.get('pnl_pct', 0):+.1f}%",
        "",
        "Worst Trade",
        f"{wt.get('symbol', '—')} {wt.get('direction', '')}".strip(),
        f"{wt.get('pnl_pct', 0):+.1f}%",
        "",
        "Top Symbols",
        "\n".join(agg.get("top_symbols") or ["—"]),
        "",
        "Worst Symbols",
        "\n".join(agg.get("worst_symbols") or ["—"]),
        "",
        "Largest Drawdown",
        f"{agg.get('largest_drawdown_pct', 0):.1f}%",
        "",
        "Average Confidence",
        f"{agg.get('avg_confidence', 0):.1f}",
        "",
        "Decision Distribution",
        f"LONG {agg.get('long_n', 0)}",
        f"SHORT {agg.get('short_n', 0)}",
        f"FLAT {agg.get('flat_n', 0)}",
    ]
    return "\n".join(lines)


def _format_weekly_report(*, week_key: str, agg: dict[str, Any], net_profit: float) -> str:
    best_sym = (agg.get("top_symbols") or ["—"])[0]
    worst_sym = (agg.get("worst_symbols") or ["—"])[0]
    lines = [
        "Weekly Paper Performance",
        "",
        week_key,
        "",
        "Trades",
        str(agg["signals"]),
        "",
        "Winrate",
        f"{agg['accuracy_pct']:.1f}%",
        "",
        "Net Paper Profit",
        f"${net_profit:+.0f}",
        "",
        "Best Coin",
        str(best_sym),
        "",
        "Worst Coin",
        str(worst_sym),
        "",
        "Best Pattern",
        str(agg.get("best_pattern") or "—"),
        "",
        "Worst Pattern",
        str(agg.get("worst_pattern") or "—"),
        "",
        "Best News Category",
        str(agg.get("best_news_category") or "—"),
        "",
        "Worst News Category",
        str(agg.get("worst_news_category") or "—"),
        "",
        "Average Hold",
        f"{agg['avg_hold_hours']:.1f}h",
    ]
    return "\n".join(lines)


def _store_report(conn: Any, *, report_type: str, period_key: str, body: str) -> None:
    now = int(time.time())
    execute_with_retry(
        conn,
        f"""
        INSERT OR REPLACE INTO {_REPORTS} (report_type, period_key, body_text, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (report_type, period_key, body, now),
    )


def maybe_emit_scheduled_reports_s42(conn: Any) -> dict[str, Any]:
    """Daily at 22:00 local; weekly on Sunday."""
    now = int(time.time())
    dt = datetime.fromtimestamp(now)
    emitted: dict[str, Any] = {"daily": False, "weekly": False}

    day_key = dt.strftime("%Y-%m-%d")
    if dt.hour >= DAILY_REPORT_HOUR and _get_ops(conn, "last_daily_report") != day_key:
        day_start = _day_start_local(now)
        rows = conn.execute(
            f"""
            SELECT * FROM {_TRADES}
            WHERE status = ? AND closed_at IS NOT NULL AND closed_at >= ?
            """,
            (STATUS_CLOSED, day_start),
        ).fetchall()
        agg = _aggregate_trades([dict(r) for r in rows])
        equity = _ensure_account(conn)
        body = _format_daily_report(day_key=day_key, agg=agg, equity=equity)
        _store_report(conn, report_type="daily", period_key=day_key, body=body)
        _set_ops(conn, "last_daily_report", day_key)
        emitted["daily"] = True
        emitted["daily_report"] = body

    if dt.weekday() == 6:  # Sunday
        week_key = dt.strftime("%Y-W%W")
        if _get_ops(conn, "last_weekly_report") != week_key:
            week_start = _week_start_local(now)
            rows = conn.execute(
                f"""
                SELECT * FROM {_TRADES}
                WHERE status = ? AND closed_at IS NOT NULL AND closed_at >= ?
                """,
                (STATUS_CLOSED, week_start),
            ).fetchall()
            agg = _aggregate_trades([dict(r) for r in rows])
            body = _format_weekly_report(
                week_key=week_key,
                agg=agg,
                net_profit=agg["paper_pnl_usd"],
            )
            _store_report(conn, report_type="weekly", period_key=week_key, body=body)
            _set_ops(conn, "last_weekly_report", week_key)
            emitted["weekly"] = True
            emitted["weekly_report"] = body

    return emitted


def run_paper_performance_cycle_s42() -> dict[str, Any]:
    """Worker hook: open, tick, scheduled reports — short write transactions."""
    with market_events_connection() as conn:
        apply_migrations(conn)
        _ensure_account(conn)
        opened = open_paper_trades_from_s40(conn)
        ticked = tick_open_paper_trades_s42(conn)
        reports = maybe_emit_scheduled_reports_s42(conn)
        conn.commit()
    return {"opened": opened, "ticked": ticked, **reports}


def paper_performance_dashboard_s42(conn: Any) -> dict[str, Any]:
    equity_row = conn.execute(f"SELECT current_equity FROM {_ACCOUNT} WHERE id = 1").fetchone()
    equity = float(equity_row["current_equity"]) if equity_row else INITIAL_CAPITAL_USD
    now = int(time.time())
    day_start = _day_start_local(now)
    week_start = _week_start_local(now)

    all_closed = conn.execute(
        f"SELECT result FROM {_TRADES} WHERE status = ?",
        (STATUS_CLOSED,),
    ).fetchall()
    wins = sum(1 for r in all_closed if str(r["result"]) == "WIN")
    total = len(all_closed)

    today_pnl = conn.execute(
        f"SELECT COALESCE(SUM(pnl_usd), 0) AS s FROM {_TRADES} WHERE status = ? AND closed_at >= ?",
        (STATUS_CLOSED, day_start),
    ).fetchone()["s"]
    week_pnl = conn.execute(
        f"SELECT COALESCE(SUM(pnl_usd), 0) AS s FROM {_TRADES} WHERE status = ? AND closed_at >= ?",
        (STATUS_CLOSED, week_start),
    ).fetchone()["s"]

    return {
        "tab": "Paper Account",
        "current_equity": round(equity, 2),
        "today_pnl_usd": round(float(today_pnl or 0), 2),
        "weekly_pnl_usd": round(float(week_pnl or 0), 2),
        "trades": total,
        "winrate_pct": round(100.0 * wins / total, 1) if total else 0.0,
        "capital_per_trade": CAPITAL_PER_TRADE_USD,
        "leverage": LEVERAGE,
    }


def format_paper_performance_s42(
    conn: Any,
    *,
    symbol: str | None = None,
    today: bool = False,
    week: bool = False,
) -> str:
    equity_row = conn.execute(f"SELECT current_equity FROM {_ACCOUNT} WHERE id = 1").fetchone()
    equity = float(equity_row["current_equity"]) if equity_row else INITIAL_CAPITAL_USD
    now = int(time.time())

    if today:
        period_key = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        cached = conn.execute(
            f"SELECT body_text FROM {_REPORTS} WHERE report_type = 'daily' AND period_key = ?",
            (period_key,),
        ).fetchone()
        if cached and cached["body_text"]:
            return str(cached["body_text"])
        day_start = _day_start_local(now)
        clauses = ["status = ?", "closed_at >= ?"]
        params: list[Any] = [STATUS_CLOSED, day_start]
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        rows = conn.execute(
            f"SELECT * FROM {_TRADES} WHERE {' AND '.join(clauses)} ORDER BY closed_at DESC",
            params,
        ).fetchall()
        agg = _aggregate_trades([dict(r) for r in rows])
        return _format_daily_report(day_key=period_key, agg=agg, equity=equity)

    if week:
        period_key = datetime.fromtimestamp(now).strftime("%Y-W%W")
        cached = conn.execute(
            f"SELECT body_text FROM {_REPORTS} WHERE report_type = 'weekly' AND period_key = ?",
            (period_key,),
        ).fetchone()
        if cached and cached["body_text"]:
            return str(cached["body_text"])
        week_start = _week_start_local(now)
        clauses = ["status = ?", "closed_at >= ?"]
        params = [STATUS_CLOSED, week_start]
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        rows = conn.execute(
            f"SELECT * FROM {_TRADES} WHERE {' AND '.join(clauses)} ORDER BY closed_at DESC",
            params,
        ).fetchall()
        agg = _aggregate_trades([dict(r) for r in rows])
        return _format_weekly_report(week_key=period_key, agg=agg, net_profit=agg["paper_pnl_usd"])

    dash = paper_performance_dashboard_s42(conn)
    lines = [
        "Paper Performance (Observe Only)",
        "",
        f"Current Equity  ${dash['current_equity']:.2f}",
        f"Today's PnL     ${dash['today_pnl_usd']:+.2f}",
        f"Weekly PnL      ${dash['weekly_pnl_usd']:+.2f}",
        f"Trades          {dash['trades']}",
        f"Winrate         {dash['winrate_pct']:.1f}%",
        f"Margin/trade    ${CAPITAL_PER_TRADE_USD:.0f} @ {LEVERAGE}x",
    ]
    if symbol:
        sym = symbol.upper()
        sym_rows = conn.execute(
            f"SELECT * FROM {_TRADES} WHERE symbol = ? AND status = ? ORDER BY closed_at DESC LIMIT 20",
            (sym, STATUS_CLOSED),
        ).fetchall()
        agg = _aggregate_trades([dict(r) for r in sym_rows])
        lines.extend([
            "",
            f"Symbol {sym}",
            f"  Trades {agg['signals']}  WIN {agg['win']}  LOSS {agg['loss']}",
            f"  Accuracy {agg['accuracy_pct']:.1f}%  PnL ${agg['paper_pnl_usd']:+.2f}",
        ])
    return "\n".join(lines)


__all__ = [
    "format_paper_performance_s42",
    "paper_performance_dashboard_s42",
    "run_paper_performance_cycle_s42",
]
