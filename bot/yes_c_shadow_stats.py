"""Analytics for YES_C shadow trades and comparison with NO_C live."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.er_btc_direction_stats import (
    BTC_FLAT_THRESHOLD_USD,
    BtcDirection,
    _classify_btc_direction,
    _exit_ts,
    _nearest_btc_price,
)
from bot.er_stats import (
    YES_C_SHADOW_VERSION,
    _realized_pnl_percent,
    fetch_strategy_counters,
)

logger = logging.getLogger(__name__)

YES_C_SHADOW_TABLE = "yes_c_shadow_trades"
NO_C_LIVE_TABLE = "early_reversion_v2_trades"
NO_C_STRATEGY = "NO_C"
YES_C_STRATEGY = "YES_C"


@dataclass(frozen=True)
class TradeSummary:
    strategy: str
    mode: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_percent: float
    profit_factor: float
    avg_holding_seconds: float


@dataclass(frozen=True)
class BtcDirectionSlice:
    direction: str
    trades: int
    win_rate: float


def _profit_factor(closed: list[sqlite3.Row]) -> float:
    gross_profit = sum(float(row["pnl_usdc"]) for row in closed if row["pnl_usdc"] and row["pnl_usdc"] > 0)
    gross_loss = abs(
        sum(float(row["pnl_usdc"]) for row in closed if row["pnl_usdc"] and row["pnl_usdc"] < 0)
    )
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _format_profit_factor(value: float) -> str:
    if value == float("inf"):
        return "∞"
    if value == 0:
        return "0.00"
    return f"{value:.2f}"


def _fetch_closed_trades(
    conn: sqlite3.Connection,
    *,
    table: str,
    strategy_name: str | None = None,
) -> list[sqlite3.Row]:
    if strategy_name is None:
        query = f"""
            SELECT * FROM {table}
            WHERE status = 'closed'
            ORDER BY entry_ts ASC
        """
        return conn.execute(query).fetchall()

    return conn.execute(
        f"""
        SELECT * FROM {table}
        WHERE status = 'closed' AND strategy_name = ?
        ORDER BY entry_ts ASC
        """,
        (strategy_name,),
    ).fetchall()


def _summarize_trades(
    closed: list[sqlite3.Row],
    *,
    strategy: str,
    mode: str,
) -> TradeSummary:
    wins = 0
    losses = 0
    pnls: list[float] = []
    holding_times: list[float] = []

    for row in closed:
        pnl = _realized_pnl_percent(row)
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        if row["holding_time_seconds"] is not None:
            holding_times.append(float(row["holding_time_seconds"]))

    count = len(closed)
    return TradeSummary(
        strategy=strategy,
        mode=mode,
        trades=count,
        wins=wins,
        losses=losses,
        win_rate=wins / count if count else 0.0,
        avg_pnl_percent=sum(pnls) / len(pnls) if pnls else 0.0,
        profit_factor=_profit_factor(closed),
        avg_holding_seconds=sum(holding_times) / len(holding_times) if holding_times else 0.0,
    )


def _btc_direction_slices(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> tuple[BtcDirectionSlice, BtcDirectionSlice]:
    up_pnls: list[float] = []
    down_pnls: list[float] = []

    for trade in closed:
        entry_ts = int(trade["entry_ts"])
        exit_ts = _exit_ts(trade)
        btc_entry = _nearest_btc_price(conn, market_slug=trade["market_slug"], target_ts=entry_ts)
        btc_exit = _nearest_btc_price(conn, market_slug=trade["market_slug"], target_ts=exit_ts)
        if btc_entry is None or btc_exit is None:
            continue
        direction = _classify_btc_direction(btc_exit - btc_entry)
        pnl = _realized_pnl_percent(trade)
        if direction == BtcDirection.UP:
            up_pnls.append(pnl)
        elif direction == BtcDirection.DOWN:
            down_pnls.append(pnl)

    def _slice(direction: str, pnls: list[float]) -> BtcDirectionSlice:
        wins = sum(1 for pnl in pnls if pnl > 0)
        count = len(pnls)
        return BtcDirectionSlice(
            direction=direction,
            trades=count,
            win_rate=wins / count if count else 0.0,
        )

    return _slice("BTC UP", up_pnls), _slice("BTC DOWN", down_pnls)


def fetch_yes_c_shadow_summary(conn: sqlite3.Connection) -> TradeSummary:
    closed = _fetch_closed_trades(conn, table=YES_C_SHADOW_TABLE)
    return _summarize_trades(closed, strategy=YES_C_STRATEGY, mode="SHADOW")


def fetch_no_c_live_summary(conn: sqlite3.Connection) -> TradeSummary:
    closed = _fetch_closed_trades(
        conn,
        table=NO_C_LIVE_TABLE,
        strategy_name=NO_C_STRATEGY,
    )
    return _summarize_trades(closed, strategy=NO_C_STRATEGY, mode="LIVE")


def format_yes_c_shadow_funnel(conn: sqlite3.Connection) -> str:
    rows = fetch_strategy_counters(conn, YES_C_SHADOW_VERSION)
    row = next((item for item in rows if item.strategy_name == YES_C_STRATEGY), None)
    if row is None:
        return "\n".join(["YES_C SHADOW FUNNEL", "", "(no data yet)"])

    return "\n".join(
        [
            "YES_C SHADOW FUNNEL",
            "",
            f"Проверок: {row.checks}",
            "",
            f"Окно открыто: {row.window_ok}",
            "",
            f"Цена прошла порог: {row.price_ok}",
            "",
            f"Готова к входу: {row.already_open_ok}",
            "",
            f"Виртуальных входов: {row.entry_success}",
            "",
            "Блокировки:",
            f"  окно: {row.blocked_by_window}",
            f"  цена: {row.blocked_by_price}",
            f"  уже открыта: {row.blocked_by_already_open}",
        ]
    )


def format_yes_c_shadow_report(conn: sqlite3.Connection) -> str:
    closed = _fetch_closed_trades(conn, table=YES_C_SHADOW_TABLE)
    summary = _summarize_trades(closed, strategy=YES_C_STRATEGY, mode="SHADOW")
    btc_up, btc_down = _btc_direction_slices(conn, closed)

    lines = [
        "YES_C SHADOW",
        "",
        f"Trades: {summary.trades}",
        "",
        f"Wins: {summary.wins}",
        "",
        f"Losses: {summary.losses}",
        "",
        f"Win Rate: {summary.win_rate:.0%}",
        "",
        f"Average PnL: {summary.avg_pnl_percent:+.1f}%",
        "",
        btc_up.direction,
        "",
        f"Trades: {btc_up.trades}",
        "",
        f"Win Rate: {btc_up.win_rate:.0%}",
        "",
        btc_down.direction,
        "",
        f"Trades: {btc_down.trades}",
        "",
        f"Win Rate: {btc_down.win_rate:.0%}",
        "",
        f"Profit Factor: {_format_profit_factor(summary.profit_factor)}",
        "",
        f"Average Holding Time: {summary.avg_holding_seconds:.0f}s",
        "",
        f"(BTC flat threshold: ${BTC_FLAT_THRESHOLD_USD:.0f})",
    ]
    return "\n".join(lines)


def format_strategy_comparison(conn: sqlite3.Connection) -> str:
    from bot.v4_shadow_stats import fetch_v4_shadow_comparison_summary

    no_c = fetch_no_c_live_summary(conn)
    yes_c = fetch_yes_c_shadow_summary(conn)
    v4 = fetch_v4_shadow_comparison_summary(conn)

    header = f"{'Strategy':<10} {'Mode':<8} {'Trades':>7} {'Win %':>7} {'Avg PnL':>9}"
    rows = [
        (
            no_c.strategy,
            no_c.mode,
            no_c.trades,
            no_c.win_rate,
            no_c.avg_pnl_percent,
        ),
        (
            yes_c.strategy,
            yes_c.mode,
            yes_c.trades,
            yes_c.win_rate,
            yes_c.avg_pnl_percent,
        ),
        (
            v4.strategy,
            v4.mode,
            v4.trades,
            v4.win_rate,
            v4.avg_pnl_percent,
        ),
    ]
    body = [
        f"{strategy:<10} {mode:<8} {trades:>7} {win_rate:>6.0%} {avg_pnl:>+8.1f}%"
        for strategy, mode, trades, win_rate, avg_pnl in rows
    ]
    return "\n".join(["Strategy Comparison", "", header, *body])


def format_yes_c_shadow_full_report(conn: sqlite3.Connection) -> str:
    return "\n".join(
        [
            format_yes_c_shadow_funnel(conn),
            "",
            format_yes_c_shadow_report(conn),
            "",
            format_strategy_comparison(conn),
        ]
    )


def log_yes_c_shadow_reports(conn: sqlite3.Connection) -> None:
    report = format_yes_c_shadow_full_report(conn)
    logger.info("\n%s", report)
