"""Simulation and discovery report rendering."""

from __future__ import annotations

from statistics import mean

from bot.research.market_behavior.quality_control import QcReport, render_qc_warnings
from bot.research.strategy_simulator.config import MIN_TRADES_FOR_RANK
from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.statistics import SimulationStats


def validate_simulation(
    stats: SimulationStats,
    trades: list[VirtualTrade],
    *,
    min_trades: int | None = None,
) -> QcReport:
    """QC checks for a single simulation run."""
    report = QcReport()
    floor = min_trades or MIN_TRADES_FOR_RANK

    if stats.trades < floor:
        report.warnings.append(
            f"sample size {stats.trades} below minimum {floor}"
        )
        report.rejected_keys.add(stats.fingerprint)

    if trades:
        pnl_ev = mean(t.pnl for t in trades)
        if abs(pnl_ev - stats.expected_value) > 1e-9:
            report.warnings.append(
                f"EV mismatch: stats={stats.expected_value:.6f} pnl_mean={pnl_ev:.6f}"
            )
            report.rejected_keys.add(stats.fingerprint)

        for t in trades:
            if t.exit_ts < t.entry_ts:
                report.warnings.append("look-ahead: exit before entry detected")
                report.rejected_keys.add(stats.fingerprint)
                break

    if stats.win_rate < 0 or stats.win_rate > 1:
        report.warnings.append(f"invalid win rate {stats.win_rate}")
        report.rejected_keys.add(stats.fingerprint)

    return report


def _format_strategy_lines(stats: SimulationStats) -> list[str]:
    s = stats.strategy
    lines = [s.direction, f"entry <={s.max_entry:.2f}"]
    if s.min_delta is not None:
        lines.append(f"delta >{s.min_delta:.0f}$")
    if s.max_delta is not None:
        lines.append(f"delta <{s.max_delta:.0f}$")
    lines.append(f"spread <{s.max_spread * 100:.0f}c")
    lines.append(f"seconds >{s.min_seconds_left}")
    lines.append(f"tp={s.tp:.2f}")
    return lines


def render_simulation_report(
    stats: SimulationStats,
    trades: list[VirtualTrade],
    *,
    min_trades: int | None = None,
) -> str:
    qc = validate_simulation(stats, trades, min_trades=min_trades)
    lines = [
        "SIMULATION",
        "=" * 40,
        stats.strategy.label,
        "",
        f"Trades {stats.trades}",
        f"WinRate {stats.win_rate:.1%}",
        f"Average Win {stats.average_win:+.2f}",
        f"Average Loss {stats.average_loss:.2f}",
        f"Profit Factor {stats.profit_factor:.2f}",
        f"Expected Value {stats.expected_value:+.3f}",
        f"Average Holding Time {stats.average_holding_seconds:.0f}s",
        f"Max Drawdown {stats.max_drawdown:.2f}",
    ]
    if stats.sharpe is not None:
        lines.append(f"Sharpe {stats.sharpe:.2f}")
    lines.extend([
        f"Longest Losing Streak {stats.longest_losing_streak}",
        f"Longest Winning Streak {stats.longest_winning_streak}",
        "",
    ])
    if not qc.ok:
        lines.append(render_qc_warnings(qc))
        lines.append("")
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)


def render_discovery_report(
    ranked: list[SimulationStats],
    *,
    min_trades: int | None = None,
) -> str:
    floor = min_trades or MIN_TRADES_FOR_RANK
    lines = ["TOP HISTORICAL STRATEGIES", "=" * 40, ""]

    accepted: list[SimulationStats] = []
    qc_all = QcReport()
    for stats in ranked:
        qc = validate_simulation(stats, [], min_trades=floor)
        if stats.fingerprint in qc.rejected_keys:
            qc_all.warnings.extend(qc.warnings)
            continue
        accepted.append(stats)

    if not qc_all.ok:
        lines.append(render_qc_warnings(qc_all))
        lines.append("")

    for i, stats in enumerate(accepted, start=1):
        lines.append(f"#{i}")
        lines.extend(_format_strategy_lines(stats))
        lines.extend([
            f"Trades {stats.trades}",
            f"WinRate {stats.win_rate:.0%}",
            f"Profit Factor {stats.profit_factor:.2f}",
            f"EV {stats.expected_value:.3f}",
            "-" * 20,
            "",
        ])

    if not accepted:
        lines.append(f"No strategies passed QC with trades >= {floor}")

    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)
