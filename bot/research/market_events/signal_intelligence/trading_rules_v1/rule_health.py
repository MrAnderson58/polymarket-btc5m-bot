"""One-screen rule health: READY / RESEARCH / BROKEN."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.trading_rules_v1.engine import (
    run_trading_rules_v1,
)


def _bucket(rule: dict[str, Any]) -> str:
    status = str(rule.get("stability_status") or "")
    pf_ok = (rule.get("pf_verify") or {}).get("ok")
    if rule.get("ready_ok") and status == "PAPER_READY" and pf_ok is not False:
        return "READY"
    if rule.get("ready_ok") is False and (rule.get("n") or 0) == 0:
        return "BROKEN"
    if pf_ok is False:
        return "BROKEN"
    if status == "RESEARCH_ONLY" or rule.get("ready_ok"):
        return "RESEARCH"
    return "BROKEN"


def run_rule_health(conn: Any, *, limit: int | None = None) -> dict[str, Any]:
    result = run_trading_rules_v1(conn, write_reports=False, limit=limit)
    ready_rules = result.get("ready_for_paper") or []
    blocks = result.get("hard_block") or []

    buckets = {"READY": [], "RESEARCH": [], "BROKEN": []}
    for r in ready_rules:
        buckets[_bucket(r)].append(r)
    for r in blocks:
        # hard blocks are RESEARCH (forbid), not READY
        label = "BROKEN" if (r.get("pf_verify") or {}).get("ok") is False else "RESEARCH"
        buckets[label].append(r)

    return {
        "ok": True,
        "n_trades": result.get("n_trades"),
        "READY": buckets["READY"],
        "RESEARCH": buckets["RESEARCH"],
        "BROKEN": buckets["BROKEN"],
        "n_paper_ready": sum(
            1 for r in ready_rules if r.get("stability_status") == "PAPER_READY"
        ),
        "pf_verified": all(
            (r.get("pf_verify") or {}).get("ok", True) for r in ready_rules
        ),
        "elapsed_sec": result.get("elapsed_sec"),
    }


def format_rule_health(report: dict[str, Any]) -> str:
    lines = [
        "RULE HEALTH",
        "",
        "READY",
    ]
    ready = report.get("READY") or []
    if not ready:
        lines.append("(none)")
    for r in ready[:8]:
        cond = " + ".join(r.get("conditions") or [])
        lines.append(f"  {cond}  n={r.get('n')} PF={r.get('pf')} EV={r.get('ev')}")
    lines.extend(["", "RESEARCH"])
    research = report.get("RESEARCH") or []
    if not research:
        lines.append("(none)")
    for r in research[:8]:
        cond = " + ".join(r.get("conditions") or [])
        st = r.get("stability_status") or "RESEARCH_ONLY"
        lines.append(f"  {cond}  [{st}] n={r.get('n')} PF={r.get('pf')}")
    lines.extend(["", "BROKEN"])
    broken = report.get("BROKEN") or []
    if not broken:
        lines.append("(none)")
    for r in broken[:8]:
        cond = " + ".join(r.get("conditions") or [])
        lines.append(f"  {cond}  pf_ok={(r.get('pf_verify') or {}).get('ok')}")
    lines.append("")
    lines.append(
        f"paper_ready={report.get('n_paper_ready')} "
        f"pf_verified={report.get('pf_verified')} "
        f"trades={report.get('n_trades')}"
    )
    return "\n".join(lines)


__all__ = ["format_rule_health", "run_rule_health"]
