"""Render §45 STRATEGY REVIEW section."""

from __future__ import annotations

from typing import Any


def render_strategy_review_section(review: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(f"_{review.get('disclaimer', '')}_")

    entry = review.get("entry_analysis", {})
    rec = entry.get("recommendation", {})
    current_entry = rec.get("current") or entry.get("current_entry") or 0.40
    lines.append("\n**Entry Threshold**")
    lines.append(f"- Current: **{float(current_entry):.2f}**")
    if rec.get("action") == "CHANGE":
        lines.append(
            f"- Recommended: **{rec.get('recommended'):.2f}** | "
            f"Confidence: **{rec.get('confidence_pct', 0):.0f}%**"
        )
        for r in rec.get("reasons", [])[:5]:
            lines.append(f"  - {r}")
    else:
        lines.append(f"- Recommendation: **KEEP {float(current_entry):.2f}**")
        lines.append(f"  - Reason: {rec.get('reasons', ['Insufficient evidence'])[0]}")

    lines.append("\n| Entry | Trades | WR | PF | Avg PnL | EV | Stability | p-value | WF | Overfit | Conf |")
    lines.append("|-------|--------|----|----|---------|----|-----------|---------|----|---------|------|")
    for row in entry.get("rows", []):
        pf = row.get("profit_factor", 0)
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        wf = "PASS" if row.get("walk_forward", {}).get("passed") else "FAIL"
        lines.append(
            f"| {row['entry_price']:.2f} | {row['trades']} | {row['win_rate']:.0%} | {pf_s} | "
            f"{row['avg_pnl']:+.2f}% | {row.get('expected_value', 0):+.2f} | "
            f"{row.get('stability_score', 0):.0f} | {row.get('p_value', 1):.3f} | {wf} | "
            f"{row.get('overfit_risk', '?')} | {row.get('confidence_pct', 0):.0f}% |"
        )

    stop = review.get("stop_loss_analysis", {})
    srec = stop.get("recommendation", {})
    current_stop = srec.get("current_stop_pct") or stop.get("current_stop_pct") or -10
    lines.append("\n**Stop Loss**")
    lines.append(f"- Current: **{float(current_stop):.0f}%**")
    if srec.get("action") == "CHANGE":
        lines.append(
            f"- Recommended: **{srec.get('recommended_stop_pct'):.0f}%** | "
            f"Expected PF: **+{srec.get('expected_pf_improvement_pct', 0):.0f}%** | "
            f"Confidence: **{srec.get('confidence_pct', 0):.0f}%** | Risk: **{srec.get('risk')}**"
        )
    else:
        lines.append("- Recommendation: **KEEP** current stop")

    recov = stop.get("recovery", {}).get("recovery_windows", [])
    if recov:
        parts = [f"{w['window_sec']}s={w['recovery_rate']:.0%}" for w in recov[:5]]
        lines.append(f"- Recovery: {', '.join(parts)}")

    trail = review.get("trailing_analysis", {})
    trec = trail.get("recommendation", {})
    lines.append("\n**Trailing**")
    lines.append(
        f"- Current: activation **{trec.get('current_activation', trail.get('current', {}).get('activation'))}** / "
        f"distance **{trec.get('current_distance', trail.get('current', {}).get('distance'))}**"
    )
    best = trail.get("best")
    if best:
        lines.append(
            f"- Best grid: activation **{best['activation']}** / distance **{best['distance']}** "
            f"(PF {best['profit_factor']:.2f})"
        )
    if trec.get("action") == "CHANGE":
        lines.append(
            f"- Recommended: **{trec.get('recommended_activation')}** / **{trec.get('recommended_distance')}**"
        )
    else:
        lines.append("- Recommendation: **KEEP** current trailing")

    combos = review.get("combinations", {})
    lines.append("\n**Parameter interactions** _(not recommended)_")
    for item in combos.get("interactions", [])[:3]:
        lines.append(
            f"- {item['label']}: synergy {item['synergy']:+.2f}% ({item['interaction']})"
        )

    verdict = review.get("final_verdict", {})
    lines.append("\n**FINAL STRATEGY REVIEW**")
    lines.append(f"- **{verdict.get('decision', 'KEEP CURRENT SETTINGS')}**")
    if verdict.get("change_text"):
        lines.append(f"  - Change: {verdict['change_text']}")
    lines.append(f"- Confidence: **{verdict.get('confidence_pct', 0):.0f}%**")
    lines.append(f"- Reason: {verdict.get('reason', '')}")
    if verdict.get("next_review"):
        lines.append(f"- Next review: {verdict['next_review']}")

    return lines
