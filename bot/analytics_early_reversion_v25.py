"""Analytics for Early Reversion v2.5 paper trades."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.config import ER_V25_GRACE_PERIOD_SEC
from bot.database import connect, init_db

EXIT_REASONS = ("STOP_LOSS", "TRAILING_STOP", "TIME_STOP")
STRATEGY_ORDER = ("NO_C", "YES_B", "YES_C")


@dataclass(frozen=True)
class ExitReasonDetail:
    reason: str
    count: int
    average_pnl_percent: float


@dataclass(frozen=True)
class TimeStopDetail:
    count: int
    average_pnl_percent: float
    median_pnl_percent: float
    max_pnl_percent: float
    min_pnl_percent: float


@dataclass(frozen=True)
class TrailingStopDetail:
    count: int
    average_profit_left_on_table: float
    median_profit_left_on_table: float


@dataclass(frozen=True)
class StrategyExtendedStats:
    strategy_name: str
    exit_reasons: tuple[ExitReasonDetail, ...]
    time_stop: TimeStopDetail
    trailing_stop: TrailingStopDetail


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
    average_max_profit_percent: float
    average_max_drawdown_percent: float
    exit_reasons: tuple[ExitReasonBreakdown, ...]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _profit_left_on_table(trade: sqlite3.Row) -> float:
    max_price = float(trade["max_price_seen"] or 0)
    exit_price = float(trade["exit_price"] or 0)
    if max_price <= 0:
        return 0.0
    return (max_price - exit_price) / max_price * 100


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
        FROM early_reversion_v25_trades
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


def _exit_reason_details(trades: list[sqlite3.Row]) -> tuple[ExitReasonDetail, ...]:
    result: list[ExitReasonDetail] = []
    for reason in EXIT_REASONS:
        group = [t for t in trades if t["exit_reason"] == reason]
        pnls = [float(t["pnl_percent"] or 0) for t in group]
        result.append(
            ExitReasonDetail(
                reason=reason,
                count=len(group),
                average_pnl_percent=sum(pnls) / len(pnls) if pnls else 0.0,
            )
        )
    return tuple(result)


def _time_stop_detail(trades: list[sqlite3.Row]) -> TimeStopDetail:
    group = [t for t in trades if t["exit_reason"] == "TIME_STOP"]
    pnls = [float(t["pnl_percent"] or 0) for t in group]
    if not pnls:
        return TimeStopDetail(
            count=0,
            average_pnl_percent=0.0,
            median_pnl_percent=0.0,
            max_pnl_percent=0.0,
            min_pnl_percent=0.0,
        )
    return TimeStopDetail(
        count=len(pnls),
        average_pnl_percent=sum(pnls) / len(pnls),
        median_pnl_percent=_median(pnls),
        max_pnl_percent=max(pnls),
        min_pnl_percent=min(pnls),
    )


def _trailing_stop_detail(trades: list[sqlite3.Row]) -> TrailingStopDetail:
    group = [t for t in trades if t["exit_reason"] == "TRAILING_STOP"]
    left_on_table = [_profit_left_on_table(t) for t in group]
    if not left_on_table:
        return TrailingStopDetail(
            count=0,
            average_profit_left_on_table=0.0,
            median_profit_left_on_table=0.0,
        )
    return TrailingStopDetail(
        count=len(left_on_table),
        average_profit_left_on_table=sum(left_on_table) / len(left_on_table),
        median_profit_left_on_table=_median(left_on_table),
    )


def _build_extended_stats(trades: list[sqlite3.Row]) -> list[StrategyExtendedStats]:
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategy_name"], []).append(trade)

    ordered_names = [
        name for name in STRATEGY_ORDER if name in by_strategy
    ] + sorted(name for name in by_strategy if name not in STRATEGY_ORDER)

    return [
        StrategyExtendedStats(
            strategy_name=name,
            exit_reasons=_exit_reason_details(by_strategy[name]),
            time_stop=_time_stop_detail(by_strategy[name]),
            trailing_stop=_trailing_stop_detail(by_strategy[name]),
        )
        for name in ordered_names
    ]


def build_report(
    trades: list[sqlite3.Row],
) -> tuple[list[StrategyStats], OverallStats, list[StrategyExtendedStats]]:
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
        exit_reasons=_exit_reason_breakdown(trades),
    )
    return strategy_stats, overall, _build_extended_stats(trades)


def _format_extended_stats(extended: list[StrategyExtendedStats]) -> list[str]:
    lines = ["=== Extended analytics by strategy ===", ""]
    if not extended:
        lines.append("(no closed trades)")
        return lines

    for stats in extended:
        lines.append(f"[{stats.strategy_name}]")
        lines.append("  Exit reason breakdown:")
        for item in stats.exit_reasons:
            lines.append(
                f"    {item.reason:14s}  count={item.count:3d},  "
                f"avg_pnl_percent={item.average_pnl_percent:+.4f}%"
            )

        ts = stats.time_stop
        lines.append(f"  TIME_STOP detail (n={ts.count}):")
        if ts.count:
            lines.extend(
                [
                    f"    avg_pnl_percent:    {ts.average_pnl_percent:+.4f}%",
                    f"    median_pnl_percent: {ts.median_pnl_percent:+.4f}%",
                    f"    max_pnl_percent:    {ts.max_pnl_percent:+.4f}%",
                    f"    min_pnl_percent:    {ts.min_pnl_percent:+.4f}%",
                ]
            )
        else:
            lines.append("    (no TIME_STOP trades)")

        tr = stats.trailing_stop
        lines.append(
            f"  TRAILING_STOP — profit left on table (n={tr.count}):"
        )
        if tr.count:
            lines.extend(
                [
                    f"    average: {tr.average_profit_left_on_table:.4f}%",
                    f"    median:  {tr.median_profit_left_on_table:.4f}%",
                ]
            )
        else:
            lines.append("    (no TRAILING_STOP trades)")

        lines.append("")

    return lines


def format_report(
    strategies: list[StrategyStats],
    overall: OverallStats,
    extended: list[StrategyExtendedStats],
) -> str:
    lines = [
        "=== Early Reversion v2.5 Analytics ===",
        f"grace_period_sec: {ER_V25_GRACE_PERIOD_SEC}",
        "",
    ]

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

    lines.append("=== Exit reasons ===")
    for item in overall.exit_reasons:
        lines.append(
            f"  {item.reason}: count={item.count}, avg_pnl_percent={item.average_pnl_percent:+.4f}%"
        )
    lines.append("")

    lines.extend(
        [
            "=== Overall ===",
            f"  trades:                        {overall.trades}",
            f"  win_rate:                      {overall.win_rate:.1%}",
            f"  average_pnl_percent:           {overall.average_pnl_percent:+.4f}%",
            f"  total_pnl_usdc:                {overall.total_pnl_usdc:+.6f}",
            f"  average_holding_time_seconds:  {overall.average_holding_time_seconds:.1f}",
            f"  average_max_profit_percent:    {overall.average_max_profit_percent:.4f}%",
            f"  average_max_drawdown_percent:  {overall.average_max_drawdown_percent:.4f}%",
            "",
        ]
    )
    lines.extend(_format_extended_stats(extended))
    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_closed_trades(conn)
    strategies, overall, extended = build_report(trades)
    print(format_report(strategies, overall, extended))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
