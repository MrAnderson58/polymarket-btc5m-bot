"""Counters for Early Reversion entry diagnostics and outcomes."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass

from bot.database import connect, init_db
from bot.early_reversion import SIGNALS
from bot.risk import RiskCheckResult

logger = logging.getLogger(__name__)

ASK_LEVELS: tuple[float, ...] = (0.50, 0.45, 0.40, 0.35, 0.30, 0.25)
TIMING_BUCKET_LABELS: tuple[str, ...] = ("0-30", "31-60", "61-90", "91-120", "121+")
TRACKED_VERSIONS = frozenset({"v2", "v2.5", "v3"})
YES_C_SHADOW_VERSION = "yes_c_shadow"
FUNNEL_TRACKED_VERSIONS = TRACKED_VERSIONS | frozenset({YES_C_SHADOW_VERSION})
STRATEGY_NAMES: tuple[str, ...] = tuple(signal.strategy_name for signal in SIGNALS)
_ER_TRADE_TABLES: tuple[str, ...] = (
    "early_reversion_v2_trades",
    "early_reversion_v25_trades",
    "early_reversion_v3_trades",
)
_INACTIVE_LOOKBACK_SEC = 86_400
FUNNEL_TIME_WINDOWS: tuple[tuple[str, int], ...] = (
    ("Last 1 hour", 3_600),
    ("Last 6 hours", 21_600),
)
FUNNEL_TIME_REPORT_VERSION = "v2"

_ER_TRADE_SELECT = """
    SELECT market_slug,
           strategy_name,
           status,
           entry_ts,
           entry_price,
           exit_price,
           pnl_percent,
           realized_profit_pct
    FROM {table}
    WHERE strategy_name = ?
"""


@dataclass(frozen=True)
class StrategyTradeStats:
    entries: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_percent: float

BLOCK_REASON_MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
BLOCK_REASON_MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
BLOCK_REASON_DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
BLOCK_REASON_EXISTING_POSITION = "EXISTING_POSITION"
BLOCK_REASON_LIVE_MODE = "LIVE_MODE"
BLOCK_REASON_OTHER = "OTHER"
BLOCK_REASON_UNKNOWN = "UNKNOWN"

_REASON_TO_COLUMN = {
    BLOCK_REASON_MAX_OPEN_POSITIONS: "blocked_max_open_positions",
    BLOCK_REASON_MAX_DAILY_LOSS: "blocked_daily_loss",
    BLOCK_REASON_DUPLICATE_ENTRY: "blocked_duplicate_entry",
    BLOCK_REASON_EXISTING_POSITION: "blocked_existing_position",
    BLOCK_REASON_LIVE_MODE: "blocked_live_mode",
    BLOCK_REASON_OTHER: "blocked_other",
    BLOCK_REASON_UNKNOWN: "blocked_unknown",
}

_BLOCK_DETAIL_COLUMNS = tuple(_REASON_TO_COLUMN.values())

_FUNNEL_INSERT_COLUMNS = (
    "checks",
    "price_reached",
    "window_ok",
    "price_ok",
    "window_and_price_ok",
    "already_open_ok",
    "risk_ok",
    "blocked_by_window",
    "blocked_by_price",
    "blocked_by_already_open",
    "blocked_by_risk",
    *_BLOCK_DETAIL_COLUMNS,
    "entries",
    "entry_attempt",
    "entry_success",
    "exits_tp",
    "exits_stop",
    "exits_expiration",
)


@dataclass(frozen=True)
class StrategyCounterRow:
    strategy_name: str
    checks: int
    price_reached: int
    window_ok: int
    price_ok: int
    window_and_price_ok: int
    already_open_ok: int
    risk_ok: int
    entry_attempt: int
    entry_success: int
    blocked_by_window: int
    blocked_by_price: int
    blocked_by_already_open: int
    blocked_by_risk: int
    blocked_max_open_positions: int
    blocked_daily_loss: int
    blocked_duplicate_entry: int
    blocked_existing_position: int
    blocked_live_mode: int
    blocked_other: int
    blocked_unknown: int
    entries: int
    exits_tp: int
    exits_stop: int
    exits_expiration: int


@dataclass(frozen=True)
class AggregatedFunnelRow:
    strategy_name: str
    checks: int
    window_ok: int
    price_ok: int
    window_and_price_ok: int
    already_open_ok: int
    risk_ok: int
    entry_attempt: int
    entry_success: int
    blocked_by_window: int
    blocked_by_price: int
    blocked_by_already_open: int
    blocked_by_risk: int
    blocked_max_open_positions: int
    blocked_daily_loss: int
    blocked_duplicate_entry: int
    blocked_existing_position: int
    blocked_live_mode: int
    blocked_other: int
    blocked_unknown: int


def classify_block_reason(reason: str | None) -> str:
    if reason is None:
        return BLOCK_REASON_OTHER
    upper = reason.upper()
    if "MAX_OPEN_POSITIONS" in upper:
        return BLOCK_REASON_MAX_OPEN_POSITIONS
    if "MAX_DAILY_LOSS" in upper:
        return BLOCK_REASON_MAX_DAILY_LOSS
    if "DUPLICATE" in upper or "IDEMPOTENCY" in upper:
        return BLOCK_REASON_DUPLICATE_ENTRY
    if "ALREADY_OPEN" in upper or "EXISTING" in upper:
        return BLOCK_REASON_EXISTING_POSITION
    if "LIVE" in upper or "CLOB" in upper or "ORPHAN" in upper:
        return BLOCK_REASON_LIVE_MODE
    return BLOCK_REASON_OTHER


def log_entry_blocked(strategy_name: str, reason_code: str) -> None:
    logger.info(
        "ENTRY BLOCKED\n\nstrategy=%s\n\nreason=%s",
        strategy_name,
        reason_code,
    )


def _zero_deltas() -> dict[str, int]:
    return {name: 0 for name in _FUNNEL_INSERT_COLUMNS}


def _increment_block_counter(deltas: dict[str, int], reason_code: str) -> None:
    column = _REASON_TO_COLUMN.get(reason_code, "blocked_other")
    deltas[column] = deltas.get(column, 0) + 1
    if reason_code in {BLOCK_REASON_MAX_OPEN_POSITIONS, BLOCK_REASON_MAX_DAILY_LOSS}:
        deltas["blocked_by_risk"] = deltas.get("blocked_by_risk", 0) + 1


def record_entry_blocked(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
    reason_code: str,
    *,
    increment_counter: bool = True,
    log_event: bool = True,
) -> None:
    if strategy_version not in FUNNEL_TRACKED_VERSIONS:
        return
    if log_event:
        log_entry_blocked(strategy_name, reason_code)
    if reason_code == BLOCK_REASON_MAX_OPEN_POSITIONS:
        from bot.recovery_health import (
            EVENT_BLOCKED_MAX_OPEN_POSITIONS,
            record_health_event,
        )

        record_health_event(conn, EVENT_BLOCKED_MAX_OPEN_POSITIONS)

    if not increment_counter:
        return
    deltas = _zero_deltas()
    _increment_block_counter(deltas, reason_code)
    _upsert_funnel_counters(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        deltas=deltas,
    )


def _timing_bucket(seconds_open: float) -> str:
    if seconds_open <= 30:
        return "0-30"
    if seconds_open <= 60:
        return "31-60"
    if seconds_open <= 90:
        return "61-90"
    if seconds_open <= 120:
        return "91-120"
    return "121+"


def _record_ask_and_timing(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    ask: float,
    price_reached: bool,
    seconds_open: float,
) -> None:
    for level in ASK_LEVELS:
        if ask <= level:
            conn.execute(
                """
                INSERT INTO er_ask_level_counters (
                    strategy_version, strategy_name, ask_level, hit_count
                ) VALUES (?, ?, ?, 1)
                ON CONFLICT(strategy_version, strategy_name, ask_level) DO UPDATE SET
                    hit_count = hit_count + 1
                """,
                (strategy_version, strategy_name, level),
            )

    if price_reached:
        bucket = _timing_bucket(seconds_open)
        conn.execute(
            """
            INSERT INTO er_timing_counters (
                strategy_version, strategy_name, seconds_bucket, price_reached_count
            ) VALUES (?, ?, ?, 1)
            ON CONFLICT(strategy_version, strategy_name, seconds_bucket) DO UPDATE SET
                price_reached_count = price_reached_count + 1
            """,
            (strategy_version, strategy_name, bucket),
        )


def _compute_funnel_deltas(
    *,
    seconds_open: float,
    entry_window_sec: int,
    ask: float | None,
    entry_threshold: float,
    has_trade: bool,
    risk: RiskCheckResult | None,
    would_enter: bool,
) -> tuple[dict[str, int], str | None]:
    window_passed = seconds_open <= entry_window_sec
    price_passed = ask is not None and ask <= entry_threshold
    window_and_price_passed = window_passed and price_passed
    already_open_passed = window_and_price_passed and not has_trade

    risk_passed = int(would_enter)
    if not would_enter and already_open_passed and risk is not None:
        risk_passed = int(risk.allowed)

    deltas = {
        "checks": 1,
        "price_reached": int(price_passed),
        "window_ok": int(window_passed),
        "price_ok": int(price_passed),
        "window_and_price_ok": int(window_and_price_passed),
        "already_open_ok": int(already_open_passed),
        "risk_ok": risk_passed,
        "blocked_by_window": int(not window_passed),
        "blocked_by_price": int(not price_passed),
        "blocked_by_already_open": int(window_and_price_passed and has_trade),
        "blocked_by_risk": 0,
        **{column: 0 for column in _BLOCK_DETAIL_COLUMNS},
        "entries": 0,
        "entry_attempt": 0,
        "entry_success": 0,
        "exits_tp": 0,
        "exits_stop": 0,
        "exits_expiration": 0,
    }

    block_reason: str | None = None
    if window_and_price_passed and has_trade:
        _increment_block_counter(deltas, BLOCK_REASON_EXISTING_POSITION)
        block_reason = BLOCK_REASON_EXISTING_POSITION
    elif already_open_passed and risk is not None and not risk.allowed:
        block_reason = classify_block_reason(risk.reason)
        _increment_block_counter(deltas, block_reason)
    elif already_open_passed and deltas["risk_ok"] == 0:
        block_reason = BLOCK_REASON_UNKNOWN
        _increment_block_counter(deltas, block_reason)

    return deltas, block_reason


def _upsert_funnel_counters(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    deltas: dict[str, int],
) -> None:
    columns = ", ".join(_FUNNEL_INSERT_COLUMNS)
    placeholders = ", ".join("?" for _ in _FUNNEL_INSERT_COLUMNS)
    updates = ", ".join(f"{column} = {column} + excluded.{column}" for column in _FUNNEL_INSERT_COLUMNS)
    conn.execute(
        f"""
        INSERT INTO er_strategy_counters (
            strategy_version, strategy_name, {columns}
        ) VALUES (?, ?, {placeholders})
        ON CONFLICT(strategy_version, strategy_name) DO UPDATE SET
            {updates}
        """,
        (strategy_version, strategy_name, *[deltas[column] for column in _FUNNEL_INSERT_COLUMNS]),
    )


def _insert_funnel_event(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    market_slug: str | None,
    eval_ts: int,
    deltas: dict[str, int],
) -> None:
    conn.execute(
        """
        INSERT INTO er_funnel_events (
            strategy_version, strategy_name, market_slug, eval_ts,
            window_ok, price_ok, already_open_ok, risk_ok
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_version,
            strategy_name,
            market_slug,
            eval_ts,
            deltas["window_ok"],
            deltas["price_ok"],
            deltas["already_open_ok"],
            deltas["risk_ok"],
        ),
    )


def record_entry_evaluation(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    ask: float | None,
    entry_threshold: float,
    seconds_open: float,
    entry_window_sec: int,
    has_trade: bool,
    risk: RiskCheckResult | None,
    would_enter: bool = False,
    market_slug: str | None = None,
    eval_ts: int | None = None,
) -> None:
    """Record one entry evaluation with sequential funnel counters."""
    if strategy_version not in FUNNEL_TRACKED_VERSIONS:
        return

    deltas, block_reason = _compute_funnel_deltas(
        seconds_open=seconds_open,
        entry_window_sec=entry_window_sec,
        ask=ask,
        entry_threshold=entry_threshold,
        has_trade=has_trade,
        risk=risk,
        would_enter=would_enter,
    )
    _upsert_funnel_counters(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        deltas=deltas,
    )
    _insert_funnel_event(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        market_slug=market_slug,
        eval_ts=eval_ts if eval_ts is not None else int(time.time()),
        deltas=deltas,
    )

    if block_reason == BLOCK_REASON_MAX_OPEN_POSITIONS:
        from bot.recovery_health import (
            EVENT_BLOCKED_MAX_OPEN_POSITIONS,
            record_health_event,
        )

        record_health_event(
            conn,
            EVENT_BLOCKED_MAX_OPEN_POSITIONS,
            event_ts=eval_ts if eval_ts is not None else int(time.time()),
        )

    if block_reason is not None:
        log_entry_blocked(strategy_name, block_reason)

    if ask is None:
        return
    _record_ask_and_timing(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        ask=ask,
        price_reached=bool(deltas["price_reached"]),
        seconds_open=seconds_open,
    )


def record_strategy_check(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    strategy_name: str,
    ask: float | None,
    entry_threshold: float,
    seconds_open: float,
) -> None:
    """Backward-compatible wrapper for tests and legacy callers."""
    record_entry_evaluation(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        ask=ask,
        entry_threshold=entry_threshold,
        seconds_open=seconds_open,
        entry_window_sec=10**9,
        has_trade=False,
        risk=None,
        would_enter=False,
    )


def record_entry_attempt(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
) -> None:
    if strategy_version not in FUNNEL_TRACKED_VERSIONS:
        return
    _upsert_funnel_counters(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        deltas=_zero_deltas() | {"entry_attempt": 1},
    )


def record_entry_success(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
) -> None:
    if strategy_version not in FUNNEL_TRACKED_VERSIONS:
        return
    _upsert_funnel_counters(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        deltas=_zero_deltas() | {"entries": 1, "entry_success": 1},
    )


def record_entry_executed(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
) -> None:
    """Backward-compatible alias."""
    record_entry_success(conn, strategy_version, strategy_name)


def record_exit(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
    exit_reason: str,
) -> None:
    if strategy_version not in TRACKED_VERSIONS:
        return

    column = {
        "TRAILING_STOP": "exits_tp",
        "STOP_LOSS": "exits_stop",
        "TIME_STOP": "exits_expiration",
    }.get(exit_reason)
    if column is None:
        return

    deltas = {name: 0 for name in _FUNNEL_INSERT_COLUMNS}
    deltas[column] = 1
    _upsert_funnel_counters(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        deltas=deltas,
    )


def _counter_row_from_db(row: sqlite3.Row) -> StrategyCounterRow:
    return StrategyCounterRow(
        strategy_name=row["strategy_name"],
        checks=int(row["checks"]),
        price_reached=int(row["price_reached"]),
        window_ok=int(row["window_ok"]),
        price_ok=int(row["price_ok"]),
        window_and_price_ok=int(row["window_and_price_ok"]),
        already_open_ok=int(row["already_open_ok"]),
        risk_ok=int(row["risk_ok"]),
        entry_attempt=int(row["entry_attempt"]),
        entry_success=int(row["entry_success"]),
        blocked_by_window=int(row["blocked_by_window"]),
        blocked_by_price=int(row["blocked_by_price"]),
        blocked_by_already_open=int(row["blocked_by_already_open"]),
        blocked_by_risk=int(row["blocked_by_risk"]),
        blocked_max_open_positions=int(row["blocked_max_open_positions"]),
        blocked_daily_loss=int(row["blocked_daily_loss"]),
        blocked_duplicate_entry=int(row["blocked_duplicate_entry"]),
        blocked_existing_position=int(row["blocked_existing_position"]),
        blocked_live_mode=int(row["blocked_live_mode"]),
        blocked_other=int(row["blocked_other"]),
        blocked_unknown=int(row["blocked_unknown"]),
        entries=int(row["entries"]),
        exits_tp=int(row["exits_tp"]),
        exits_stop=int(row["exits_stop"]),
        exits_expiration=int(row["exits_expiration"]),
    )


_BLOCK_DETAIL_SELECT = ", ".join(
    f"COALESCE({column}, 0) AS {column}" for column in _BLOCK_DETAIL_COLUMNS
)


def fetch_strategy_counters(
    conn: sqlite3.Connection,
    strategy_version: str,
) -> list[StrategyCounterRow]:
    rows = conn.execute(
        f"""
        SELECT strategy_name, checks, price_reached,
               COALESCE(window_ok, 0) AS window_ok,
               COALESCE(price_ok, 0) AS price_ok,
               COALESCE(window_and_price_ok, 0) AS window_and_price_ok,
               COALESCE(already_open_ok, 0) AS already_open_ok,
               COALESCE(risk_ok, 0) AS risk_ok,
               COALESCE(entry_attempt, 0) AS entry_attempt,
               COALESCE(entry_success, 0) AS entry_success,
               COALESCE(blocked_by_window, 0) AS blocked_by_window,
               COALESCE(blocked_by_price, 0) AS blocked_by_price,
               COALESCE(blocked_by_already_open, 0) AS blocked_by_already_open,
               COALESCE(blocked_by_risk, 0) AS blocked_by_risk,
               {_BLOCK_DETAIL_SELECT},
               entries, exits_tp, exits_stop, exits_expiration
        FROM er_strategy_counters
        WHERE strategy_version = ?
        ORDER BY strategy_name ASC
        """,
        (strategy_version,),
    ).fetchall()
    return [_counter_row_from_db(row) for row in rows]


def fetch_aggregated_funnel_stats(
    conn: sqlite3.Connection,
) -> list[AggregatedFunnelRow]:
    placeholders = ",".join("?" for _ in TRACKED_VERSIONS)
    rows = conn.execute(
        f"""
        SELECT strategy_name,
               SUM(checks) AS checks,
               SUM(COALESCE(window_ok, 0)) AS window_ok,
               SUM(COALESCE(price_ok, 0)) AS price_ok,
               SUM(COALESCE(window_and_price_ok, 0)) AS window_and_price_ok,
               SUM(COALESCE(already_open_ok, 0)) AS already_open_ok,
               SUM(COALESCE(risk_ok, 0)) AS risk_ok,
               SUM(COALESCE(entry_attempt, 0)) AS entry_attempt,
               SUM(COALESCE(entry_success, 0)) AS entry_success,
               SUM(COALESCE(blocked_by_window, 0)) AS blocked_by_window,
               SUM(COALESCE(blocked_by_price, 0)) AS blocked_by_price,
               SUM(COALESCE(blocked_by_already_open, 0)) AS blocked_by_already_open,
               SUM(COALESCE(blocked_by_risk, 0)) AS blocked_by_risk,
               {", ".join(f"SUM(COALESCE({column}, 0)) AS {column}" for column in _BLOCK_DETAIL_COLUMNS)}
        FROM er_strategy_counters
        WHERE strategy_version IN ({placeholders})
        GROUP BY strategy_name
        ORDER BY strategy_name ASC
        """,
        tuple(sorted(TRACKED_VERSIONS)),
    ).fetchall()
    by_name = {
        row["strategy_name"]: AggregatedFunnelRow(
            strategy_name=row["strategy_name"],
            checks=int(row["checks"]),
            window_ok=int(row["window_ok"]),
            price_ok=int(row["price_ok"]),
            window_and_price_ok=int(row["window_and_price_ok"]),
            already_open_ok=int(row["already_open_ok"]),
            risk_ok=int(row["risk_ok"]),
            entry_attempt=int(row["entry_attempt"]),
            entry_success=int(row["entry_success"]),
            blocked_by_window=int(row["blocked_by_window"]),
            blocked_by_price=int(row["blocked_by_price"]),
            blocked_by_already_open=int(row["blocked_by_already_open"]),
            blocked_by_risk=int(row["blocked_by_risk"]),
            blocked_max_open_positions=int(row["blocked_max_open_positions"]),
            blocked_daily_loss=int(row["blocked_daily_loss"]),
            blocked_duplicate_entry=int(row["blocked_duplicate_entry"]),
            blocked_existing_position=int(row["blocked_existing_position"]),
            blocked_live_mode=int(row["blocked_live_mode"]),
            blocked_other=int(row["blocked_other"]),
            blocked_unknown=int(row["blocked_unknown"]),
        )
        for row in rows
    }
    return [by_name[name] for name in STRATEGY_NAMES if name in by_name]


def _enabled_strategy_names() -> tuple[str, ...]:
    from bot.config import (
        ENABLED_STRATEGIES,
        ENABLED_STRATEGIES_V2,
        ENABLED_STRATEGIES_V25,
        ENABLED_STRATEGIES_V3,
    )

    combined = (
        ENABLED_STRATEGIES
        | ENABLED_STRATEGIES_V2
        | ENABLED_STRATEGIES_V25
        | ENABLED_STRATEGIES_V3
    )
    order = ("YES_C", "NO_C", "YES_B", "YES_A", "NO_A", "NO_B")
    known = [name for name in order if name in combined]
    extra = sorted(name for name in combined if name not in order)
    return tuple(known + extra)


def _realized_pnl_percent(row: sqlite3.Row) -> float:
    if row["realized_profit_pct"] is not None:
        return float(row["realized_profit_pct"])
    if row["pnl_percent"] is not None:
        return float(row["pnl_percent"])
    entry_price = float(row["entry_price"])
    exit_price = row["exit_price"]
    if exit_price is None or entry_price <= 0:
        return 0.0
    return (float(exit_price) - entry_price) / entry_price * 100


def _dedupe_strategy_trades(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Keep one row per (market_slug, strategy_name); prefer latest entry_ts."""
    by_key: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row["market_slug"], row["strategy_name"])
        existing = by_key.get(key)
        if existing is None or int(row["entry_ts"]) >= int(existing["entry_ts"]):
            by_key[key] = row
    return list(by_key.values())


def _fetch_strategy_trade_rows(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for table in _ER_TRADE_TABLES:
        rows.extend(
            conn.execute(_ER_TRADE_SELECT.format(table=table), (strategy_name,)).fetchall()
        )
    return _dedupe_strategy_trades(rows)


def _fetch_strategy_trade_stats(
    conn: sqlite3.Connection,
    strategy_name: str,
) -> StrategyTradeStats:
    trades = _fetch_strategy_trade_rows(conn, strategy_name)
    open_trades = [trade for trade in trades if trade["status"] == "open"]
    closed_trades = [trade for trade in trades if trade["status"] == "closed"]

    wins = 0
    losses = 0
    pnls: list[float] = []
    for trade in closed_trades:
        pnl = _realized_pnl_percent(trade)
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        else:
            losses += 1

    closed_count = len(closed_trades)
    entries = len(trades)
    return StrategyTradeStats(
        entries=entries,
        open_trades=len(open_trades),
        closed_trades=closed_count,
        wins=wins,
        losses=losses,
        win_rate=wins / closed_count if closed_count else 0.0,
        avg_pnl_percent=sum(pnls) / len(pnls) if pnls else 0.0,
    )


def _count_recent_entries(
    conn: sqlite3.Connection,
    strategy_name: str,
    since_ts: int,
) -> int:
    recent = [
        trade
        for trade in _fetch_strategy_trade_rows(conn, strategy_name)
        if int(trade["entry_ts"]) >= since_ts
    ]
    return len(recent)


def format_er_summary(conn: sqlite3.Connection) -> str:
    aggregated = {
        row.strategy_name: row for row in fetch_aggregated_funnel_stats(conn)
    }
    lines: list[str] = ["ER SUMMARY", ""]
    enabled = _enabled_strategy_names()

    for strategy_name in enabled:
        row = aggregated.get(strategy_name)
        checks = row.checks if row is not None else 0
        trade_stats = _fetch_strategy_trade_stats(conn, strategy_name)
        lines.extend(
            [
                strategy_name,
                "",
                f"checks: {checks}",
                f"entries: {trade_stats.entries}",
                f"closed_trades: {trade_stats.closed_trades}",
                f"wins: {trade_stats.wins}",
                f"losses: {trade_stats.losses}",
                f"win_rate: {trade_stats.win_rate:.1%}",
                f"avg pnl: {trade_stats.avg_pnl_percent:+.2f}%",
                "",
            ]
        )

    if not enabled:
        lines.append("(no enabled strategies)")

    since_ts = int(time.time()) - _INACTIVE_LOOKBACK_SEC
    inactive = [
        strategy_name
        for strategy_name in enabled
        if _count_recent_entries(conn, strategy_name, since_ts) == 0
    ]
    if inactive:
        lines.append("")
        for strategy_name in inactive:
            lines.append(f"Strategy inactive: {strategy_name}")

    from bot.recovery_health import fetch_recovery_health_stats, format_recovery_health_block
    from bot.no_c_btc_filter import format_no_c_filter_live_block

    lines.extend(["", format_recovery_health_block(fetch_recovery_health_stats(conn))])
    lines.extend(["", format_no_c_filter_live_block(conn)])

    return "\n".join(lines).rstrip()


def log_er_summary(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_er_summary(conn))


@dataclass(frozen=True)
class FunnelWindowStats:
    label: str
    strategy_name: str
    checks: int
    window_ok: int
    price_ok: int
    ready: int
    entries: int


def _count_entries_since(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    since_ts: int,
    strategy_version: str = FUNNEL_TIME_REPORT_VERSION,
) -> int:
    table = {
        "v2": "early_reversion_v2_trades",
        "v2.5": "early_reversion_v25_trades",
        "v3": "early_reversion_v3_trades",
    }.get(strategy_version)
    if table is None:
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM {table}
        WHERE strategy_name = ? AND entry_ts >= ?
        """,
        (strategy_name, since_ts),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def fetch_funnel_window_stats(
    conn: sqlite3.Connection,
    *,
    strategy_name: str,
    since_ts: int,
    label: str,
    strategy_version: str = FUNNEL_TIME_REPORT_VERSION,
) -> FunnelWindowStats:
    row = conn.execute(
        """
        SELECT COUNT(*) AS checks,
               COALESCE(SUM(window_ok), 0) AS window_ok,
               COALESCE(SUM(price_ok), 0) AS price_ok,
               COALESCE(SUM(already_open_ok), 0) AS ready
        FROM er_funnel_events
        WHERE strategy_version = ?
          AND strategy_name = ?
          AND eval_ts >= ?
        """,
        (strategy_version, strategy_name, since_ts),
    ).fetchone()
    checks = int(row["checks"]) if row is not None else 0
    return FunnelWindowStats(
        label=label,
        strategy_name=strategy_name,
        checks=checks,
        window_ok=int(row["window_ok"]) if row is not None else 0,
        price_ok=int(row["price_ok"]) if row is not None else 0,
        ready=int(row["ready"]) if row is not None else 0,
        entries=_count_entries_since(
            conn,
            strategy_name=strategy_name,
            since_ts=since_ts,
            strategy_version=strategy_version,
        ),
    )


def format_strategy_funnel_window_block(stats: FunnelWindowStats) -> list[str]:
    return [
        stats.label,
        "",
        stats.strategy_name,
        "",
        f"Checks: {stats.checks}",
        "",
        f"Window OK: {stats.window_ok}",
        "",
        f"Price OK: {stats.price_ok}",
        "",
        f"Ready: {stats.ready}",
        "",
        f"Entries: {stats.entries}",
    ]


def format_er_funnel_time_report(
    conn: sqlite3.Connection,
    *,
    strategy_names: tuple[str, ...] | None = None,
) -> str:
    names = strategy_names or _enabled_strategy_names() or ("NO_C",)
    now_ts = int(time.time())
    blocks: list[str] = ["ER FUNNEL BY TIME", ""]
    for window_label, window_sec in FUNNEL_TIME_WINDOWS:
        since_ts = now_ts - window_sec
        for index, strategy_name in enumerate(names):
            stats = fetch_funnel_window_stats(
                conn,
                strategy_name=strategy_name,
                since_ts=since_ts,
                label=window_label,
            )
            if index > 0 or window_label != FUNNEL_TIME_WINDOWS[0][0]:
                blocks.append("")
            blocks.extend(format_strategy_funnel_window_block(stats))
    return "\n".join(blocks).rstrip()


def log_er_funnel_time_report(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_er_funnel_time_report(conn))


def fetch_ask_level_counts(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
) -> list[tuple[float, int]]:
    rows = conn.execute(
        """
        SELECT ask_level, hit_count
        FROM er_ask_level_counters
        WHERE strategy_version = ? AND strategy_name = ?
        ORDER BY ask_level DESC
        """,
        (strategy_version, strategy_name),
    ).fetchall()
    by_level = {float(row["ask_level"]): int(row["hit_count"]) for row in rows}
    return [(level, by_level[level]) for level in ASK_LEVELS if level in by_level]


def fetch_timing_counts(
    conn: sqlite3.Connection,
    strategy_version: str,
    strategy_name: str,
) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT seconds_bucket, price_reached_count
        FROM er_timing_counters
        WHERE strategy_version = ? AND strategy_name = ?
        """,
        (strategy_version, strategy_name),
    ).fetchall()
    by_bucket = {row["seconds_bucket"]: int(row["price_reached_count"]) for row in rows}
    return [(label, by_bucket[label]) for label in TIMING_BUCKET_LABELS if label in by_bucket]


def format_report(conn: sqlite3.Connection, strategy_version: str) -> str:
    lines: list[str] = [
        f"Early Reversion stats | {strategy_version}",
        "",
        _format_summary_table(fetch_strategy_counters(conn, strategy_version)),
    ]

    for row in fetch_strategy_counters(conn, strategy_version):
        ask_lines = fetch_ask_level_counts(conn, strategy_version, row.strategy_name)
        timing_lines = fetch_timing_counts(conn, strategy_version, row.strategy_name)
        if not ask_lines and not timing_lines:
            continue

        lines.append("")
        lines.append(row.strategy_name)

        for level, count in ask_lines:
            lines.append(f"  ask <={level:.2f}   {count} раз")

        if timing_lines:
            lines.append("  цена <= порога по секундам от старта окна:")
            for bucket, count in timing_lines:
                lines.append(f"    {bucket}s: {count}")

    return "\n".join(lines)


def _format_summary_table(rows: list[StrategyCounterRow]) -> str:
    header = (
        f"{'Стратегия':<10} {'Проверок':>10} {'Окно':>8} {'Цена':>8} "
        f"{'W+P':>6} {'Open':>6} {'Risk':>6} {'Try':>6} {'OK':>6}"
    )
    if not rows:
        return header + "\n(нет данных)"
    body = [
        (
            f"{row.strategy_name:<10} {row.checks:>10} {row.window_ok:>8} "
            f"{row.price_ok:>8} {row.window_and_price_ok:>6} {row.already_open_ok:>6} "
            f"{row.risk_ok:>6} {row.entry_attempt:>6} {row.entry_success:>6}"
        )
        for row in rows
    ]
    return "\n".join([header, *body])


def print_report(conn: sqlite3.Connection, strategy_version: str) -> None:
    print(format_report(conn, strategy_version))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Early Reversion strategy counters")
    parser.add_argument(
        "--version",
        choices=sorted(FUNNEL_TRACKED_VERSIONS),
        default="v2",
        help="Strategy version to report (default: v2)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print reports for v2, v2.5, and v3",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print aggregated ER SUMMARY rejection stats",
    )
    parser.add_argument(
        "--trailing-summary",
        action="store_true",
        help="Print trailing stop activation statistics",
    )
    parser.add_argument(
        "--btc-direction-summary",
        action="store_true",
        help="Print BTC direction analytics for closed trades",
    )
    parser.add_argument(
        "--yes-c-shadow-summary",
        action="store_true",
        help="Print YES_C shadow report and comparison with NO_C live",
    )
    parser.add_argument(
        "--v4-shadow-summary",
        action="store_true",
        help="Print V4 shadow report and strategy comparison",
    )
    parser.add_argument(
        "--funnel-time-summary",
        action="store_true",
        help="Print ER funnel stats for the last 1 hour and 6 hours",
    )
    parser.add_argument(
        "--no-c-filter-shadow",
        action="store_true",
        help="Print NO_C BTC-up filter shadow statistics",
    )
    parser.add_argument(
        "--stop-loss-recovery-summary",
        action="store_true",
        help="Print STOP LOSS recovery analysis for early_reversion_v2_trades",
    )
    parser.add_argument(
        "--stop-loss-short-recovery",
        action="store_true",
        help="Print STOP LOSS short-window recovery analysis (15/30/45/60s)",
    )
    args = parser.parse_args(argv)

    init_db()
    with connect() as conn:
        if args.btc_direction_summary:
            from bot.er_btc_direction_stats import format_btc_direction_summary

            print(format_btc_direction_summary(conn))
        elif args.yes_c_shadow_summary:
            from bot.yes_c_shadow_stats import format_yes_c_shadow_full_report

            print(format_yes_c_shadow_full_report(conn))
        elif args.v4_shadow_summary:
            from bot.v4_shadow_stats import format_v4_shadow_full_report

            print(format_v4_shadow_full_report(conn))
        elif args.funnel_time_summary:
            print(format_er_funnel_time_report(conn))
        elif args.no_c_filter_shadow:
            from bot.no_c_filter_shadow import format_no_c_filter_shadow_report

            print(format_no_c_filter_shadow_report(conn))
        elif args.stop_loss_recovery_summary:
            from bot.er_stop_loss_recovery_stats import format_stop_loss_recovery_summary

            print(format_stop_loss_recovery_summary(conn))
        elif args.stop_loss_short_recovery:
            from bot.er_stop_loss_short_recovery_stats import (
                format_stop_loss_short_recovery_summary,
            )

            print(format_stop_loss_short_recovery_summary(conn))
        elif args.trailing_summary:
            from bot.er_trailing_stats import format_trailing_summary

            print(format_trailing_summary(conn))
        elif args.summary:
            print(format_er_summary(conn))
        elif args.all:
            for version in sorted(TRACKED_VERSIONS):
                print_report(conn, version)
                print()
        else:
            print_report(conn, args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
