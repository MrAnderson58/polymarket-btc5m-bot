"""Simulation and discovery report rendering."""

from __future__ import annotations

from statistics import mean

from bot.research.market_behavior.quality_control import QcReport, render_qc_warnings
from bot.research.strategy_simulator.config import MIN_TRADES_FOR_RANK
from bot.research.strategy_simulator.deduplication import StrategyFamily
from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.splits import MarketSplit
from bot.research.strategy_simulator.statistics import SimulationStats
from bot.research.strategy_simulator.walk_forward import WalkForwardResult


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
    return stats.strategy.predicate_lines()


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
        *stats.strategy.predicate_lines(),
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
    families: list[StrategyFamily] | None = None,
) -> str:
    floor = min_trades or MIN_TRADES_FOR_RANK
    lines = ["TOP HISTORICAL STRATEGIES", "=" * 40, ""]

    family_by_fp = {f.representative.fingerprint: f for f in (families or [])}

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
        family = family_by_fp.get(stats.fingerprint)
        if family and family.family_size > 1:
            lines.append(f"Family size {family.family_size} (equiv. spreads {family.spread_range})")
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


def _split_block(name: str, m) -> list[str]:
    return [
        name,
        f"  trades {m.trades}",
        f"  win_rate {m.win_rate:.1%}",
        f"  PF {m.profit_factor:.2f}",
        f"  EV {m.expected_value:+.4f}",
        f"  max_drawdown {m.max_drawdown:.2f}",
    ]


def render_walk_forward_report(
    split: MarketSplit,
    results: list[WalkForwardResult],
) -> str:
    lines = [
        "WALK-FORWARD VALIDATION",
        "=" * 40,
        f"Train markets {len(split.train)} | Validation {len(split.validation)} | Test {len(split.test)}",
        "",
    ]
    for i, r in enumerate(results, start=1):
        lines.append(f"#{i} {'QUALIFIED' if r.qualified else 'REJECTED'}")
        lines.extend(r.strategy.predicate_lines())
        if r.family and r.family.family_size > 1:
            lines.append(
                f"Family {r.family.family_size} equiv. spreads {r.family.spread_range}"
            )
        lines.append("")
        lines.extend(_split_block("TRAIN", r.train))
        lines.extend(_split_block("VALIDATION", r.validation))
        lines.extend(_split_block("TEST", r.test))
        lines.append("STABILITY")
        lines.append(f"  EV retention val/train {r.stability.ev_retention_validation_train}")
        lines.append(f"  EV retention test/train {r.stability.ev_retention_test_train}")
        lines.append(f"  PF retention val {r.stability.pf_retention_validation}")
        lines.append(f"  PF retention test {r.stability.pf_retention_test}")
        lines.append("BOOTSTRAP (test markets)")
        lines.append(f"  EV mean {r.bootstrap.ev_mean:+.4f}")
        lines.append(f"  EV 95% CI [{r.bootstrap.ev_ci_low:+.4f}, {r.bootstrap.ev_ci_high:+.4f}]")
        lines.append(f"  WinRate 95% CI [{r.bootstrap.win_rate_ci_low:.1%}, {r.bootstrap.win_rate_ci_high:.1%}]")
        lines.append(f"  PF median {r.bootstrap.pf_median:.2f} CI [{r.bootstrap.pf_ci_low:.2f}, {r.bootstrap.pf_ci_high:.2f}]")
        lines.append(f"  P(EV>0) {r.bootstrap.prob_ev_positive:.1%}")
        lines.append("COST STRESS (test)")
        for name, cm in r.cost_metrics.items():
            lines.append(f"  {name}: EV {cm.expected_value:+.4f} PF {cm.profit_factor:.2f}")
        if r.reject_reasons:
            lines.append(f"Reject: {'; '.join(r.reject_reasons)}")
        lines.extend(["", "-" * 20, ""])
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)


def render_finalists_report(rows: list[dict]) -> str:
    lines = ["SHADOW CANDIDATES", "=" * 40, ""]
    if not rows:
        lines.append("No shadow candidates stored.")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            f"#{row['id']} fp={row['strategy_fp']} "
            f"qualified={row['qualified']} shadow={row['enabled_shadow']} "
            f"created={row['created_at']}"
        )
    lines.append("")
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)
