"""Analytics for Early Reversion v3 paper trades."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db

EXIT_REASONS = ("STOP_LOSS", "TRAILING_STOP", "TIME_STOP")


@dataclass(frozen=True)
class StrategyStats:
    strategy_name: str
    trades: int
    win_rate: float
    average_pnl_percent: float
    total_pnl_usdc: float


@dataclass(frozen=True)
class ExitReasonBreakdown:
    reason: str
    count: int
    average_pnl_percent: float


@dataclass(frozen=True)
class OverallStats:
    trades: int
    win_rate: float
    average_pnl_percent: float
    total_pnl_usdc: float
    average_holding_time_seconds: float
    exit_reasons: tuple[ExitReasonBreakdown, ...]


def fetch_closed_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM early_reversion_v3_trades
        WHERE status = 'closed'
        ORDER BY strategy_name ASC, closed_at ASC
        """
    ).fetchall()


def _aggregate_strategy(trades: list[sqlite3.Row], strategy_name: str) -> StrategyStats:
    count = len(trades)
    wins = sum(1 for t in trades if float(t["pnl_usdc"] or 0) > 0)
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]

    return StrategyStats(
        strategy_name=strategy_name,
        trades=count,
        win_rate=wins / count if count else 0.0,
        average_pnl_percent=sum(pnl_percents) / count if count else 0.0,
        total_pnl_usdc=sum(pnl_usdcs),
    )


def _exit_reason_breakdown(trades: list[sqlite3.Row]) -> tuple[ExitReasonBreakdown, ...]:
    result: list[ExitReasonBreakdown] = []
    for reason in EXIT_REASONS:
        group = [t for t in trades if t["exit_reason"] == reason]
        pnls = [float(t["pnl_percent"] or 0) for t in group]
        result.append(
            ExitReasonBreakdown(
                reason=reason,
                count=len(group),
                average_pnl_percent=sum(pnls) / len(pnls) if pnls else 0.0,
            )
        )
    return tuple(result)


def _stop_loss_slippage(trades: list[sqlite3.Row]) -> tuple[float, float, float] | None:
    sl_trades = [
        t for t in trades
        if t["exit_reason"] == "STOP_LOSS" and t["stop_loss_trigger_pnl"] is not None
    ]
    if not sl_trades:
        return None
    triggers = [float(t["stop_loss_trigger_pnl"]) for t in sl_trades]
    actuals = [float(t["pnl_percent"] or 0) for t in sl_trades]
    slippage = [a - tr for a, tr in zip(actuals, triggers)]
    n = len(sl_trades)
    return sum(triggers) / n, sum(actuals) / n, sum(slippage) / n


def build_report(trades: list[sqlite3.Row]) -> tuple[list[StrategyStats], OverallStats]:
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategy_name"], []).append(trade)

    strategy_stats = [
        _aggregate_strategy(group, name)
        for name, group in by_strategy.items()
    ]
    strategy_stats.sort(key=lambda s: s.total_pnl_usdc, reverse=True)

    count = len(trades)
    wins = sum(1 for t in trades if float(t["pnl_usdc"] or 0) > 0)
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]
    holding_times = [float(t["holding_time_seconds"] or 0) for t in trades]

    overall = OverallStats(
        trades=count,
        win_rate=wins / count if count else 0.0,
        average_pnl_percent=sum(pnl_percents) / count if count else 0.0,
        total_pnl_usdc=sum(pnl_usdcs),
        average_holding_time_seconds=sum(holding_times) / count if count else 0.0,
        exit_reasons=_exit_reason_breakdown(trades),
    )
    return strategy_stats, overall


def format_report(strategies: list[StrategyStats], overall: OverallStats, trades: list[sqlite3.Row]) -> str:
    lines = ["=== Early Reversion v3 Analytics ===", ""]

    if not strategies:
        lines.append("(no closed trades)")
        return "\n".join(lines)

    for stats in strategies:
        lines.extend(
            [
                f"[{stats.strategy_name}]",
                f"  trades:               {stats.trades}",
                f"  win_rate:             {stats.win_rate:.1%}",
                f"  average_pnl_percent:  {stats.average_pnl_percent:+.4f}%",
                f"  total_pnl_usdc:       {stats.total_pnl_usdc:+.6f}",
                "",
            ]
        )

    lines.append("=== Exit reasons ===")
    for item in overall.exit_reasons:
        lines.append(f"  {item.reason}: count={item.count}, avg_pnl_percent={item.average_pnl_percent:+.4f}%")
    lines.append("")

    lines.extend(
        [
            "=== Overall ===",
            f"  trades:                        {overall.trades}",
            f"  win_rate:                      {overall.win_rate:.1%}",
            f"  average_pnl_percent:           {overall.average_pnl_percent:+.4f}%",
            f"  total_pnl_usdc:                {overall.total_pnl_usdc:+.6f}",
            f"  average_holding_time_seconds:  {overall.average_holding_time_seconds:.1f}",
        ]
    )

    slippage = _stop_loss_slippage(trades)
    if slippage:
        trigger_avg, actual_avg, slip_avg = slippage
        lines.extend(
            [
                "",
                "=== Stop loss slippage ===",
                f"  avg trigger pnl_percent:  {trigger_avg:+.4f}%",
                f"  avg actual pnl_percent:   {actual_avg:+.4f}%",
                f"  avg slippage:             {slip_avg:+.4f}%",
            ]
        )

    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_closed_trades(conn)
    strategies, overall = build_report(trades)
    print(format_report(strategies, overall, trades))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
