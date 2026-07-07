"""Human-readable reports for Discovery v2 and archetype analysis."""

from __future__ import annotations

from bot.research.strategy_simulator.discovery_v2 import DiscoverV2Report, audit_v1_grid_collapse
from bot.research.strategy_simulator.grid_v2 import grid_summary


def render_discover_v2_report(report: DiscoverV2Report) -> str:
    lines: list[str] = []
    s = report.split
    lines.append("STRATEGY DISCOVERY V2 (observe-only, leakage-safe)")
    lines.append(f"  markets: train={len(s.train)} validation={len(s.validation)} test={len(s.test)}")
    lines.append(f"  v2 grid strategies: {report.grid_total}")
    lines.append(f"  train candidates (diverse): {report.train_candidates}")
    lines.append(f"  validation gated: {report.validation_gated}")
    lines.append("")

    audit = report.grid_audit
    lines.append("V1 GRID COLLAPSE AUDIT")
    lines.append(f"  v1 grid size: {audit['v1_total']} (YES={audit['v1_yes']} NO={audit['v1_no']})")
    lines.append(f"  cheap-YES positive-delta variants: {audit['v1_yes_cheap_positive_delta']}")
    for cause in audit["collapse_causes"]:
        lines.append(f"  - {cause}")
    lines.append("")

    gs = grid_summary()
    lines.append("V2 GRID COMPOSITION")
    lines.append(f"  total: {gs['total']}")
    for arch, count in sorted(gs["by_archetype"].items()):
        lines.append(f"    {arch}: {count}")
    lines.append(f"  YES={gs['by_direction']['YES']} NO={gs['by_direction']['NO']}")
    lines.append("")

    r = report.regime
    lines.append("REGIME DIAGNOSTICS (train vs validation)")
    lines.append(
        f"  BTC |delta| median: train={r.train.median_abs_final_delta:.1f} "
        f"val={r.validation.median_abs_final_delta:.1f} "
        f"test={r.test.median_abs_final_delta:.1f}"
    )
    lines.append(
        f"  spread median: train={r.train.median_spread:.4f} "
        f"val={r.validation.median_spread:.4f}"
    )
    lines.append(f"  assessment: {', '.join(r.assessment.likely_causes)}")
    lines.append("")

    lines.append("TOP CANDIDATES (ranked on TRAIN, gated on VALIDATION, TEST evaluated once)")
    lines.append(
        f"  {'#':>2}  {'arch':<18} {'dir':<3} {'exit':<12} "
        f"{'trainEV':>8} {'valEV':>8} {'testEV':>8} {'testN':>6} {'valOK':>5}"
    )
    for res in report.results:
        st = res.strategy
        lines.append(
            f"  {res.train_rank:2d}  {st.archetype:<18} {st.direction:<3} "
            f"{st.exit_spec.label():<12} "
            f"{res.train.expected_value:8.4f} {res.validation.expected_value:8.4f} "
            f"{res.test.expected_value:8.4f} {res.test.trades:6d} "
            f"{'Y' if res.validation_passed else 'N':>5}"
        )
        if res.reject_reasons:
            lines.append(f"       reject: {', '.join(res.reject_reasons)}")
        if res.rolling:
            ro = res.rolling
            lines.append(
                f"       rolling: folds={len(ro.folds)} pos={ro.positive_test_folds} "
                f"wOOS_EV={ro.weighted_oos_ev:.4f} worst={ro.worst_fold_ev:.4f} "
                f"mkts={ro.distinct_markets}"
            )
        lines.append(
            f"       costs: base_EV={res.cost_base_ev:.4f} stress_EV={res.cost_stress_ev:.4f} "
            f"bootstrap_CI=[{res.bootstrap.ev_ci_low:.4f},{res.bootstrap.ev_ci_high:.4f}]"
        )
    lines.append("")
    lines.append("NOTE: No forward candidates registered. Observe-only research.")
    return "\n".join(lines)


def render_archetype_report() -> str:
    """Static archetype grid audit (no DB required)."""
    audit = audit_v1_grid_collapse()
    gs = grid_summary()
    lines = [
        "ARCHETYPE GRID REPORT",
        "",
        "V1 collapse root causes:",
    ]
    for cause in audit["collapse_causes"]:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append(f"V2 grid total: {gs['total']}")
    for arch, count in sorted(gs["by_archetype"].items()):
        lines.append(f"  {arch}: {count} strategies")
    lines.append(f"  YES: {gs['by_direction']['YES']}  NO: {gs['by_direction']['NO']}")
    lines.append("")
    lines.append("Archetype families:")
    lines.append("  A. momentum — delta direction aligned, velocity + confirmation")
    lines.append("  B. mean_reversion — extreme |delta|, reversal velocity")
    lines.append("  C. late_convergence — low seconds_left, distance/vol normalized")
    lines.append("  D. spread_dislocation — wide spread, complement gap")
    lines.append("  E. legacy_cheap — v1 baseline for comparison")
    lines.append("")
    lines.append("Exit models: fixed_tp, time_exit, settlement, trailing")
    lines.append("Entry: normalized ASK. Exit: normalized BID or settlement.")
    return "\n".join(lines)
