"""One-screen terminal formatter for READY / HARD BLOCK."""

from __future__ import annotations

from typing import Any


def _pf(v: Any, inf: bool = False) -> str:
    if v is None:
        return "inf" if inf else "n/a"
    return f"{float(v):.2f}"


def _ci(lo: Any, hi: Any) -> str:
    if lo is None or hi is None:
        return "n/a"
    return f"[{float(lo):.4f}, {float(hi):.4f}]"


def format_terminal(result: dict[str, Any]) -> str:
    lines: list[str] = ["READY FOR PAPER", ""]
    ready = result.get("ready_for_paper") or []
    if not ready:
        lines.append("(none passed n≥500 + universal + CI filters)")
        lines.append("")
    for i, r in enumerate(ready[:10], 1):
        lines.append(f"{i}.")
        lines.append("Rule:")
        for c in r.get("conditions") or []:
            lines.append(str(c))
        lines.append("")
        lines.append("Trades:")
        lines.append(str(r.get("n")))
        lines.append("")
        lines.append("WR:")
        lines.append(f"{r.get('wr')}%")
        lines.append("")
        lines.append("PF:")
        lines.append(_pf(r.get("pf"), bool(r.get("pf_inf"))))
        lines.append("")
        lines.append("Gross Profit:")
        lines.append(str(r.get("gross_profit")))
        lines.append("")
        lines.append("Gross Loss:")
        lines.append(str(r.get("gross_loss")))
        lines.append("")
        lines.append("Avg Win:")
        lines.append(str(r.get("avg_win")))
        lines.append("")
        lines.append("Avg Loss:")
        lines.append(str(r.get("avg_loss")))
        lines.append("")
        lines.append("EV:")
        lines.append(str(r.get("ev")))
        lines.append("")
        pv = r.get("pf_verify") or {}
        lines.append("PF check:")
        lines.append(
            f"{'OK' if pv.get('ok') else 'FAIL'} "
            f"(PF={pv.get('pf')} = GP/GL, dupes={pv.get('duplicates', 0)})"
        )
        lines.append("")
        lines.append("Stability:")
        lines.append(str(r.get("stability_status") or "RESEARCH_ONLY"))
        stab = (r.get("stability") or {}).get("months") or []
        for m in stab[-6:]:
            lines.append(
                f"  {m.get('month')} WR={m.get('wr')} PF={m.get('pf')} "
                f"EV={m.get('ev')} n={m.get('n')}"
            )
        lines.append("")
        lines.append("CI:")
        lines.append(_ci(r.get("ci_lo"), r.get("ci_hi")))
        lines.append("")
        lines.append("Confidence:")
        lines.append(str(r.get("confidence")))
        lines.append("")
        lines.append("MinN:")
        lines.append(
            f"PASS (≥{r.get('min_n')})" if r.get("passes_min_n") else f"FAIL (<{r.get('min_n')})"
        )
        lines.append("")
        lines.append("----------------")
        lines.append("")

    lines.append("HARD BLOCK")
    lines.append("")
    blocks = result.get("hard_block") or []
    if not blocks:
        lines.append("(none)")
        lines.append("")
    for i, r in enumerate(blocks[:10], 1):
        lines.append(f"{i}.")
        lines.append("")
        for c in r.get("conditions") or []:
            lines.append(str(c))
        lines.append("")
        lines.append(f"Trades: {r.get('n')}")
        lines.append(f"WR: {r.get('wr')}%")
        lines.append(f"PF: {_pf(r.get('pf'))}")
        lines.append(f"EV: {r.get('ev')}")
        lines.append(f"CI: {_ci(r.get('ci_lo'), r.get('ci_hi'))}")
        lines.append("Action: BLOCK")
        lines.append("")
        lines.append("----------------")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_report(result: dict[str, Any]) -> str:
    lines = [
        "# TRADING_RULES_REPORT",
        "",
        "_Trading Rules Extraction V1 — research only. No new features; DNA setups minimized for paper._",
        "",
        f"- corpus: **{result.get('n_trades')}**",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- ready: **{len(result.get('ready_for_paper') or [])}**",
        f"- hard_block: **{len(result.get('hard_block') or [])}**",
        "",
        "## READY FOR PAPER",
        "",
    ]
    for i, r in enumerate((result.get("ready_for_paper") or [])[:10], 1):
        lines.append(f"### {i}. {' + '.join(r.get('conditions') or [])}")
        lines.append("")
        lines.append(
            f"- n={r.get('n')} WR={r.get('wr')} PF={r.get('pf')} EV={r.get('ev')} "
            f"CI=[{r.get('ci_lo')}, {r.get('ci_hi')}] conf={r.get('confidence')} "
            f"universal={((r.get('universal') or {}).get('ok'))}"
        )
        lines.append("")
    lines.extend(["## HARD BLOCK", ""])
    for i, r in enumerate((result.get("hard_block") or [])[:10], 1):
        lines.append(f"### {i}. {' + '.join(r.get('conditions') or [])}")
        lines.append("")
        lines.append(
            f"- n={r.get('n')} WR={r.get('wr')} PF={r.get('pf')} EV={r.get('ev')} Action=BLOCK"
        )
        lines.append("")
    lines.extend([
        "## Integrity",
        "",
        f"- research_only: {result.get('research_only')}",
        f"- paper_unchanged: {result.get('paper_unchanged')}",
        f"- execution_unchanged: {result.get('execution_unchanged')}",
        f"- strategy_unchanged: {result.get('strategy_unchanged')}",
        f"- gate_unchanged: {result.get('gate_unchanged')}",
        f"- optimizer_unchanged: {result.get('optimizer_unchanged')}",
        f"- brain_unchanged: {result.get('brain_unchanged')}",
        "",
    ])
    return "\n".join(lines)


__all__ = ["format_report", "format_terminal"]
