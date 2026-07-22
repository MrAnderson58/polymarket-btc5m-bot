"""Phase S4.2 — Paper Performance Tracker (observe-only, on top of S4.0/S4.1).

Virtual account: $100 start, $100 margin per trade, 20x leverage ($2,000 notional).
Does not modify Decision Engine, G3, or production trading paths.

S54 — trailing-after-TP1 (TRAIL_AFTER_TP1=True by default; set False for Classic).
"""

from __future__ import annotations

import json
import logging
import os
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
EXIT_TRAILING = "TRAILING"

# ===== S54 (production default: trailing after TP1) =====
TRAIL_AFTER_TP1 = True
TRAIL_DISTANCE_MODE = "ENTRY_TO_TP1"
TRAIL_DISTANCE_MULTIPLIER = 1.0
LOG_TRAILING_EVENTS = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_trailing_config_from_env() -> None:
    """Reload S54 flags from environment (tests / ops)."""
    global TRAIL_AFTER_TP1, TRAIL_DISTANCE_MODE, TRAIL_DISTANCE_MULTIPLIER, LOG_TRAILING_EVENTS
    TRAIL_AFTER_TP1 = _env_bool("TRAIL_AFTER_TP1", True)
    TRAIL_DISTANCE_MODE = os.environ.get("TRAIL_DISTANCE_MODE", "ENTRY_TO_TP1").strip() or "ENTRY_TO_TP1"
    try:
        TRAIL_DISTANCE_MULTIPLIER = float(os.environ.get("TRAIL_DISTANCE_MULTIPLIER", "1.0"))
    except (TypeError, ValueError):
        TRAIL_DISTANCE_MULTIPLIER = 1.0
    LOG_TRAILING_EVENTS = _env_bool("LOG_TRAILING_EVENTS", True)


refresh_trailing_config_from_env()

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


def _max_drawdown_pct_from_pnl_usd(
    pnls: list[float],
    *,
    initial_equity: float = INITIAL_CAPITAL_USD,
) -> float:
    """Peak-to-trough drawdown as % of running equity peak (not per-trade margin)."""
    if not pnls:
        return 0.0
    equity = float(initial_equity)
    peak = float(initial_equity)
    max_dd_pct = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, min(100.0, (peak - equity) / peak * 100.0))
        if equity <= 0:
            max_dd_pct = 100.0
            break
    return round(max_dd_pct, 1)


def _current_price(conn: Any, symbol: str) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=2)
    if not bars:
        return None
    return float(bars[-1].close)


def _tp_hit(is_long: bool, price: float, level: float) -> bool:
    return price >= level if is_long else price <= level


def _sl_hit(is_long: bool, price: float, sl: float) -> bool:
    return price <= sl if is_long else price >= sl


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        keys = row.keys() if hasattr(row, "keys") else None
        if keys is not None and key not in keys:
            return default
        val = row[key]
        return default if val is None else val
    except Exception:
        return default


def _log_trailing(event: str, **fields: Any) -> None:
    if not LOG_TRAILING_EVENTS:
        return
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("S54 %s %s", event, extra)


def trail_distance_from_entry_tp1(
    entry: float,
    tp1: float,
    *,
    multiplier: float | None = None,
) -> float:
    """S54 distance = abs(tp1 - entry) * TRAIL_DISTANCE_MULTIPLIER."""
    mult = TRAIL_DISTANCE_MULTIPLIER if multiplier is None else float(multiplier)
    return abs(float(tp1) - float(entry)) * mult


def initial_trailing_stop(
    *,
    is_long: bool,
    entry: float,
    tp1: float,
    multiplier: float | None = None,
) -> tuple[float, float, float]:
    """
    Returns (extreme_price, trailing_stop, distance).
    LONG: extreme=highest=tp1, stop=tp1-distance
    SHORT: extreme=lowest=tp1, stop=tp1+distance
    """
    distance = trail_distance_from_entry_tp1(entry, tp1, multiplier=multiplier)
    if is_long:
        highest = float(tp1)
        return highest, highest - distance, distance
    lowest = float(tp1)
    return lowest, lowest + distance, distance


def ratchet_trailing_stop(
    *,
    is_long: bool,
    price: float,
    distance: float,
    trailing_stop: float,
    highest: float,
    lowest: float,
) -> tuple[float, float, float, bool]:
    """
    Update extremes and ratchet trailing stop (never loosens).
    Returns (highest, lowest, trailing_stop, moved).
    """
    moved = False
    if is_long:
        new_highest = max(highest, price)
        new_stop = new_highest - distance
        # Stop never moves down
        if new_stop > trailing_stop:
            trailing_stop = new_stop
            moved = True
        return new_highest, min(lowest, price) if lowest else price, trailing_stop, moved

    new_lowest = min(lowest, price)
    new_stop = new_lowest + distance
    # Stop never moves up (against short)
    if new_stop < trailing_stop:
        trailing_stop = new_stop
        moved = True
    return max(highest, price) if highest else price, new_lowest, trailing_stop, moved


def simulate_exit_on_path(
    prices: list[float],
    *,
    entry: float,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    direction: str,
    trail_after_tp1: bool,
    multiplier: float = 1.0,
    timeout_bars: int | None = None,
) -> dict[str, Any]:
    """
    Pure A/B simulator over a price path.
    Classic: close on TP1. Experimental: activate trailing after TP1.
    """
    is_long = str(direction).upper() == "LONG"
    trailing_active = False
    trail_stop = 0.0
    distance = 0.0
    highest = entry
    lowest = entry
    extreme_run = 0.0  # max favorable excursion after TP1 (price units beyond tp1)

    for i, price in enumerate(prices):
        if stop is not None and not trailing_active and _sl_hit(is_long, price, float(stop)):
            pnl = _pnl_pct(entry, price, is_long=is_long)
            return {
                "mode": "trailing" if trail_after_tp1 else "classic",
                "exit_reason": EXIT_STOP,
                "exit_price": price,
                "bar": i,
                "pnl_pct": pnl,
                "pnl_usd": _margin_pnl_usd(pnl),
                "trailing_active": False,
                "max_run_after_tp1": 0.0,
            }

        if tp2 is not None and tp2 > 0 and _tp_hit(is_long, price, float(tp2)):
            pnl = _pnl_pct(entry, price, is_long=is_long)
            return {
                "mode": "trailing" if trail_after_tp1 else "classic",
                "exit_reason": EXIT_TP2,
                "exit_price": price,
                "bar": i,
                "pnl_pct": pnl,
                "pnl_usd": _margin_pnl_usd(pnl),
                "trailing_active": trailing_active,
                "max_run_after_tp1": extreme_run,
            }

        if trailing_active:
            highest, lowest, trail_stop, _moved = ratchet_trailing_stop(
                is_long=is_long,
                price=price,
                distance=distance,
                trailing_stop=trail_stop,
                highest=highest,
                lowest=lowest,
            )
            if tp1 is not None:
                if is_long:
                    extreme_run = max(extreme_run, highest - float(tp1))
                else:
                    extreme_run = max(extreme_run, float(tp1) - lowest)
            if _sl_hit(is_long, price, trail_stop):
                pnl = _pnl_pct(entry, price, is_long=is_long)
                tp1_pnl = _pnl_pct(entry, float(tp1 or entry), is_long=is_long)
                return {
                    "mode": "trailing",
                    "exit_reason": EXIT_TRAILING,
                    "exit_price": price,
                    "bar": i,
                    "pnl_pct": pnl,
                    "pnl_usd": _margin_pnl_usd(pnl),
                    "trailing_active": True,
                    "max_run_after_tp1": extreme_run,
                    "additional_pnl_usd": _margin_pnl_usd(pnl) - _margin_pnl_usd(tp1_pnl),
                    "trailing_stop": trail_stop,
                }
        elif tp1 is not None and tp1 > 0 and _tp_hit(is_long, price, float(tp1)):
            if trail_after_tp1:
                extreme, trail_stop, distance = initial_trailing_stop(
                    is_long=is_long, entry=entry, tp1=float(tp1), multiplier=multiplier,
                )
                highest = extreme if is_long else max(entry, price)
                lowest = extreme if not is_long else min(entry, price)
                trailing_active = True
                # Same-bar ratchet if price already beyond TP1
                highest, lowest, trail_stop, _ = ratchet_trailing_stop(
                    is_long=is_long,
                    price=price,
                    distance=distance,
                    trailing_stop=trail_stop,
                    highest=highest,
                    lowest=lowest,
                )
                if is_long:
                    extreme_run = max(0.0, highest - float(tp1))
                else:
                    extreme_run = max(0.0, float(tp1) - lowest)
                if _sl_hit(is_long, price, trail_stop):
                    pnl = _pnl_pct(entry, price, is_long=is_long)
                    tp1_pnl = _pnl_pct(entry, float(tp1), is_long=is_long)
                    return {
                        "mode": "trailing",
                        "exit_reason": EXIT_TRAILING,
                        "exit_price": price,
                        "bar": i,
                        "pnl_pct": pnl,
                        "pnl_usd": _margin_pnl_usd(pnl),
                        "trailing_active": True,
                        "max_run_after_tp1": extreme_run,
                        "additional_pnl_usd": _margin_pnl_usd(pnl) - _margin_pnl_usd(tp1_pnl),
                    }
            else:
                pnl = _pnl_pct(entry, price, is_long=is_long)
                return {
                    "mode": "classic",
                    "exit_reason": EXIT_TP1,
                    "exit_price": price,
                    "bar": i,
                    "pnl_pct": pnl,
                    "pnl_usd": _margin_pnl_usd(pnl),
                    "trailing_active": False,
                    "max_run_after_tp1": 0.0,
                }

        if timeout_bars is not None and i + 1 >= timeout_bars:
            pnl = _pnl_pct(entry, price, is_long=is_long)
            return {
                "mode": "trailing" if trail_after_tp1 else "classic",
                "exit_reason": EXIT_TIMEOUT,
                "exit_price": price,
                "bar": i,
                "pnl_pct": pnl,
                "pnl_usd": _margin_pnl_usd(pnl),
                "trailing_active": trailing_active,
                "max_run_after_tp1": extreme_run,
            }

    last = prices[-1] if prices else entry
    pnl = _pnl_pct(entry, last, is_long=is_long)
    return {
        "mode": "trailing" if trail_after_tp1 else "classic",
        "exit_reason": EXIT_TIMEOUT,
        "exit_price": last,
        "bar": max(0, len(prices) - 1),
        "pnl_pct": pnl,
        "pnl_usd": _margin_pnl_usd(pnl),
        "trailing_active": trailing_active,
        "max_run_after_tp1": extreme_run,
    }


def compare_classic_vs_trailing(
    prices: list[float],
    *,
    entry: float,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    direction: str,
    multiplier: float | None = None,
) -> dict[str, Any]:
    """Run the same path under Classic and Experimental trailing modes."""
    mult = TRAIL_DISTANCE_MULTIPLIER if multiplier is None else float(multiplier)
    classic = simulate_exit_on_path(
        prices,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        direction=direction,
        trail_after_tp1=False,
        multiplier=mult,
    )
    trailing = simulate_exit_on_path(
        prices,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        direction=direction,
        trail_after_tp1=True,
        multiplier=mult,
    )
    return {
        "classic": classic,
        "trailing": trailing,
        "extra_pnl_usd": float(trailing["pnl_usd"]) - float(classic["pnl_usd"]),
    }


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
    trailing_exit_reason: str | None = None,
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
    trail_reason = trailing_exit_reason
    if trail_reason is None and exit_reason == EXIT_TRAILING:
        trail_reason = "trailing_stop"

    execute_with_retry(
        conn,
        f"""
        UPDATE {_TRADES} SET
          status = ?, closed_at = ?, holding_seconds = ?,
          mfe_pct = ?, mae_pct = ?, pnl_pct = ?, pnl_usd = ?,
          result = ?, exit_reason = ?, exit_price = ?, rr_achieved = ?,
          trailing_exit_reason = COALESCE(?, trailing_exit_reason),
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
            trail_reason,
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


def _activate_trailing_after_tp1(
    conn: Any,
    *,
    row: Any,
    price: float,
    now: int,
    is_long: bool,
) -> dict[str, float]:
    """Mark TP1 reached and arm trailing stop; position stays OPEN."""
    entry = float(row["entry"])
    tp1 = float(row["tp1"])
    extreme, trail_stop, distance = initial_trailing_stop(
        is_long=is_long, entry=entry, tp1=tp1,
    )
    highest = extreme if is_long else max(entry, price, extreme)
    lowest = extreme if not is_long else min(entry, price, extreme)
    highest, lowest, trail_stop, moved = ratchet_trailing_stop(
        is_long=is_long,
        price=price,
        distance=distance,
        trailing_stop=trail_stop,
        highest=highest,
        lowest=lowest,
    )
    trade_id = int(row["id"])
    _log_trailing("TP1 reached", trade_id=trade_id, price=price, tp1=tp1)
    _log_trailing("Trailing activated", trade_id=trade_id, distance=distance)
    _log_trailing("Initial trailing stop", trade_id=trade_id, trailing_stop=trail_stop)
    if moved:
        _log_trailing("Trailing stop moved", trade_id=trade_id, trailing_stop=trail_stop, price=price)

    execute_with_retry(
        conn,
        f"""
        UPDATE {_TRADES} SET
          trailing_active = 1,
          trailing_stop = ?,
          highest_price_after_tp1 = ?,
          lowest_price_after_tp1 = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (trail_stop, highest, lowest, now, trade_id),
    )
    return {
        "trailing_stop": trail_stop,
        "highest": highest,
        "lowest": lowest,
        "distance": distance,
    }


def tick_open_paper_trades_s42(conn: Any) -> int:
    """Update MFE/MAE and close open paper trades (Classic or S54 trailing)."""
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
        trailing_active = int(_row_get(row, "trailing_active", 0) or 0) == 1

        # --- S54 trailing management (after TP1) ---
        if trailing_active:
            distance = trail_distance_from_entry_tp1(entry, float(tp1 or entry))
            highest = float(_row_get(row, "highest_price_after_tp1", price) or price)
            lowest = float(_row_get(row, "lowest_price_after_tp1", price) or price)
            trail_stop = float(_row_get(row, "trailing_stop", entry) or entry)
            highest, lowest, new_stop, moved = ratchet_trailing_stop(
                is_long=is_long,
                price=price,
                distance=distance,
                trailing_stop=trail_stop,
                highest=highest,
                lowest=lowest,
            )
            if moved or highest != float(_row_get(row, "highest_price_after_tp1", 0) or 0) \
                    or lowest != float(_row_get(row, "lowest_price_after_tp1", 0) or 0):
                if moved:
                    _log_trailing(
                        "Trailing stop moved",
                        trade_id=int(row["id"]),
                        trailing_stop=new_stop,
                        price=price,
                    )
                execute_with_retry(
                    conn,
                    f"""
                    UPDATE {_TRADES} SET
                      trailing_stop = ?,
                      highest_price_after_tp1 = ?,
                      lowest_price_after_tp1 = ?,
                      updated_at = ?
                    WHERE id = ?
                    """,
                    (new_stop, highest, lowest, now, int(row["id"])),
                )
                trail_stop = new_stop

            if tp2 is not None and tp2 > 0 and _tp_hit(is_long, price, tp2):
                _close_trade(
                    conn, row=row, exit_price=price, exit_reason=EXIT_TP2, now=now,
                    trailing_exit_reason="tp2",
                )
                continue
            if _sl_hit(is_long, price, trail_stop):
                tp1_pnl = _pnl_pct(entry, float(tp1 or entry), is_long=is_long)
                exit_pnl = _pnl_pct(entry, price, is_long=is_long)
                captured = _margin_pnl_usd(exit_pnl) - _margin_pnl_usd(tp1_pnl)
                _log_trailing(
                    "Trailing exit",
                    trade_id=int(row["id"]),
                    price=price,
                    trailing_stop=trail_stop,
                )
                _log_trailing(
                    "Trailing profit captured",
                    trade_id=int(row["id"]),
                    additional_pnl_usd=round(captured, 4),
                )
                _close_trade(
                    conn, row=row, exit_price=price, exit_reason=EXIT_TRAILING, now=now,
                    trailing_exit_reason="trailing_stop",
                )
                continue
            if sl is not None and _sl_hit(is_long, price, sl):
                _close_trade(
                    conn, row=row, exit_price=price, exit_reason=EXIT_STOP, now=now,
                    trailing_exit_reason="emergency_stop",
                )
                continue
            if now - int(row["created_at"]) >= TIMEOUT_SECONDS:
                _close_trade(
                    conn, row=row, exit_price=price, exit_reason=EXIT_TIMEOUT, now=now,
                    trailing_exit_reason="emergency_timeout",
                )
            continue

        # --- Classic path (and TP1 → optional trail arm) ---
        if sl is not None and _sl_hit(is_long, price, sl):
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_STOP, now=now)
            continue
        if tp2 is not None and tp2 > 0 and _tp_hit(is_long, price, tp2):
            _close_trade(conn, row=row, exit_price=price, exit_reason=EXIT_TP2, now=now)
            continue
        if tp1 is not None and tp1 > 0 and _tp_hit(is_long, price, tp1):
            if TRAIL_AFTER_TP1:
                state = _activate_trailing_after_tp1(
                    conn, row=row, price=price, now=now, is_long=is_long,
                )
                # Same tick: exit if already through TP2 or trailing stop
                if tp2 is not None and tp2 > 0 and _tp_hit(is_long, price, tp2):
                    _close_trade(
                        conn, row=row, exit_price=price, exit_reason=EXIT_TP2, now=now,
                        trailing_exit_reason="tp2",
                    )
                elif _sl_hit(is_long, price, float(state["trailing_stop"])):
                    _close_trade(
                        conn, row=row, exit_price=price, exit_reason=EXIT_TRAILING, now=now,
                        trailing_exit_reason="trailing_stop",
                    )
                continue
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
            "best_pattern": None,
            "worst_pattern": None,
            "best_news_category": None,
            "worst_news_category": None,
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

    ordered = sorted(rows, key=lambda r: int(r.get("closed_at") or r.get("created_at") or 0))
    ordered_pnls = [float(r["pnl_usd"] or 0) for r in ordered if r.get("closed_at") is not None]

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
        "largest_drawdown_pct": _max_drawdown_pct_from_pnl_usd(ordered_pnls),
        "pnl_pcts": pnl_pcts,
    }


def paper_trade_counts_s42(conn: Any) -> dict[str, int]:
    """Trade bucket counts for paper-performance / learning-health."""
    open_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status = ?",
        (STATUS_OPEN,),
    ).fetchone()["n"]
    closed_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TRADES} WHERE status = ?",
        (STATUS_CLOSED,),
    ).fetchone()["n"]
    breakeven_n = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM {_TRADES}
        WHERE status = ? AND result = 'BE'
        """,
        (STATUS_CLOSED,),
    ).fetchone()["n"]
    pending_settlement = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_events_signal_learning_s40_signals s
        LEFT JOIN market_events_paper_trades_s42 p
          ON p.s40_signal_type = s.signal_type AND p.s40_signal_id = s.signal_id
        WHERE p.id IS NULL
          AND s.entry IS NOT NULL AND s.entry > 0
          AND s.direction IN ('LONG', 'SHORT')
        """,
    ).fetchone()["n"]
    return {
        "open_trades": int(open_n or 0),
        "closed_trades": int(closed_n or 0),
        "breakeven_trades": int(breakeven_n or 0),
        "pending_settlement": int(pending_settlement or 0),
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


def paper_closed_trades_since_s42(conn: Any, since_ts: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT * FROM {_TRADES}
        WHERE status = ? AND closed_at IS NOT NULL AND closed_at >= ?
        ORDER BY closed_at DESC
        """,
        (STATUS_CLOSED, since_ts),
    ).fetchall()
    return [dict(r) for r in rows]


def paper_day_stats_s42(conn: Any, *, day_start: int | None = None) -> dict[str, Any]:
    """Aggregate closed paper trades for a local calendar day (observe-only)."""
    start = day_start if day_start is not None else _day_start_local()
    rows = paper_closed_trades_since_s42(conn, start)
    # Restrict to this day only (not week).
    end = start + 86400
    day_rows = [r for r in rows if int(r.get("closed_at") or 0) < end]
    agg = _aggregate_trades(day_rows)
    return {
        "day_start": start,
        "signals": agg["signals"],
        "wins": agg["win"],
        "losses": agg["loss"],
        "win_rate": agg["accuracy_pct"],
        "pnl_usd": agg["paper_pnl_usd"],
        "best_symbol": (agg.get("top_symbols") or [None])[0],
        "worst_symbol": (agg.get("worst_symbols") or [None])[0],
        "best_pattern": agg.get("best_pattern"),
        "worst_pattern": agg.get("worst_pattern"),
        "avg_hold_hours": agg["avg_hold_hours"],
        "avg_rr": agg["avg_rr"],
    }


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
        **paper_trade_counts_s42(conn),
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
        f"Open Trades     {dash['open_trades']}",
        f"Closed Trades   {dash['closed_trades']}",
        f"Pending Settlement {dash['pending_settlement']}",
        f"Breakeven Trades {dash['breakeven_trades']}",
        f"Winrate         {dash['winrate_pct']:.1f}%",
        f"Margin/trade    ${CAPITAL_PER_TRADE_USD:.0f} @ {LEVERAGE}x",
        "",
        "S54 Trailing mode",
        f"  TRAIL_AFTER_TP1={TRAIL_AFTER_TP1}  (Classic when False)",
        f"  DISTANCE_MODE={TRAIL_DISTANCE_MODE}  x{TRAIL_DISTANCE_MULTIPLIER}",
    ]
    trail = trailing_stats_s42(conn)
    lines.extend(_format_trailing_stats_block(trail))
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


def trailing_stats_s42(conn: Any) -> dict[str, Any]:
    """Aggregate S54 trailing metrics from closed paper trades."""
    empty = {
        "trailing_exits": 0,
        "avg_trailing_profit_usd": 0.0,
        "avg_additional_profit_after_tp1_usd": 0.0,
        "max_additional_run_after_tp1": 0.0,
        "avg_giveback_after_tp1": 0.0,
        "trailing_stop_hit_pct": 0.0,
        "extra_pnl_generated_usd": 0.0,
        "classic_tp1_exits": 0,
        "armed_trailing_trades": 0,
    }
    try:
        rows = conn.execute(
            f"""
            SELECT entry, direction, tp1, exit_price, exit_reason, pnl_usd,
                   trailing_active, trailing_exit_reason,
                   highest_price_after_tp1, lowest_price_after_tp1
            FROM {_TRADES}
            WHERE status = ?
            """,
            (STATUS_CLOSED,),
        ).fetchall()
    except Exception:
        return empty

    trailing_exits = []
    classic_tp1 = 0
    armed = 0
    extras: list[float] = []
    runs: list[float] = []
    givebacks: list[float] = []

    for r in rows:
        exit_reason = str(r["exit_reason"] or "")
        trail_exit = str(r["trailing_exit_reason"] or "")
        was_armed = int(r["trailing_active"] or 0) == 1 or exit_reason == EXIT_TRAILING
        if was_armed:
            armed += 1
        if exit_reason == EXIT_TP1:
            classic_tp1 += 1

        is_trail_hit = exit_reason == EXIT_TRAILING or trail_exit == "trailing_stop"
        if not is_trail_hit:
            continue

        trailing_exits.append(r)
        entry = float(r["entry"])
        tp1 = _safe_float(r["tp1"]) or entry
        is_long = str(r["direction"]).upper() == "LONG"
        actual = float(r["pnl_usd"] or 0)
        classic_usd = _margin_pnl_usd(_pnl_pct(entry, tp1, is_long=is_long))
        extras.append(actual - classic_usd)
        hi = _safe_float(r["highest_price_after_tp1"])
        lo = _safe_float(r["lowest_price_after_tp1"])
        if is_long and hi is not None:
            run = hi - tp1
            peak_usd = _margin_pnl_usd(_pnl_pct(entry, hi, is_long=True))
        elif (not is_long) and lo is not None:
            run = tp1 - lo
            peak_usd = _margin_pnl_usd(_pnl_pct(entry, lo, is_long=False))
        else:
            run = 0.0
            peak_usd = actual
        runs.append(max(0.0, run))
        givebacks.append(max(0.0, peak_usd - actual))

    n_trail = len(trailing_exits)
    hit_pct = round(100.0 * n_trail / armed, 1) if armed else 0.0
    return {
        "trailing_exits": n_trail,
        "avg_trailing_profit_usd": round(sum(float(r["pnl_usd"] or 0) for r in trailing_exits) / n_trail, 2) if n_trail else 0.0,
        "avg_additional_profit_after_tp1_usd": round(sum(extras) / len(extras), 2) if extras else 0.0,
        "max_additional_run_after_tp1": round(max(runs), 6) if runs else 0.0,
        "avg_giveback_after_tp1": round(sum(givebacks) / len(givebacks), 2) if givebacks else 0.0,
        "trailing_stop_hit_pct": hit_pct,
        "extra_pnl_generated_usd": round(sum(extras), 2) if extras else 0.0,
        "classic_tp1_exits": classic_tp1,
        "armed_trailing_trades": armed,
    }


def _format_trailing_stats_block(stats: dict[str, Any]) -> list[str]:
    return [
        "",
        "Trailing stats (S54)",
        f"  Trailing exits              {stats['trailing_exits']}",
        f"  Classic TP1 exits           {stats['classic_tp1_exits']}",
        f"  Avg trailing profit         ${stats['avg_trailing_profit_usd']:+.2f}",
        f"  Avg additional after TP1    ${stats['avg_additional_profit_after_tp1_usd']:+.2f}",
        f"  Max additional run after TP1 {stats['max_additional_run_after_tp1']}",
        f"  Avg giveback after TP1      ${stats['avg_giveback_after_tp1']:+.2f}",
        f"  Trailing stop hit %         {stats['trailing_stop_hit_pct']:.1f}%",
        f"  Extra PnL from trailing     ${stats['extra_pnl_generated_usd']:+.2f}",
    ]


def format_classic_vs_trailing_ab(
    paths: list[dict[str, Any]],
) -> str:
    """
    Compare Classic vs Trailing on identical synthetic/historical price paths.
    Each path: {prices, entry, stop, tp1, tp2, direction, id?}.
    """
    if not paths:
        return "Classic vs Trailing A/B\n\n(no paths)"
    classic_pnl = 0.0
    trail_pnl = 0.0
    classic_reasons: dict[str, int] = {}
    trail_reasons: dict[str, int] = {}
    lines = [
        "Classic vs Trailing A/B",
        f"Paths: {len(paths)}",
        "",
    ]
    for i, p in enumerate(paths):
        cmp_ = compare_classic_vs_trailing(
            list(p["prices"]),
            entry=float(p["entry"]),
            stop=_safe_float(p.get("stop")),
            tp1=_safe_float(p.get("tp1")),
            tp2=_safe_float(p.get("tp2")),
            direction=str(p.get("direction") or "LONG"),
            multiplier=_safe_float(p.get("multiplier")),
        )
        c, t = cmp_["classic"], cmp_["trailing"]
        classic_pnl += float(c["pnl_usd"])
        trail_pnl += float(t["pnl_usd"])
        classic_reasons[str(c["exit_reason"])] = classic_reasons.get(str(c["exit_reason"]), 0) + 1
        trail_reasons[str(t["exit_reason"])] = trail_reasons.get(str(t["exit_reason"]), 0) + 1
        label = p.get("id") or f"#{i + 1}"
        lines.append(
            f"{label}: Classic {c['exit_reason']} ${c['pnl_usd']:+.2f}  |  "
            f"Trail {t['exit_reason']} ${t['pnl_usd']:+.2f}  "
            f"(Δ ${cmp_['extra_pnl_usd']:+.2f})"
        )
    lines.extend([
        "",
        "Summary",
        f"  Classic total PnL   ${classic_pnl:+.2f}  exits={classic_reasons}",
        f"  Trailing total PnL  ${trail_pnl:+.2f}  exits={trail_reasons}",
        f"  Extra from trailing ${trail_pnl - classic_pnl:+.2f}",
    ])
    return "\n".join(lines)


def list_open_trades_s42(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    """Open paper trades for Telegram /open (S53 primary paper book)."""
    try:
        rows = conn.execute(
            f"""
            SELECT id, symbol, direction, entry, stop, tp1, tp2, status,
                   created_at, mfe_pct, mae_pct, capital_usd, leverage,
                   s40_signal_type, s40_signal_id
            FROM {_TRADES}
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (STATUS_OPEN, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("list_open_trades_s42 failed: %s", exc)
        return []


def format_trading_audit_report(conn: Any | None = None) -> str:
    """
    S53 — Paper Trading Audit: storage backend, open/closed, WR, PnL, equity, UI sync.
    """
    def _body(c: Any) -> str:
        from bot.research.ai_analyst.signal_consistency.repository import get_repository

        dash = paper_performance_dashboard_s42(c)
        repo = get_repository()
        ai_open = repo.count_ai_open_trades(c)
        ai_signals_today = repo.count_signals_today(conn=c)
        paper_open = int(dash.get("open_trades") or 0)
        ui_open = repo.count_paper_open_trades(c)
        ui_synced = ui_open == paper_open
        lines = [
            "Paper Trading Audit",
            "",
            "Storage backend:",
            _TRADES,
            "",
            "Open:",
            str(paper_open),
            "",
            "Closed:",
            str(int(dash.get("closed_trades") or 0)),
            "",
            "Win Rate:",
            f"{float(dash.get('winrate_pct') or 0):.1f}%",
            "",
            "PnL Today:",
            f"${float(dash.get('today_pnl_usd') or 0):+.2f}",
            "",
            "Equity:",
            f"${float(dash.get('current_equity') or 0):,.2f}",
            "",
            "Data source:",
            _TRADES,
            "",
            "UI status:",
            "Synced ✓" if ui_synced else f"Diverged ✗ (UI open={ui_open} vs storage={paper_open})",
            "",
            "----------------",
            "Entity map",
            "",
            "paper-performance  →  market_events_paper_trades_s42",
            "doctor Open Trades  →  market_events_paper_trades_s42",
            "/open               →  market_events_paper_trades_s42",
            "/stats              →  market_events_paper_trades_s42 (+ account)",
            "/signals            →  ai_signal_history_s48  (AI signals, separate)",
            "doctor Signals today →  ai_signal_history_s48",
            "",
            f"AI paper opens (S47, separate): {ai_open}",
            f"AI signals today (S48): {ai_signals_today}",
        ]
        return "\n".join(lines)

    if conn is not None:
        return _body(conn)
    with market_events_readonly_connection() as c:
        return _body(c)


__all__ = [
    "TRAIL_AFTER_TP1",
    "compare_classic_vs_trailing",
    "format_classic_vs_trailing_ab",
    "format_paper_performance_s42",
    "format_trading_audit_report",
    "initial_trailing_stop",
    "list_open_trades_s42",
    "paper_day_stats_s42",
    "paper_performance_dashboard_s42",
    "paper_trade_counts_s42",
    "ratchet_trailing_stop",
    "refresh_trailing_config_from_env",
    "run_paper_performance_cycle_s42",
    "simulate_exit_on_path",
    "tick_open_paper_trades_s42",
    "trailing_stats_s42",
    "_max_drawdown_pct_from_pnl_usd",
]
