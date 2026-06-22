"""Compare Early Reversion V2, V2.5, and V3 paper trading results."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bot.database import connect, init_db

VERSIONS: tuple[tuple[str, str], ...] = (
    ("V2", "early_reversion_v2_trades"),
    ("V2.5", "early_reversion_v25_trades"),
    ("V3", "early_reversion_v3_trades"),
)
STRATEGIES = ("NO_C", "YES_B", "YES_C")


@dataclass(frozen=True)
class VersionStats:
    version: str
    trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float


@dataclass(frozen=True)
class StrategyVersionCell:
    trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float


@dataclass(frozen=True)
class StrategyCrossVersionStats:
    strategy_name: str
    by_version: dict[str, StrategyVersionCell]
    total_trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float


@dataclass(frozen=True)
class ComboStats:
    version: str
    strategy_name: str
    trades: int
    total_pnl_usdc: float
    average_pnl_percent: float
    pnl_per_trade: float
    win_rate: float


@dataclass(frozen=True)
class CompareReport:
    versions: tuple[VersionStats, ...]
    strategies: tuple[StrategyCrossVersionStats, ...]
    combos: tuple[ComboStats, ...]
    best_version: VersionStats
    best_strategy: StrategyCrossVersionStats
    best_combo: ComboStats


def _aggregate_trades(trades: list[sqlite3.Row]) -> StrategyVersionCell:
    count = len(trades)
    if count == 0:
        return StrategyVersionCell(
            trades=0,
            total_pnl_usdc=0.0,
            average_pnl_percent=0.0,
            pnl_per_trade=0.0,
            win_rate=0.0,
        )

    pnl_usdcs = [float(t["pnl_usdc"] or 0) for t in trades]
    pnl_percents = [float(t["pnl_percent"] or 0) for t in trades]
    wins = sum(1 for p in pnl_usdcs if p > 0)
    total_pnl = sum(pnl_usdcs)

    return StrategyVersionCell(
        trades=count,
        total_pnl_usdc=total_pnl,
        average_pnl_percent=sum(pnl_percents) / count,
        pnl_per_trade=total_pnl / count,
        win_rate=wins / count,
    )


def _fetch_closed_trades(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT strategy_name, pnl_usdc, pnl_percent
        FROM {table}
        WHERE status = 'closed'
        """
    ).fetchall()


def build_report(conn: sqlite3.Connection) -> CompareReport:
    version_stats: list[VersionStats] = []
    all_trades_by_version: dict[str, list[sqlite3.Row]] = {}

    for version, table in VERSIONS:
        trades = _fetch_closed_trades(conn, table)
        all_trades_by_version[version] = trades
        cell = _aggregate_trades(trades)
        version_stats.append(
            VersionStats(
                version=version,
                trades=cell.trades,
                total_pnl_usdc=cell.total_pnl_usdc,
                average_pnl_percent=cell.average_pnl_percent,
                pnl_per_trade=cell.pnl_per_trade,
                win_rate=cell.win_rate,
            )
        )

    strategy_stats: list[StrategyCrossVersionStats] = []
    combos: list[ComboStats] = []

    for strategy_name in STRATEGIES:
        by_version: dict[str, StrategyVersionCell] = {}
        all_strategy_trades: list[sqlite3.Row] = []

        for version, table in VERSIONS:
            trades = [
                t
                for t in all_trades_by_version[version]
                if t["strategy_name"] == strategy_name
            ]
            cell = _aggregate_trades(trades)
            by_version[version] = cell
            all_strategy_trades.extend(trades)

            if cell.trades:
                combos.append(
                    ComboStats(
                        version=version,
                        strategy_name=strategy_name,
                        trades=cell.trades,
                        total_pnl_usdc=cell.total_pnl_usdc,
                        average_pnl_percent=cell.average_pnl_percent,
                        pnl_per_trade=cell.pnl_per_trade,
                        win_rate=cell.win_rate,
                    )
                )

        total = _aggregate_trades(all_strategy_trades)
        strategy_stats.append(
            StrategyCrossVersionStats(
                strategy_name=strategy_name,
                by_version=by_version,
                total_trades=total.trades,
                total_pnl_usdc=total.total_pnl_usdc,
                average_pnl_percent=total.average_pnl_percent,
                pnl_per_trade=total.pnl_per_trade,
                win_rate=total.win_rate,
            )
        )

    versions_tuple = tuple(version_stats)
    strategies_tuple = tuple(strategy_stats)
    combos_tuple = tuple(combos)

    if not combos_tuple:
        empty_version = VersionStats("—", 0, 0.0, 0.0, 0.0, 0.0)
        empty_strategy = StrategyCrossVersionStats(
            strategy_name="—",
            by_version={v: StrategyVersionCell(0, 0.0, 0.0, 0.0, 0.0) for v, _ in VERSIONS},
            total_trades=0,
            total_pnl_usdc=0.0,
            average_pnl_percent=0.0,
            pnl_per_trade=0.0,
            win_rate=0.0,
        )
        empty_combo = ComboStats("—", "—", 0, 0.0, 0.0, 0.0, 0.0)
        return CompareReport(
            versions=versions_tuple,
            strategies=strategies_tuple,
            combos=combos_tuple,
            best_version=empty_version,
            best_strategy=empty_strategy,
            best_combo=empty_combo,
        )

    best_version = max(versions_tuple, key=lambda v: (v.pnl_per_trade, v.total_pnl_usdc))
    best_strategy = max(strategies_tuple, key=lambda s: (s.pnl_per_trade, s.total_pnl_usdc))
    best_combo = max(combos_tuple, key=lambda c: (c.pnl_per_trade, c.total_pnl_usdc))

    return CompareReport(
        versions=versions_tuple,
        strategies=strategies_tuple,
        combos=combos_tuple,
        best_version=best_version,
        best_strategy=best_strategy,
        best_combo=best_combo,
    )


def _format_version_row(stats: VersionStats) -> list[str]:
    return [
        f"  total_trades:      {stats.trades}",
        f"  total_pnl_usdc:    {stats.total_pnl_usdc:+.6f}",
        f"  avg_pnl_percent:   {stats.average_pnl_percent:+.4f}%",
        f"  pnl_per_trade:     {stats.pnl_per_trade:+.6f} USDC",
        f"  win_rate:          {stats.win_rate:.1%}",
    ]


def _format_cell(cell: StrategyVersionCell) -> str:
    if cell.trades == 0:
        return "—"
    return (
        f"n={cell.trades}, pnl={cell.total_pnl_usdc:+.3f}, "
        f"avg={cell.average_pnl_percent:+.1f}%, "
        f"$/trade={cell.pnl_per_trade:+.4f}, wr={cell.win_rate:.0%}"
    )


def format_report(report: CompareReport) -> str:
    lines = [
        "=== Early Reversion — Version Comparison ===",
        "",
        "=== By version ===",
        "",
    ]

    for stats in report.versions:
        lines.append(f"[{stats.version}]")
        lines.extend(_format_version_row(stats))
        lines.append("")

    lines.extend(["=== By strategy (all versions combined) ===", ""])
    for stats in report.strategies:
        lines.append(f"[{stats.strategy_name}]")
        lines.append(f"  total (all versions):")
        lines.extend(
            [
                f"    trades:            {stats.total_trades}",
                f"    total_pnl_usdc:    {stats.total_pnl_usdc:+.6f}",
                f"    avg_pnl_percent:   {stats.average_pnl_percent:+.4f}%",
                f"    pnl_per_trade:     {stats.pnl_per_trade:+.6f} USDC",
                f"    win_rate:          {stats.win_rate:.1%}",
            ]
        )
        lines.append("  per version:")
        for version, _ in VERSIONS:
            lines.append(f"    {version:5s}  {_format_cell(stats.by_version[version])}")
        lines.append("")

    lines.extend(["=== Rankings ===", ""])
    if not report.combos:
        lines.append("(no closed trades)")
        return "\n".join(lines)

    bv = report.best_version
    lines.extend(
        [
            f"1. Best version:  {bv.version}",
            f"   pnl_per_trade={bv.pnl_per_trade:+.6f} USDC, "
            f"total_pnl={bv.total_pnl_usdc:+.6f}, trades={bv.trades}, win_rate={bv.win_rate:.1%}",
            "",
        ]
    )

    bs = report.best_strategy
    lines.extend(
        [
            f"2. Best strategy: {bs.strategy_name}",
            f"   pnl_per_trade={bs.pnl_per_trade:+.6f} USDC, "
            f"total_pnl={bs.total_pnl_usdc:+.6f}, trades={bs.total_trades}, win_rate={bs.win_rate:.1%}",
            "",
        ]
    )

    bc = report.best_combo
    lines.extend(
        [
            f"3. Best combo:    {bc.version} + {bc.strategy_name}",
            f"   pnl_per_trade={bc.pnl_per_trade:+.6f} USDC, "
            f"total_pnl={bc.total_pnl_usdc:+.6f}, trades={bc.trades}, win_rate={bc.win_rate:.1%}",
            "",
            "=== Recommended configuration ===",
            f"  {bc.version} / {bc.strategy_name}",
        ]
    )

    return "\n".join(lines)


def print_report() -> None:
    init_db()
    with connect() as conn:
        report = build_report(conn)
    print(format_report(report))


def main() -> None:
    print_report()


if __name__ == "__main__":
    sys.exit(main() or 0)
