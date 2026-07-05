"""15m research report renderer."""

from __future__ import annotations

from typing import Any


def render_15m_report(result: dict[str, Any]) -> str:
    lines = [
        "BTC 15M POLYMARKET BIDIRECTIONAL RESEARCH",
        "=" * 50,
        "",
    ]

    audit = result.get("data_audit", {})
    lines.extend([
        "DATA",
        f"  MTF snapshot rows (15m): {audit.get('snapshot_rows', 0)}",
        f"  15m markets: {audit.get('markets', 0)}",
        f"  observations in windows: {audit.get('observations', 0)}",
        f"  train markets: {result.get('train_markets', 0)}",
        f"  test markets: {result.get('test_markets', 0)}",
        "",
    ])

    if result.get("status") == "insufficient_data":
        lines.append(f"VERDICT: {result.get('verdict', 'COLLECT_MORE_DATA')}")
        return "\n".join(lines)

    if result.get("thresholds"):
        lines.append("TRAIN-CALIBRATED THRESHOLDS (not 5m)")
        for k, v in result["thresholds"].items():
            lines.append(f"  {k}: {v:.2f}")
        lines.append("")

    lines.append("FAMILY RESULTS (entry on train, exit chosen on train, OOS on test)")
    lines.append(f"{'family':<22} {'side':<5} {'exit':<14} {'N':>4} {'PF':>6} {'OOS':>6} {'WR':>6} {'avgPnL':>7} {'bootP':>6}")
    lines.append("-" * 80)
    for r in sorted(result.get("family_results", []), key=lambda x: -x.get("pf", 0)):
        boot = r.get("bootstrap_p_pf_gt1")
        boot_s = f"{boot:.2f}" if boot is not None else "  n/a"
        oos = r.get("oos_pf")
        oos_s = f"{oos:.2f}" if oos is not None else "  n/a"
        lines.append(
            f"{r['family']:<22} {r['side']:<5} {r['exit_mode']:<14} "
            f"{r['n']:>4} {r['pf']:>6.2f} {oos_s:>6} {r['wr']:>5.1f}% "
            f"{r['avg_pnl']:>7.2f} {boot_s:>6}"
        )

    asym = result.get("yes_no_asymmetry", {})
    if asym:
        lines.extend(["", "YES/NO ASYMMETRY (train PF)",])
        for fam, v in asym.items():
            lines.append(f"  {fam}: YES PF={v.get('yes_pf', 0):.2f}  NO PF={v.get('no_pf', 0):.2f}")

    lines.extend(["", f"VERDICT: {result.get('verdict', 'CONTINUE_RESEARCH')}"])
    lines.extend([
        "",
        "Notes:",
        "  - Research-only; no execution enabled",
        "  - Thresholds calibrated on train fold only",
        "  - Exit mode selected on train; OOS metrics on test markets",
        "  - Uses multi_timeframe_snapshots + market_checks (no look-ahead)",
    ])
    return "\n".join(lines)
