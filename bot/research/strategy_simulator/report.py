"""Simulation and discovery report rendering."""

from __future__ import annotations

from statistics import mean

from bot.research.market_behavior.quality_control import QcReport, render_qc_warnings
from bot.research.strategy_simulator.config import MIN_TRADES_FOR_RANK
from bot.research.strategy_simulator.deduplication import StrategyFamily
from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.splits import MarketSplit
from bot.research.strategy_simulator.split_diagnostics import SplitDiagnosticsReport
from bot.research.strategy_simulator.cost_model import CostScenarioResult
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
        lines.append("COST STRESS (test, same trade set)")
        for name, cm in r.cost_metrics_same_set.items():
            lines.append(f"  {name}: EV {cm.expected_value:+.4f} PF {cm.profit_factor:.2f}")
        lines.append("COST STRESS (test, with fill filtering)")
        for name, cm in r.cost_metrics.items():
            counts = r.cost_trade_counts.get(name, {})
            filt_n = counts.get("filtered", 0)
            in_n = counts.get("in", 0)
            extra = ""
            if in_n and filt_n < in_n:
                extra = f" [filtered {in_n - filt_n}/{in_n} trades]"
            lines.append(
                f"  {name}: EV {cm.expected_value:+.4f} PF {cm.profit_factor:.2f}{extra}"
            )
        if r.reject_reasons:
            lines.append(f"Reject: {'; '.join(r.reject_reasons)}")
        lines.extend(["", "-" * 20, ""])
    lines.append("Observe-only. No execution impact.")
    return "\n".join(lines)


def _fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "n/a"
    return str(ts)


def render_split_diagnostics_report(report: SplitDiagnosticsReport) -> str:
    lines = ["SPLIT DIAGNOSTICS", "=" * 40, ""]

    global_audit = report.audits.get("GLOBAL")
    if global_audit:
        lines.append("SPLIT CORRECTNESS")
        lines.extend(global_audit.assertions)
        lines.append("")

    for split_name in ("TRAIN", "VALIDATION", "TEST"):
        dq = report.data_quality[split_name]
        rg = report.regime[split_name]
        v4 = report.v4_summary[split_name]
        audit = report.audits.get(split_name)

        lines.append(f"=== {split_name} ===")
        lines.append(f"markets {dq.market_count}")
        lines.append(f"first market ts {_fmt_ts(dq.first_market_ts)}")
        lines.append(f"last market ts {_fmt_ts(dq.last_market_ts)}")
        if audit:
            lines.append(f"min market ts {_fmt_ts(audit.min_market_ts)}")
            lines.append(f"max market ts {_fmt_ts(audit.max_market_ts)}")
        lines.append(f"median obs/market {dq.obs_per_market_median:.0f}")
        lines.append(
            f"obs p10/p25/p50/p75/p90 "
            f"{dq.obs_per_market_p10:.0f}/"
            f"{dq.obs_per_market_p25:.0f}/"
            f"{dq.obs_per_market_p50:.0f}/"
            f"{dq.obs_per_market_p75:.0f}/"
            f"{dq.obs_per_market_p90:.0f}"
        )
        lines.append(f"median first seconds_left {dq.first_seconds_left_median:.0f}")
        lines.append(f"median last seconds_left {dq.last_seconds_left_median:.0f}")
        for t, pct in dq.seconds_left_coverage.items():
            lines.append(f"markets with obs >= {t}s: {pct:.1f}%")
        lines.append(f"YES quote completeness {dq.yes_quote_completeness:.1f}%")
        lines.append(f"NO quote completeness {dq.no_quote_completeness:.1f}%")
        lines.append(f"BTC delta completeness {dq.btc_delta_completeness:.1f}%")
        lines.append(f"spread completeness {dq.spread_completeness:.1f}%")
        lines.append(f"median snapshot interval {dq.snapshot_interval_median:.1f}s")
        for gt, pct in dq.gap_pct_over.items():
            lines.append(f"markets with gap > {gt}s: {pct:.1f}%")
        lines.append(f"v4 rows {v4['rows']} avg rows/market {v4['avg_rows_per_market']:.1f}")
        lines.append("REGIME")
        lines.append(f"  mean |final_delta| {rg.mean_abs_final_delta:.2f}")
        lines.append(f"  median |final_delta| {rg.median_abs_final_delta:.2f}")
        for k, v in rg.close_abs_delta_pct.items():
            lines.append(f"  close |delta| {k}: {v:.1f}%")
        lines.append(
            f"  YES ask p25/p50/p75 {rg.yes_ask_p25:.2f}/{rg.yes_ask_p50:.2f}/{rg.yes_ask_p75:.2f}"
        )
        lines.append(
            f"  NO ask p25/p50/p75 {rg.no_ask_p25:.2f}/{rg.no_ask_p50:.2f}/{rg.no_ask_p75:.2f}"
        )
        lines.append(f"  median spread {rg.median_spread:.4f}")
        lines.append(f"  spread <=1c {rg.spread_le_1c_pct:.1f}%")
        lines.append(f"  spread <=2c {rg.spread_le_2c_pct:.1f}%")
        lines.append(f"  spread <=5c {rg.spread_le_5c_pct:.1f}%")
        lines.append("")

    lines.append("OBSERVATION DENSITY BY CHRONOLOGICAL DECILE")
    for d, n_mkts, med in report.deciles:
        lines.append(f"Decile {d}: markets {n_mkts}, median obs {med:.0f}")
    if report.density_warnings:
        lines.append("DENSITY DISCONTINUITIES:")
        lines.extend(f"  ! {w}" for w in report.density_warnings)
    lines.append("")

    lines.append("SIGNAL OPPORTUNITY FUNNEL (walk-forward top strategies)")
    for s in report.strategies:
        lines.append(f"Strategy #{s.index}")
        lines.extend(s.strategy.predicate_lines())
        for label, funnel, trades in (
            ("TRAIN", s.train, s.train_trades),
            ("VALIDATION", s.validation, s.validation_trades),
            ("TEST", s.test, s.test_trades),
        ):
            lines.append(f"{label}:")
            lines.append(f"  markets {funnel.markets_scanned}")
            lines.append(f"  entry opportunity {funnel.entry_opportunity}")
            lines.append(f"  delta opportunity {funnel.delta_opportunity}")
            lines.append(f"  time observable {funnel.time_observable}")
            lines.append(f"  spread opportunity {funnel.spread_opportunity}")
            lines.append(f"  all conditions {funnel.all_conditions}")
            lines.append(f"  trades {trades}")
        lines.append("")

    lines.append("COST MODEL CHECK (test split, same trade set vs filtered)")
    for cc in report.cost_checks:
        lines.append(f"Strategy #{cc['strategy_index']}")
        for sc in cc["scenarios"]:
            same: CostScenarioResult = sc["same_set"]
            filt: CostScenarioResult = sc["filtered"]
            lines.append(
                f"  {same.name} same-set EV {same.expected_value:+.4f} "
                f"(trades {same.trades_out})"
            )
            if filt.trades_filtered > 0:
                lines.append(
                    f"  {filt.name} filtered EV {filt.expected_value:+.4f} "
                    f"(trades {filt.trades_out}/{filt.trades_in}, "
                    f"filtered {filt.trades_filtered})"
                )
        lines.append("")

    lines.append("ROLLING OOS (5 folds, fixed strategies from train discovery)")
    for ro in report.rolling[:10]:
        lines.append(f"Strategy {ro.strategy.direction} entry<={ro.strategy.max_entry:.2f}")
        for f in ro.folds:
            lines.append(
                f"  fold {f.fold}: train {f.train_trades}/{f.train_ev:+.4f}/{f.train_pf:.2f} "
                f"test {f.test_trades}/{f.test_ev:+.4f}/{f.test_pf:.2f}"
            )
        lines.append(
            f"  positive test folds {ro.positive_test_folds}/{len(ro.folds)} "
            f"weighted OOS EV {ro.weighted_oos_ev:+.4f} "
            f"total OOS trades {ro.total_oos_trades} OOS PF {ro.oos_pf:.2f} "
            f"worst fold EV {ro.worst_fold_ev:+.4f}"
        )
        lines.append("")

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
