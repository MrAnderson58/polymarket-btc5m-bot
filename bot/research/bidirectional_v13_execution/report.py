"""V1.3 execution research report renderer."""

from __future__ import annotations

from typing import Any

REPORT_WIDTH = 72


def render_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    w = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * w)
        lines.append(title)
        lines.append("=" * w)

    h("BIDIRECTIONAL V1.3 EXECUTION-AWARE RESEARCH")
    lines.append("Read-only. Does not modify live execution, V1.1, or V1.2 shadow.")

    cov = report["coverage"]
    h("1. DATA COVERAGE")
    lines.append(f"  Base spec: {cov['base_spec']}")
    lines.append(f"  Candidates: {cov['total_candidates']}")
    lines.append(f"  Quote path coverage: {cov['quote_path_coverage']}/{cov['total_candidates']} ({cov['quote_path_coverage_pct']}%)")
    if cov["dedup"].get("duplicates_removed"):
        lines.append(f"  Dedup removed: {cov['dedup']['duplicates_removed']}")

    h("2. ENTRY MODEL COMPARISON (exit=immediate_bid)")
    lines.append(f"  {'Model':<22} {'Signals':>7} {'Fills':>6} {'Fill%':>6} {'PF':>6} {'OOS':>6} {'YES':>6} {'NO':>6}")
    for m in sorted(report["entry_models"], key=lambda x: -(x.get("pf") or 0)):
        lines.append(
            f"  {m.get('entry_model','?'):<22} {m.get('signals',0):>7} {m.get('fills',0):>6} "
            f"{100*(m.get('fill_rate') or 0):>5.1f}% {m.get('pf',0):>6.3f} "
            f"{m.get('walk_forward',{}).get('oos_pf',0):>6.3f} "
            f"{m.get('yes_pf') or 0:>6.3f} {m.get('no_pf') or 0:>6.3f}"
        )

    h("3. EXIT MODEL COMPARISON (entry=immediate_taker)")
    lines.append(f"  {'Model':<22} {'PF':>6} {'WR%':>6} {'MaxDD':>7} {'Last100':>8}")
    for m in sorted(report["exit_models"], key=lambda x: -(x.get("pf") or 0)):
        lines.append(
            f"  {m.get('exit_model','?'):<22} {m.get('pf',0):>6.3f} {m.get('wr',0):>6.1f} "
            f"{m.get('max_dd',0):>7.2f} {m.get('last100_pf') or 0:>8.3f}"
        )

    h("4. SIDE-SPECIFIC FILTERS (entry=immediate, exit=immediate_bid)")
    for m in report["side_filters"]:
        warn = f" [{m['sample_warning']}]" if m.get("sample_warning") else ""
        lines.append(
            f"  {m.get('filter','?'):<22} n={m.get('closed_trades',0):>3} "
            f"PF={m.get('pf',0):.3f} fill={100*(m.get('fill_rate') or 0):.0f}%{warn}"
        )

    h("5. BEST ROBUST COMBINATION")
    be = report.get("best_entry") or {}
    bx = report.get("best_exit") or {}
    bc = report.get("best_combo") or {}
    lines.append(f"  Entry: {be.get('entry_model', '?')}")
    lines.append(f"  Exit:  {bx.get('exit_model', '?')}")
    lines.append(f"  Filled markets: {bc.get('unique_markets', 0)}")
    lines.append(f"  PF: {bc.get('pf', 0):.3f}  OOS: {bc.get('walk_forward', {}).get('oos_pf', 0):.3f}")
    lines.append(f"  Bootstrap P(PF>1): {bc.get('bootstrap_pp_pf_gt1')}")
    lines.append(f"  Rolling pass: {bc.get('rolling_pass', 0)}/5")

    h("6. PROMOTION GATES")
    for k, v in (report.get("combo_gates") or {}).items():
        lines.append(f"  {k}: {'PASS' if v else 'FAIL'}")

    h("7. VERDICT")
    lines.append(f"  {report.get('verdict', 'UNKNOWN')}")
    lines.append("")
    return "\n".join(lines)
