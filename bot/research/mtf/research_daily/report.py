"""Daily research report."""

from __future__ import annotations

from typing import Any


def render_daily_report(result: dict[str, Any]) -> str:
    lines = ["BTC DAILY POLYMARKET BIDIRECTIONAL RESEARCH", "=" * 50, ""]
    audit = result.get("data_audit", {})
    lines.extend([
        "COVERAGE AUDIT",
        f"  snapshot rows: {audit.get('snapshot_rows', 0)}",
        f"  markets: {audit.get('markets', 0)}",
        f"  observations: {audit.get('observations', 0)}",
        f"  strike coverage: {audit.get('strike_coverage_pct', 0)}%",
        f"  seconds_left coverage: {audit.get('seconds_left_coverage_pct', 0)}%",
        f"  train/test markets: {result.get('train_markets', 0)}/{result.get('test_markets', 0)}",
        "",
    ])
    if result.get("status") == "insufficient_data":
        lines.append(f"VERDICT: {result.get('verdict')}")
        return "\n".join(lines)

    if result.get("thresholds"):
        lines.append("TRAIN THRESHOLDS (daily-specific)")
        for k, v in result["thresholds"].items():
            lines.append(f"  {k}: {v:.2f}")
        lines.append("")

    lines.append(f"{'family':<24} {'side':<5} {'exit':<14} {'N':>4} {'PF':>6} {'OOS':>6} {'WR':>6}")
    lines.append("-" * 72)
    for r in sorted(result.get("family_results", []), key=lambda x: -x.get("pf", 0)):
        oos = r.get("oos_pf")
        lines.append(
            f"{r['family']:<24} {r['side']:<5} {r['exit_mode']:<14} "
            f"{r['n']:>4} {r['pf']:>6.2f} {oos if oos is not None else 'n/a':>6} {r['wr']:>5.1f}%"
        )

    conc = result.get("concentration", {})
    if conc:
        lines.extend(["", "CONCENTRATION", f"  top market PnL share: {conc.get('top_market_pct', 0)}%"])

    asym = result.get("yes_no_asymmetry", {})
    if asym:
        lines.extend(["", "YES/NO ASYMMETRY"])
        for f, v in asym.items():
            lines.append(f"  {f}: YES PF={v.get('yes_pf', 0):.2f} NO PF={v.get('no_pf', 0):.2f}")

    lines.extend(["", f"VERDICT: {result.get('verdict')}", "", "Research-only. No execution enabled."])
    return "\n".join(lines)
