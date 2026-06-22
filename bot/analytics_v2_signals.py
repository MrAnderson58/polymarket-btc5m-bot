"""V2 signal analytics — per-strategy performance and subset scenarios."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db

STRATEGY_ORDER = ("NO_C", "YES_B", "YES_C")
SUBSET_SCENARIOS: tuple[tuple[str, ...], ...] = (
    ("NO_C",),
    ("YES_B",),
    ("YES_C",),
    ("NO_C", "YES_B"),
    ("NO_C", "YES_C"),
    ("YES_B", "YES_C"),
)


@dataclass(frozen=True)
class SignalStats:
    strategy_name: str
    trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float
    profit_share_percent: float


@dataclass(frozen=True)
class SubsetStats:
    label: str
    strategies: tuple[str, ...]
    trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float
    share_of_v2_pnl_percent: float


@dataclass(frozen=True)
class V2SignalsReport:
    overall_trades: int
    overall_total_pnl_usdc: float
    strategies: tuple[SignalStats, ...]
    ranking: tuple[SignalStats, ...]
    subsets: tuple[SubsetStats, ...]
    best_subset: SubsetStats | None


def fetch_closed_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT strategy_name, pnl_usdc, pnl_percent
        FROM early_reversion_v2_trades
        WHERE status = 'closed'
        """
    ).fetchall()


def _aggregate(trades: list[sqlite3.Row], strategy_name: str) -> SignalStats:
    count = len(trades)
    if count == 0:
        return SignalStats(
            strategy_name=strategy_name,
            trades=0,
            total_pnl_usdc=0.0,
            average_pnl_percent=0.0,
            pnl_per_trade=0.0,
            win_rate=0.0,
            profit_share_percent=0.0,
        )

    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    wins = sum(1 for p in pnl_usdcs if p > 0)
    total_pnl = sum(pnl_usdcs)

    return SignalStats(
        strategy_name=strategy_name,
        trades=count,
        total_pnl_usdc=total_pnl,
        average_pnl_percent=sum(pnl_percents) / count,
        pnl_per_trade=total_pnl / count,
        win_rate=wins / count,
        profit_share_percent=0.0,
    )


def _subset_label(strategies: tuple[str, ...]) -> str:
    return " + ".join(strategies)


def build_report(trades: list[sqlite3.Row]) -> V2SignalsReport:
    by_strategy: dict[str, list[sqlite3.Row]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategy_name"], []).append(trade)

    overall = _aggregate(trades, "ALL")
    overall_total = overall.total_pnl_usdc

    ordered_names = [
        name for name in STRATEGY_ORDER if name in by_strategy
    ] + sorted(name for name in by_strategy if name not in STRATEGY_ORDER)

    strategies: list[SignalStats] = []
    for name in ordered_names:
        stats = _aggregate(by_strategy[name], name)
        share = (
            stats.total_pnl_usdc / overall_total * 100
            if overall_total
            else 0.0
        )
        strategies.append(
            SignalStats(
                strategy_name=stats.strategy_name,
                trades=stats.trades,
                total_pnl_usdc=stats.total_pnl_usdc,
                average_pnl_percent=stats.average_pnl_percent,
                pnl_per_trade=stats.pnl_per_trade,
                win_rate=stats.win_rate,
                profit_share_percent=share,
            )
        )

    strategies_tuple = tuple(strategies)
    ranking = tuple(
        sorted(
            strategies_tuple,
            key=lambda s: (s.pnl_per_trade, s.total_pnl_usdc),
            reverse=True,
        )
    )

    subsets: list[SubsetStats] = []
    for scenario in SUBSET_SCENARIOS:
        subset_trades = [
            t for t in trades if t["strategy_name"] in scenario
        ]
        agg = _aggregate(subset_trades, _subset_label(scenario))
        subsets.append(
            SubsetStats(
                label=_subset_label(scenario),
                strategies=scenario,
                trades=agg.trades,
                total_pnl_usdc=agg.total_pnl_usdc,
                average_pnl_percent=agg.average_pnl_percent,
                pnl_per_trade=agg.pnl_per_trade,
                win_rate=agg.win_rate,
                share_of_v2_pnl_percent=(
                    agg.total_pnl_usdc / overall_total * 100
                    if overall_total
                    else 0.0
                ),
            )
        )

    subsets_tuple = tuple(subsets)
    best_subset = (
        max(subsets_tuple, key=lambda s: (s.pnl_per_trade, s.total_pnl_usdc))
        if subsets_tuple and any(s.trades for s in subsets_tuple)
        else None
    )

    return V2SignalsReport(
        overall_trades=overall.trades,
        overall_total_pnl_usdc=overall_total,
        strategies=strategies_tuple,
        ranking=ranking,
        subsets=subsets_tuple,
        best_subset=best_subset,
    )


def _format_signal_stats(stats: SignalStats) -> list[str]:
    return [
        f"  trades:            {stats.trades}",
        f"  total_pnl_usdc:    {stats.total_pnl_usdc:+.6f}",
        f"  avg_pnl_percent:   {stats.average_pnl_percent:+.4f}%",
        f"  pnl_per_trade:     {stats.pnl_per_trade:+.6f} USDC",
        f"  win_rate:          {stats.win_rate:.1%}",
        f"  profit_share:      {stats.profit_share_percent:+.2f}% of V2 total",
    ]


def format_report(report: V2SignalsReport) -> str:
    lines = [
        "=== Early Reversion V2 — Signal Analytics ===",
        "",
        f"V2 overall: {report.overall_trades} trades, "
        f"total_pnl={report.overall_total_pnl_usdc:+.6f} USDC",
        "",
    ]

    if not report.strategies:
        lines.append("(no closed trades)")
        return "\n".join(lines)

    lines.extend(["=== By strategy ===", ""])
    for stats in report.strategies:
        lines.append(f"[{stats.strategy_name}]")
        lines.extend(_format_signal_stats(stats))
        lines.append("")

    lines.extend(["=== Strategy ranking (by pnl_per_trade) ===", ""])
    for rank, stats in enumerate(report.ranking, start=1):
        lines.append(
            f"  {rank}. {stats.strategy_name:6s}  "
            f"pnl/trade={stats.pnl_per_trade:+.6f}, "
            f"total={stats.total_pnl_usdc:+.6f}, "
            f"trades={stats.trades}, win_rate={stats.win_rate:.1%}"
        )
    lines.append("")

    lines.extend(["=== Profit share ===", ""])
    for stats in sorted(
        report.strategies,
        key=lambda s: s.profit_share_percent,
        reverse=True,
    ):
        lines.append(
            f"  {stats.strategy_name:6s}  "
            f"{stats.profit_share_percent:+.2f}%  "
            f"({stats.total_pnl_usdc:+.6f} USDC)"
        )
    lines.append("")

    lines.extend(["=== Subset scenarios (if only these signals were enabled) ===", ""])
    for subset in report.subsets:
        lines.append(f"[{subset.label}]")
        lines.extend(
            [
                f"  trades:            {subset.trades}",
                f"  total_pnl_usdc:    {subset.total_pnl_usdc:+.6f}",
                f"  avg_pnl_percent:   {subset.average_pnl_percent:+.4f}%",
                f"  pnl_per_trade:     {subset.pnl_per_trade:+.6f} USDC",
                f"  win_rate:          {subset.win_rate:.1%}",
                f"  share_of_v2_pnl:   {subset.share_of_v2_pnl_percent:+.2f}%",
                "",
            ]
        )

    if report.best_subset:
        bs = report.best_subset
        lines.extend(
            [
                "=== Best subset (by pnl_per_trade) ===",
                f"  {bs.label}",
                f"  pnl_per_trade={bs.pnl_per_trade:+.6f} USDC, "
                f"total_pnl={bs.total_pnl_usdc:+.6f}, "
                f"trades={bs.trades}, win_rate={bs.win_rate:.1%}",
            ]
        )

    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        trades = fetch_closed_trades(conn)
    report = build_report(trades)
    print(format_report(report))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
