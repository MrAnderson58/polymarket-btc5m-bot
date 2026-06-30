"""Markdown / JSON rendering for optimizer report."""

from __future__ import annotations

import json
import math
from typing import Any


def render_json(report: dict[str, Any]) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, float):
            if math.isinf(value):
                return "inf" if value > 0 else "-inf"
        return value

    return json.dumps(report, indent=2, default=default, ensure_ascii=False)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Trading AI Optimizer v1",
        "",
        f"Generated: {report['meta']['generated_at']}",
        f"Runtime: {report['meta']['runtime_sec']}s | "
        f"Features: {report['meta']['trade_features_count']} | "
        f"Replay trades: {report['meta']['replay_trades']}",
        "",
        "> **Read-only analysis.** Ничего не меняет в торговой логике.",
        "",
    ]

    grid = report["parameter_optimizer"]
    cur, opt = grid["current"], grid["optimal"]
    lines += [
        "## PARAMETER OPTIMIZER",
        "",
        "**Current**",
        f"- Entry {cur['entry']:.2f} | Stop {abs(cur['stop_pct']):.0f}% | "
        f"Trailing {cur['trailing_activation']:.3f}/{cur['trailing_distance']:.3f} | "
        f"Time {cur['time_stop_sec']}s | BTC filter +{cur['btc_filter_usd']:.0f}",
        f"- Trades {cur['trades']} | WR {cur['win_rate']:.0%} | PF {cur['profit_factor']:.2f} | "
        f"Avg {cur['avg_pnl']:+.2f}%",
        "",
        "**Optimal (offline grid)**",
        f"- Entry {opt['entry']:.2f} | Stop {abs(opt['stop_pct']):.0f}% | "
        f"Trailing {opt['trailing_activation']:.3f}/{opt['trailing_distance']:.3f} | "
        f"Time {opt['time_stop_sec']}s | BTC filter +{opt['btc_filter_usd']:.0f}",
        f"- Trades {opt['trades']} | WR {opt['win_rate']:.0%} | PF {opt['profit_factor']:.2f} | "
        f"Avg {opt['avg_pnl']:+.2f}%",
        "",
        f"**Expected improvement:** {grid['expected_improvement_pct']:+.1f}%",
        f"**Grid:** {grid['combos_tested']}/{grid['combos_total']} combinations tested",
        "",
    ]

    ml = report.get("machine_learning", {})
    lines.append("## MACHINE LEARNING")
    for target, data in ml.get("targets", {}).items():
        if isinstance(data, dict) and data.get("best_model"):
            lines.append(
                f"- **{target}**: best={data['best_model']}, AUC={data['best_auc_cv']:.3f}"
            )
    lines.append("\n## FEATURE IMPORTANCE")
    for item in ml.get("feature_importance", []):
        lines.append(f"- {item['feature']}: {item['importance_pct']}%")

    lines.append("\n## CLUSTER ANALYSIS")
    for c in report.get("cluster_analysis", {}).get("clusters", []):
        lines.append(
            f"- Type {c['cluster_id']} ({c['label']}): {c['trades']} trades, "
            f"WR {c['win_rate']:.0%}, PF {c['profit_factor']:.2f}"
        )

    lines.append("\n## AI RULES DISCOVERY")
    for rule in report.get("rules_discovery", [])[:8]:
        lines.append(f"- {rule.get('rule_text', rule)}")

    lines.append("\n## WALK FORWARD")
    for wf in report.get("walk_forward", []):
        ok = "OK" if wf.get("generalizes") else "FAIL"
        lines.append(
            f"- Train {wf['train_size']} → Test {wf['test_size']}: "
            f"test PF {wf['test_pf']:.2f}, avg {wf['test_avg_pnl']:+.2f}% [{ok}]"
        )

    lines.append("\n## VERSION MANAGER")
    for v in report.get("version_manager", {}).get("versions", []):
        if v["trades"]:
            lines.append(
                f"- {v['version']}: {v['trades']} trades, WR {v['win_rate']:.0%}, "
                f"PF {v['profit_factor']:.2f}"
            )
    for cmp in report.get("version_manager", {}).get("comparisons", []):
        lines.append(f"- {cmp['pair']}: **{cmp['verdict']}** (ΔPF {cmp['delta_pf']:+.2f})")

    lines.append("\n## TOP 5 RECOMMENDATIONS")
    lines.append("")
    lines.append("| Parameter | Value | Confidence |")
    lines.append("| --- | --- | --- |")
    for rec in report.get("recommendations", []):
        lines.append(
            f"| {rec['parameter']} | {rec['value']} | "
            f"{rec['confidence']} ({rec['confidence_pct']:.0f}%) |"
        )
    lines.append("")
    for rec in report.get("recommendations", []):
        lines.append(f"- {rec['message']}")

    lines.append(
        "\n---\n\n**Не применять автоматически.** Согласовать изменения вручную после review."
    )
    return "\n".join(lines).rstrip() + "\n"
