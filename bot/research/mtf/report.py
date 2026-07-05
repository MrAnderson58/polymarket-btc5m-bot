"""Multi-timeframe research report and verdict."""

from __future__ import annotations

from typing import Any

REPORT_WIDTH = 72

VERDICTS = (
    "NO_VALUE",
    "COLLECT_MORE_DATA",
    "USE_SPOT_CONTEXT_ONLY",
    "READY_FOR_CONTEXT_SHADOW",
    "READY_FOR_SEPARATE_15M_RESEARCH",
    "READY_FOR_SEPARATE_1H_RESEARCH",
)


def determine_verdict(
    audit: dict[str, Any],
    wf: dict[str, Any],
    hypotheses: dict[str, Any],
    predictive: dict[str, Any],
    trade_count: int,
) -> str:
    if trade_count < 30:
        return "COLLECT_MORE_DATA"

    if not audit.get("sufficient_for_model_c"):
        b_beats = wf.get("model_b_beats_a_oos", False)
        if b_beats and wf.get("models", {}).get("MODEL_B_btc_spot", {}).get("test_lift_vs_a", 0) > 0.05:
            return "USE_SPOT_CONTEXT_ONLY"
        if audit.get("snapshot_total", 0) < 50:
            return "COLLECT_MORE_DATA"
        return "COLLECT_MORE_DATA"

    c_beats_b = wf.get("model_c_beats_b_oos", False)
    if c_beats_b:
        lift = wf.get("models", {}).get("MODEL_C_btc_pm", {}).get("test_lift_vs_a", 0)
        if lift > 0.1:
            return "READY_FOR_CONTEXT_SHADOW"

    b_lift = wf.get("models", {}).get("MODEL_B_btc_spot", {}).get("test_lift_vs_a", 0)
    if b_lift > 0.08:
        return "USE_SPOT_CONTEXT_ONLY"

    pm15 = predictive.get("15m", {})
    if pm15.get("conditional_lift", 0) > 0.3 and pm15.get("coverage", 0) > 0.5:
        return "READY_FOR_SEPARATE_15M_RESEARCH"

    pm1h = predictive.get("1h", {})
    if pm1h.get("conditional_lift", 0) > 0.3 and pm1h.get("coverage", 0) > 0.3:
        return "READY_FOR_SEPARATE_1H_RESEARCH"

    if b_lift <= 0 and not c_beats_b:
        return "NO_VALUE"

    return "COLLECT_MORE_DATA"


def render_report(
    audit: dict[str, Any],
    htf_matrix: dict,
    align_matrix: dict,
    hypotheses: dict[str, Any],
    wf: dict[str, Any],
    predictive: dict[str, Any],
    tf_research: dict[str, Any],
    trade_count: int,
    verdict: str,
) -> str:
    lines: list[str] = []
    w = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * w)
        lines.append(title)
        lines.append("=" * w)

    h("MULTI-TIMEFRAME POLYMARKET CONTEXT RESEARCH")
    lines.append("5m = execution TF | 15m/1h/daily = context TF | NO look-ahead")
    lines.append(f"Corrected V1.1 trades analyzed: {trade_count}")

    h("1. DATA AUDIT")
    lines.append(f"  market_checks 5m rows: {audit.get('market_checks_5m', {}).get('n', 0)}")
    lines.append(f"  v4 observations: {audit.get('v4_observations', {}).get('n', 0)}")
    lines.append(f"  bidi closed trades: {audit.get('bidi_closed_trades', 0)}")
    lines.append(f"  MTF snapshots total: {audit.get('snapshot_total', 0)}")
    lines.append(
        f"  PM coverage: 15m={audit.get('snapshot_coverage_15m', 0):.1%} "
        f"1h={audit.get('snapshot_coverage_1h', 0):.1%} "
        f"daily={audit.get('snapshot_coverage_daily', 0):.1%}"
    )
    lines.append(f"  Gamma search hits: {audit.get('gamma_search_results', 0)}")
    if audit.get("gamma_sample_slugs"):
        lines.append(f"  Sample slugs: {', '.join(audit['gamma_sample_slugs'][:5])}")
    lines.append("  Slug patterns:")
    for tf, pat in audit.get("slug_patterns", {}).items():
        lines.append(f"    {tf}: {pat}")
    lines.append(f"  Sufficient for MODEL C: {audit.get('sufficient_for_model_c', False)}")

    h("2. HTF CONTEXT PERFORMANCE MATRIX")
    lines.append(f"  {'Label':<22} {'N':>4} {'PF':>6} {'Y_PF':>6} {'N_PF':>6} {'WR':>5} {'bpp':>5} {'Contrib':>7}")
    for label, m in htf_matrix.items():
        bpp = m.bootstrap_pp_gt1
        bpp_s = f"{bpp:.2f}" if bpp is not None else " n/a"
        lines.append(
            f"  {label:<22} {m.n:>4} {m.pf:>6.3f} {m.yes_pf:>6.3f} {m.no_pf:>6.3f} "
            f"{m.wr:>4.0f}% {bpp_s:>5} {m.profit_contribution_pct:>6.1f}%"
        )

    h("3. ALIGNMENT PERFORMANCE MATRIX")
    for label, m in align_matrix.items():
        if m.n == 0:
            continue
        lines.append(
            f"  {label:<28} N={m.n} PF={m.pf:.3f} YES={m.yes_pf:.3f} NO={m.no_pf:.3f} "
            f"avg={m.avg_pnl:.2f}% MaxCL={m.max_cl}"
        )

    h("4. HYPOTHESIS TESTS (A–E)")
    for key, val in hypotheses.items():
        lines.append(f"  {key}: {val}")

    h("5. WALK-FORWARD A/B/C")
    if wf.get("error"):
        lines.append(f"  {wf['error']} (n={wf.get('n', 0)})")
    else:
        lines.append(f"  Train={wf.get('train_n')} Test={wf.get('test_n')}")
        for name, m in wf.get("models", {}).items():
            lines.append(
                f"  {name}: test_PF={m['test_pf']:.3f} lift={m['test_lift_vs_a']:+.3f} "
                f"retention={m['retention_pct']:.0f}%"
            )
        lines.append(f"  MODEL B beats A OOS: {wf.get('model_b_beats_a_oos')}")
        lines.append(f"  MODEL C beats B OOS: {wf.get('model_c_beats_b_oos')}")

    h("6. PREDICTIVE VALUE BY TIMEFRAME")
    for tf, val in predictive.items():
        lines.append(f"  {tf}: {val}")

    h("7. SEPARATE TIMEFRAME RESEARCH (scaffold)")
    for tf, info in tf_research.items():
        lines.append(f"  {tf}: {info.get('status')} — {info.get('note', '')}")

    h("8. VERDICT")
    lines.append(f"  {verdict}")
    if verdict == "COLLECT_MORE_DATA":
        lines.append("  → Run bot with MTF collector enabled to populate multi_timeframe_snapshots")
    elif verdict == "USE_SPOT_CONTEXT_ONLY":
        lines.append("  → BTC spot MTF context helps; do NOT add PM HTF until coverage improves")
    elif verdict == "READY_FOR_CONTEXT_SHADOW":
        lines.append("  → Consider context-filter observe-only shadow (NOT execution)")

    lines.append("")
    return "\n".join(lines)
