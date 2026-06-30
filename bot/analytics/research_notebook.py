"""Section 40 — AI research notebook with testable hypotheses."""

from __future__ import annotations

from typing import Any


def _finding(
    text: str,
    *,
    confidence: float,
    hypothesis: str | None = None,
) -> dict[str, Any]:
    return {
        "finding": text,
        "confidence_pct": round(confidence, 0),
        "hypothesis": hypothesis,
    }


def build_research_notebook(report: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []

    regime = report.get("regime_engine", {}).get("regimes", {})
    worst = report.get("regime_engine", {}).get("worst_regime")
    if worst and worst in regime and regime[worst]["trades"] >= 5:
        m = regime[worst]
        if m["avg_pnl"] < 0:
            findings.append(
                _finding(
                    f"All trades in {worst} regime are net negative "
                    f"(WR {m['win_rate']:.0%}, PF {m['profit_factor']:.2f})",
                    confidence=88 if m["trades"] >= 15 else 70,
                    hypothesis=f"Test filter to skip entries during {worst}",
                )
            )

    ep = report.get("entry_price_analysis", {})
    worst_entry = ep.get("worst_entry_price")
    if worst_entry is not None:
        row = next((r for r in ep.get("rows", []) if r["entry_price"] == worst_entry), None)
        if row and row["trades"] >= 8 and row["avg_pnl"] < 0:
            findings.append(
                _finding(
                    f"Entry {worst_entry:.2f} is consistently weak "
                    f"(PF {row['profit_factor']:.2f}, n={row['trades']})",
                    confidence=94 if row["trades"] >= 20 else 78,
                    hypothesis=f"Shadow-block entry at {worst_entry:.2f} and replay",
                )
            )

    sl = report.get("stop_loss_analysis", {})
    if sl.get("avg_time_to_stop_sec", 0) > 20:
        findings.append(
            _finding(
                f"Stop Loss most often triggers around {sl['avg_time_to_stop_sec']:.0f}s",
                confidence=85,
                hypothesis="Replay wider stop vs same time-stop window",
            )
        )

    btc = report.get("btc_filter_analysis", {}).get("rows", [])
    for row in btc:
        if row["bucket"] == ">+30" and row["trades"] >= 5 and row["profit_factor"] < 1.0:
            findings.append(
                _finding(
                    "BTC move >+30$ bucket underperforms — move may stop predicting after +30",
                    confidence=82,
                    hypothesis="Cap BTC filter strict bucket at +30",
                )
            )
            break

    exec_sum = report.get("execution_audit", {}).get("summary", {})
    if exec_sum.get("exchange_loss_pct", 0) > 5:
        findings.append(
            _finding(
                f"Execution/Exchange loss elevated ({exec_sum['exchange_loss_pct']:.1f}% cumulative)",
                confidence=80,
                hypothesis="Audit fill timing; do not change stop until execution fixed",
            )
        )

    drift = report.get("feature_drift", {}).get("alerts", [])
    for alert in drift[:2]:
        findings.append(
            _finding(
                alert.get("message", "Feature drift detected"),
                confidence=75,
                hypothesis="Hold parameters; run walk-forward on recent window only",
            )
        )

    false = report.get("false_stop_detector", {})
    if false.get("false_stop_rate", 0) > 0.25:
        findings.append(
            _finding(
                f"False stop rate {false['false_stop_rate']:.0%} — price recovers above entry quickly",
                confidence=86,
                hypothesis="Do not widen stop; test delayed stop or liquidity filter",
            )
        )

    if not findings:
        findings.append(
            _finding(
                "No strong anomalies this cycle — continue paper accumulation",
                confidence=90,
                hypothesis="KEEP CURRENT SETTINGS; review after +100 trades",
            )
        )

    live = report.get("live_sample", {})
    if not live.get("sufficient"):
        findings.append(
            _finding(
                f"Paper/live sample since last change: {live.get('current_since_change', 0)} "
                f"/ {live.get('required_min', 300)} trades",
                confidence=95,
                hypothesis="No parameter changes until sample target met",
            )
        )

    return {
        "findings_count": len(findings),
        "findings": findings[:8],
        "pipeline_reminder": (
            "Idea → Shadow → Statistics → Offline replay → Walk Forward → Paper → Live"
        ),
    }
