"""Phase F.5 — human-readable signal cause composition."""

from __future__ import annotations

from typing import Any


def compose_signal_reasons(
    *,
    trend: dict[str, Any] | None,
    report: Any,
    visual: dict[str, Any] | None,
    active_factors: list[str],
) -> tuple[str, list[str]]:
    """Return (short cause headline, interest checklist lines)."""
    parts: list[str] = []
    checklist: list[str] = []

    if trend:
        streak = int(trend.get("consecutive_bars") or 0)
        direction = str(trend.get("direction") or "DOWN")
        if streak >= 2:
            color = "красных" if direction == "DOWN" else "зелёных"
            checklist.append(f"✔ {streak} {color} свечей подряд")
        vol = float(trend.get("volume_multiple") or 0)
        if vol >= 1.5:
            checklist.append(f"✔ объём {vol:.1f}× среднего")
        fund = trend.get("funding")
        if fund is not None and float(fund) < 0:
            parts.append("Funding")
            checklist.append("✔ Funding становится отрицательным")
        elif report and report.funding_regime == "flattening":
            parts.append("Funding")
            checklist.append("✔ Funding снижается")
        oi = trend.get("open_interest_delta")
        if oi is not None and float(oi) <= 0:
            parts.append("OI")
            checklist.append("✔ OI перестал расти")
        elif report and report.oi_regime in ("falling", "divergence"):
            parts.append("OI")
            checklist.append("✔ OI перестал расти")

    if visual and (visual.get("chart_analysis") or {}).get("demand_zones"):
        parts.append("Demand-зона")
        checklist.append("✔ Цена вошла в Demand-зону")

    if report:
        if any("LL" in s or "Lower" in s for s in (report.market_structure_labels or [])):
            parts.append("потеря поддержки")
            checklist.append("✔ Потеря поддержки / LL")
        if report.historical_count >= 3:
            parts.append("история")
            checklist.append(
                f"✔ {report.historical_count} похожих случаев"
            )
            checklist.append(
                f"✔ Исторический откат {int(report.historical_reversal_rate * 100)}%"
            )

    if "liquidations" in active_factors:
        parts.insert(0, "ликвидации")

    if not parts:
        parts = ["Trend Shock"]
    headline = " + ".join(dict.fromkeys(parts))
    return headline, checklist[:8]
