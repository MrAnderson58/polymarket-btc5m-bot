"""Analytics for Early Reversion paper trades."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db


@dataclass(frozen=True)
class StrategyStats:
    strategy_name: str
    trades: int
    hits: int
    hit_rate: float
    average_pnl_percent: float
    total_pnl_percent: float
    total_pnl_usdc: float
    average_holding_time_seconds: float
    average_max_profit_percent: float
    average_max_drawdown_percent: float


@dataclass(frozen=True)
class OverallStats:
    trades: int
    hits: int
    hit_rate: float
    average_pnl_percent: float
    total_pnl_percent: float
    total_pnl_usdc: float
    average_holding_time_seconds: float
    average_max_profit_percent: float
    average_max_drawdown_percent: float


def fetch_closed_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM early_reversion_trades
        WHERE status = 'closed'
        ORDER BY strategy_name ASC, closed_at ASC
        """
    ).fetchall()


def _aggregate(trades: list[sqlite3.Row], strategy_name: str) -> StrategyStats:
    count = len(trades)
    hits = sum(1 for t in trades if t["reached_target"])
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]
    holding_times = [float(t["holding_time_seconds"] or 0) for t in trades]
    max_profits = [float(t["max_profit_percent"] or 0) for t in trades]
    max_drawdowns = [float(t["max_drawdown_percent"] or 0) for t in trades]

    return StrategyStats(
        strategy_name=strategy_name,
        trades=count,
        hits=hits,
        hit_rate=hits / count if count else 0.0,
        average_pnl_percent=sum(pnl_percents) / count if count else 0.0,
        total_pnl_percent=sum(pnl_percents),
        total_pnl_usdc=sum(pnl_usdcs),
        average_holding_time_seconds=sum(holding_times) / count if count else 0.0,
        average_max_profit_percent=sum(max_profits) / count if count else 0.0,
        average_max_drawdown_percent=sum(max_drawdowns) / count if count else 0.0,
    )


def build_report(trades: list[sqlite3.Row]) -> tuple[list[StrategyStats], OverallStats]:
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategy_name"], []).append(trade)

    strategy_stats = [
        _aggregate(group, name)
        for name, group in by_strategy.items()
    ]
    strategy_stats.sort(key=lambda s: s.total_pnl_usdc, reverse=True)

    overall = _aggregate(trades, "ALL")
    overall_stats = OverallStats(
        trades=overall.trades,
        hits=overall.hits,
        hit_rate=overall.hit_rate,
        average_pnl_percent=overall.average_pnl_percent,
        total_pnl_percent=overall.total_pnl_percent,
        total_pnl_usdc=overall.total_pnl_usdc,
        average_holding_time_seconds=overall.average_holding_time_seconds,
        average_max_profit_percent=overall.average_max_profit_percent,
        average_max_drawdown_percent=overall.average_max_drawdown_percent,
    )
    return strategy_stats, overall_stats


def format_report(
    strategies: list[StrategyStats],
    overall: OverallStats,
) -> str:
    lines = ["=== Early Reversion Analytics ===", ""]

    if not strategies:
        lines.append("(no closed trades)")
        return "\n".join(lines)

    for stats in strategies:
        lines.extend(
            [
                f"[{stats.strategy_name}]",
                f"  trades:                        {stats.trades}",
                f"  hits:                          {stats.hits}",
                f"  hit_rate:                      {stats.hit_rate:.1%}",
                f"  average_pnl_percent:           {stats.average_pnl_percent:+.4f}%",
                f"  total_pnl_percent:             {stats.total_pnl_percent:+.4f}%",
                f"  total_pnl_usdc:                {stats.total_pnl_usdc:+.6f}",
                f"  average_holding_time_seconds:  {stats.average_holding_time_seconds:.1f}",
                f"  average_max_profit_percent:    {stats.average_max_profit_percent:.4f}%",
                f"  average_max_drawdown_percent:  {stats.average_max_drawdown_percent:.4f}%",
                "",
            ]
        )

    lines.extend(
        [
            "=== Overall ===",
            f"  trades:                        {overall.trades}",
            f"  hits:                          {overall.hits}",
            f"  hit_rate:                      {overall.hit_rate:.1%}",
            f"  average_pnl_percent:           {overall.average_pnl_percent:+.4f}%",
            f"  total_pnl_percent:             {overall.total_pnl_percent:+.4f}%",
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
