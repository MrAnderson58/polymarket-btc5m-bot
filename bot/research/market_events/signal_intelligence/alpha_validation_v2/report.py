"""Reports for Alpha Validation Engine V2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORT_MD = BASE_DIR / "ALPHA_VALIDATION_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "alpha_validation_v2"


def format_validation_report(
    *,
    run_id: str,
    n_rows: int,
    results: list[dict[str, Any]],
    history_summary: dict[str, Any] | None = None,
) -> str:
    passed = [r for r in results if r.get("status") == "PASSED"]
    rejected = [r for r in results if r.get("status") == "REJECTED"]
    insuf = [r for r in results if r.get("status") == "INSUFFICIENT"]
    lines = [
        "# ALPHA_VALIDATION_REPORT",
        "",
        "_Alpha Validation Engine V2 — research only. No Gate / Strategy / Paper / Execution changes._",
        "",
        f"- run_id: `{run_id}`",
        f"- corpus trades: **{n_rows}**",
        f"- candidates tested: **{len(results)}**",
        f"- PASSED: **{len(passed)}**",
        f"- REJECTED: **{len(rejected)}**",
        f"- INSUFFICIENT: **{len(insuf)}**",
        "",
        "> Discovery candidates are hypotheses only. Production requires PASSED on walk-forward, "
        "rolling windows, OOS replay, and temporal stability (no independent window may lose edge).",
        "",
        "## PASSED alphas",
        "",
    ]
    if not passed:
        lines.append("_None — all candidates rejected or insufficient on this corpus._")
        lines.append("")
    else:
        lines.extend([
            "| Rule | n | WR | EV | PF | CI | p | WF | Roll | OOS | Stab |",
            "|---|---:|---:|---:|---:|---|---:|:---:|:---:|:---:|:---:|",
        ])
        for r in passed[:40]:
            gates = r.get("gates") or {}
            lines.append(
                "| `{label}` | {n} | {wr} | {ev} | {pf} | {ci} | {p} | {wf} | {ro} | {oos} | {st} |".format(
                    label=(r.get("rule_label") or r.get("rule_id") or "")[:70],
                    n=r.get("n_matched"),
                    wr=r.get("winrate"),
                    ev=r.get("expectancy"),
                    pf=r.get("pf"),
                    ci=r.get("ci_ev"),
                    p=r.get("p_value"),
                    wf="Y" if gates.get("walk_forward") else "N",
                    ro="Y" if gates.get("rolling") else "N",
                    oos="Y" if gates.get("oos") else "N",
                    st="Y" if gates.get("stability") else "N",
                )
            )
        lines.append("")

    lines.extend(["## Top rejections", ""])
    for r in rejected[:25]:
        lines.append(
            f"- `{(r.get('rule_label') or '')[:80]}` — **{r.get('reject_reason')}** "
            f"(n={r.get('n_matched')}, EV={r.get('expectancy')}, PF={r.get('pf')})"
        )
    if not rejected:
        lines.append("_No rejections._")
    lines.extend([
        "",
        "## Validation protocol",
        "",
        "1. Walk-forward (train / validation / test chronological splits)",
        "2. Rolling non-overlapping windows",
        "3. Out-of-sample replay (70/30 freeze)",
        "4. Monte Carlo subset null + permutation p-value",
        "5. Bootstrap expectancy CI (prefer OOS)",
        "6. Temporal stability of PF / EV / WR",
        "7. Auto-reject if edge lost in any independent window",
        "",
        "## Safety",
        "",
        "- Observe-only research module",
        "- Tables: `alpha_validations`, `alpha_validation_history`",
        "- No auto-apply to Gate / Strategy / Paper / Execution",
        "",
    ])
    if history_summary:
        lines.extend(["## Run summary JSON", "", "```json", json.dumps(history_summary, indent=2, default=str), "```", ""])
    return "\n".join(lines)


def write_validation_artifacts(
    *,
    run_id: str,
    n_rows: int,
    results: list[dict[str, Any]],
) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "n_rows": n_rows,
        "n_candidates": len(results),
        "n_passed": sum(1 for r in results if r.get("status") == "PASSED"),
        "n_rejected": sum(1 for r in results if r.get("status") == "REJECTED"),
        "n_insufficient": sum(1 for r in results if r.get("status") == "INSUFFICIENT"),
    }
    md = format_validation_report(
        run_id=run_id, n_rows=n_rows, results=results, history_summary=summary,
    )
    REPORT_MD.write_text(md, encoding="utf-8")
    report_copy = OUT_DIR / "ALPHA_VALIDATION_REPORT.md"
    report_copy.write_text(md, encoding="utf-8")
    results_path = OUT_DIR / "alpha_validations.json"
    results_path.write_text(
        json.dumps({"run_id": run_id, "n_rows": n_rows, "results": results}, indent=2, default=str),
        encoding="utf-8",
    )
    passed_path = OUT_DIR / "alpha_validated_passed.json"
    passed_path.write_text(
        json.dumps(
            [r for r in results if r.get("status") == "PASSED"],
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {
        "report_md": str(REPORT_MD),
        "report_copy": str(report_copy),
        "validations_json": str(results_path),
        "passed_json": str(passed_path),
    }


def format_report_from_db(rows: list[dict[str, Any]], *, run_id: str, n_rows: int = 0) -> str:
    results = []
    for r in rows:
        pf = r.get("pf")
        if pf is not None and isinstance(pf, float) and pf == float("inf"):
            pf_out: Any = "inf"
        else:
            pf_out = pf
        results.append({
            "rule_id": r.get("rule_id"),
            "rule_label": r.get("rule_label"),
            "status": r.get("status"),
            "reject_reason": r.get("reject_reason"),
            "n_matched": r.get("n_matched"),
            "winrate": r.get("winrate"),
            "expectancy": r.get("expectancy"),
            "pf": pf_out,
            "sharpe": r.get("sharpe"),
            "ci_ev": (r.get("ci_lo"), r.get("ci_hi")),
            "p_value": r.get("p_value"),
            "gates": {
                "walk_forward": '"passed": true' in str(r.get("walk_forward_json") or "").lower(),
                "rolling": '"passed": true' in str(r.get("rolling_json") or "").lower(),
                "oos": '"passed": true' in str(r.get("oos_json") or "").lower(),
                "stability": '"passed": true' in str(r.get("stability_json") or "").lower(),
            },
        })
    return format_validation_report(run_id=run_id, n_rows=n_rows, results=results)


__all__ = [
    "OUT_DIR",
    "REPORT_MD",
    "format_report_from_db",
    "format_validation_report",
    "write_validation_artifacts",
]
