"""Analytics for Early Reversion v2 paper trades."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db


@dataclass(frozen=True)
class StrategyStats:
    strategy_name: str
    trades: int
    win_rate: float
    average_pnl_percent: float
    total_pnl_usdc: float
    average_holding_time_seconds: float
    average_max_profit_percent: float
    average_max_drawdown_percent: float


@dataclass(frozen=True)
class ExitReasonStats:
    stop_loss: int
    trailing_stop: int
    time_stop: int


@dataclass(frozen=True)
class OverallStats:
    trades: int
    win_rate: float
    average_pnl_percent: float
    total_pnl_usdc: float
    average_holding_time_seconds: float
    average_max_profit_percent: float
    average_max_drawdown_percent: float
    exit_reasons: ExitReasonStats


def _max_profit_percent(trade: sqlite3.Row) -> float:
    entry = float(trade["entry_price"])
    peak = float(trade["max_price_seen"] or entry)
    return (peak - entry) / entry * 100


def _max_drawdown_percent(trade: sqlite3.Row) -> float:
    entry = float(trade["entry_price"])
    peak = float(trade["max_price_seen"] or entry)
    exit_price = float(trade["exit_price"] or entry)
    if peak > exit_price:
        return (peak - exit_price) / entry * 100
    if exit_price < entry:
        return (entry - exit_price) / entry * 100
    return 0.0


def fetch_closed_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM early_reversion_v2_trades
        WHERE status = 'closed'
        ORDER BY strategy_name ASC, closed_at ASC
        """
    ).fetchall()


def _aggregate(trades: list[sqlite3.Row], strategy_name: str) -> StrategyStats:
    count = len(trades)
    wins = sum(1 for t in trades if float(t["pnl_usdc"] or 0) > 0)
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]
    holding_times = [float(t["holding_time_seconds"] or 0) for t in trades]
    max_profits = [_max_profit_percent(t) for t in trades]
    max_drawdowns = [_max_drawdown_percent(t) for t in trades]

    return StrategyStats(
        strategy_name=strategy_name,
        trades=count,
        win_rate=wins / count if count else 0.0,
        average_pnl_percent=sum(pnl_percents) / count if count else 0.0,
        total_pnl_usdc=sum(pnl_usdcs),
        average_holding_time_seconds=sum(holding_times) / count if count else 0.0,
        average_max_profit_percent=sum(max_profits) / count if count else 0.0,
        average_max_drawdown_percent=sum(max_drawdowns) / count if count else 0.0,
    )


def _exit_reason_stats(trades: list[sqlite3.Row]) -> ExitReasonStats:
    return ExitReasonStats(
        stop_loss=sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        trailing_stop=sum(1 for t in trades if t["exit_reason"] == "TRAILING_STOP"),
        time_stop=sum(1 for t in trades if t["exit_reason"] == "TIME_STOP"),
    )


def build_report(
    trades: list[sqlite3.Row],
) -> tuple[list[StrategyStats], OverallStats]:
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategy_name"], []).append(trade)

    strategy_stats = [
        _aggregate(group, name)
        for name, group in by_strategy.items()
    ]
    strategy_stats.sort(key=lambda s: s.total_pnl_usdc, reverse=True)

    overall_agg = _aggregate(trades, "ALL")
    overall = OverallStats(
        trades=overall_agg.trades,
        win_rate=overall_agg.win_rate,
        average_pnl_percent=overall_agg.average_pnl_percent,
        total_pnl_usdc=overall_agg.total_pnl_usdc,
        average_holding_time_seconds=overall_agg.average_holding_time_seconds,
        average_max_profit_percent=overall_agg.average_max_profit_percent,
        average_max_drawdown_percent=overall_agg.average_max_drawdown_percent,
        exit_reasons=_exit_reason_stats(trades),
    )
    return strategy_stats, overall


def format_report(strategies: list[StrategyStats], overall: OverallStats) -> str:
    lines = ["=== Early Reversion v2 Analytics ===", ""]

    if not strategies:
        lines.append("(no closed trades)")
        return "\n".join(lines)

    for stats in strategies:
        lines.extend(
            [
                f"[{stats.strategy_name}]",
                f"  trades:                        {stats.trades}",
                f"  win_rate:                      {stats.win_rate:.1%}",
                f"  average_pnl_percent:           {stats.average_pnl_percent:+.4f}%",
                f"  total_pnl_usdc:                {stats.total_pnl_usdc:+.6f}",
                f"  average_holding_time_seconds:  {stats.average_holding_time_seconds:.1f}",
                f"  average_max_profit_percent:    {stats.average_max_profit_percent:.4f}%",
                f"  average_max_drawdown_percent:  {stats.average_max_drawdown_percent:.4f}%",
                "",
            ]
        )

    lines.extend(
        [
            "=== Exit reasons ===",
            f"  STOP_LOSS:      {overall.exit_reasons.stop_loss}",
            f"  TRAILING_STOP:  {overall.exit_reasons.trailing_stop}",
            f"  TIME_STOP:      {overall.exit_reasons.time_stop}",
            "",
            "=== Overall ===",
            f"  trades:                        {overall.trades}",
            f"  win_rate:                      {overall.win_rate:.1%}",
            f"  average_pnl_percent:           {overall.average_pnl_percent:+.4f}%",
            f"  total_pnl_usdc:                {overall.total_pnl_usdc:+.6f}",
            f"  average_holding_time_seconds:  {overall.average_holding_time_seconds:.1f}",
            f"  average_max_profit_percent:    {overall.average_max_profit_percent:.4f}%",
            f"  average_max_drawdown_percent:  {overall.average_max_drawdown_percent:.4f}%",
        ]
    )
    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_closed_trades(conn)
    strategies, overall = build_report(trades)
    print(format_report(strategies, overall))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
