"""Analytics for V4 shadow trades."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC
from bot.er_btc_direction_stats import (
    BTC_DIRECTION_ORDER,
    BTC_FLAT_THRESHOLD_USD,
    BtcDirection,
    _classify_btc_direction,
    _exit_ts,
    _nearest_btc_price,
)
from bot.er_stats import _realized_pnl_percent

logger = logging.getLogger(__name__)

V4_SHADOW_TABLE = "v4_shadow_trades"


@dataclass(frozen=True)
class BtcDirectionSlice:
    direction: str
    trades: int
    win_rate: float


@dataclass(frozen=True)
class V4ShadowSummary:
    trades: int
    closed_trades: int
    open_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_percent: float
    profit_factor: float
    avg_holding_seconds: float
    avg_entry_price: float
    avg_exit_price: float
    trailing_stop_exits: int
    stop_loss_exits: int
    time_stop_exits: int


@dataclass(frozen=True)
class ComparisonSummary:
    strategy: str
    mode: str
    trades: int
    win_rate: float
    avg_pnl_percent: float


def _pnl_usdc(row: sqlite3.Row) -> float:
    entry_price = float(row["entry_price"])
    exit_price = row["exit_price"]
    if exit_price is None or entry_price <= 0:
        return 0.0
    shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
    return shares * (float(exit_price) - entry_price)


def _profit_factor(closed: list[sqlite3.Row]) -> float:
    gross_profit = sum(_pnl_usdc(row) for row in closed if _pnl_usdc(row) > 0)
    gross_loss = abs(sum(_pnl_usdc(row) for row in closed if _pnl_usdc(row) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _format_profit_factor(value: float) -> str:
    if value == float("inf"):
        return "∞"
    if value == 0:
        return "0.00"
    return f"{value:.2f}"


def _fetch_all_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT * FROM {V4_SHADOW_TABLE}
        ORDER BY entry_ts ASC
        """
    ).fetchall()


def _fetch_closed_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT * FROM {V4_SHADOW_TABLE}
        WHERE status = 'closed'
        ORDER BY entry_ts ASC
        """
    ).fetchall()


def _exit_reason_counts(closed: list[sqlite3.Row]) -> tuple[int, int, int]:
    trailing = sum(1 for row in closed if row["exit_reason"] == "TRAILING_STOP")
    time_stop = sum(1 for row in closed if row["exit_reason"] == "TIME_STOP")
    stop_loss = sum(1 for row in closed if row["exit_reason"] == "STOP_LOSS")
    return trailing, stop_loss, time_stop


def fetch_v4_shadow_summary(conn: sqlite3.Connection) -> V4ShadowSummary:
    all_trades = _fetch_all_trades(conn)
    closed = [row for row in all_trades if row["status"] == "closed"]
    open_trades = [row for row in all_trades if row["status"] == "open"]

    wins = 0
    losses = 0
    pnls: list[float] = []
    holding_times: list[float] = []
    entry_prices: list[float] = []
    exit_prices: list[float] = []

    for row in closed:
        pnl = _realized_pnl_percent(row)
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        if row["holding_time_seconds"] is not None:
            holding_times.append(float(row["holding_time_seconds"]))
        entry_prices.append(float(row["entry_price"]))
        if row["exit_price"] is not None:
            exit_prices.append(float(row["exit_price"]))

    closed_count = len(closed)
    trailing, stop_loss, time_stop = _exit_reason_counts(closed)

    return V4ShadowSummary(
        trades=len(all_trades),
        closed_trades=closed_count,
        open_trades=len(open_trades),
        wins=wins,
        losses=losses,
        win_rate=wins / closed_count if closed_count else 0.0,
        avg_pnl_percent=sum(pnls) / len(pnls) if pnls else 0.0,
        profit_factor=_profit_factor(closed),
        avg_holding_seconds=sum(holding_times) / len(holding_times) if holding_times else 0.0,
        avg_entry_price=sum(entry_prices) / len(entry_prices) if entry_prices else 0.0,
        avg_exit_price=sum(exit_prices) / len(exit_prices) if exit_prices else 0.0,
        trailing_stop_exits=trailing,
        stop_loss_exits=stop_loss,
        time_stop_exits=time_stop,
    )


def fetch_v4_shadow_comparison_summary(conn: sqlite3.Connection) -> ComparisonSummary:
    summary = fetch_v4_shadow_summary(conn)
    return ComparisonSummary(
        strategy="V4",
        mode="SHADOW",
        trades=summary.closed_trades,
        win_rate=summary.win_rate,
        avg_pnl_percent=summary.avg_pnl_percent,
    )


def _btc_direction_slices(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> tuple[BtcDirectionSlice, ...]:
    by_direction: dict[str, list[float]] = {direction: [] for direction in BTC_DIRECTION_ORDER}

    for trade in closed:
        entry_ts = int(trade["entry_ts"])
        exit_ts = _exit_ts(trade)
        btc_entry = _nearest_btc_price(conn, market_slug=trade["market_slug"], target_ts=entry_ts)
        btc_exit = _nearest_btc_price(conn, market_slug=trade["market_slug"], target_ts=exit_ts)
        if btc_entry is None or btc_exit is None:
            continue
        direction = _classify_btc_direction(btc_exit - btc_entry).value
        by_direction[direction].append(_realized_pnl_percent(trade))

    slices: list[BtcDirectionSlice] = []
    for direction in BTC_DIRECTION_ORDER:
        pnls = by_direction[direction]
        wins = sum(1 for pnl in pnls if pnl > 0)
        count = len(pnls)
        slices.append(
            BtcDirectionSlice(
                direction=direction,
                trades=count,
                win_rate=wins / count if count else 0.0,
            )
        )
    return tuple(slices)


def format_v4_shadow_report(conn: sqlite3.Connection) -> str:
    closed = _fetch_closed_trades(conn)
    summary = fetch_v4_shadow_summary(conn)
    btc_slices = _btc_direction_slices(conn, closed)

    lines = [
        "V4 SHADOW",
        "",
        f"Trades: {summary.trades}",
        "",
        f"Closed Trades: {summary.closed_trades}",
        "",
        f"Wins: {summary.wins}",
        "",
        f"Losses: {summary.losses}",
        "",
        f"Win Rate: {summary.win_rate:.0%}",
        "",
        f"Average PnL: {summary.avg_pnl_percent:+.1f}%",
        "",
        f"Profit Factor: {_format_profit_factor(summary.profit_factor)}",
        "",
        f"Average Holding Time: {summary.avg_holding_seconds:.0f}s",
        "",
        f"Average Entry Price: {summary.avg_entry_price:.4f}",
        "",
        f"Average Exit Price: {summary.avg_exit_price:.4f}",
        "",
        f"Trailing Stop exits: {summary.trailing_stop_exits}",
        "",
        f"Stop Loss exits: {summary.stop_loss_exits}",
        "",
        f"Time Stop exits: {summary.time_stop_exits}",
        "",
        f"(BTC flat threshold: ${BTC_FLAT_THRESHOLD_USD:.0f})",
        "",
    ]

    for bucket in btc_slices:
        lines.extend(
            [
                bucket.direction,
                "",
                f"Trades: {bucket.trades}",
                "",
                f"Win Rate: {bucket.win_rate:.0%}",
                "",
            ]
        )

    return "\n".join(lines).rstrip()


def format_v4_shadow_full_report(conn: sqlite3.Connection) -> str:
    from bot.yes_c_shadow_stats import format_strategy_comparison

    return "\n".join(
        [
            format_v4_shadow_report(conn),
            "",
            format_strategy_comparison(conn),
        ]
    )


def log_v4_shadow_report(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_v4_shadow_full_report(conn))
