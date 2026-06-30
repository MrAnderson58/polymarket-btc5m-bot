"""SAFE TO CHANGE block (Report v3 §25)."""

from __future__ import annotations

from typing import Any

from bot.analytics.live_sample import MIN_LIVE_TRADES, TARGET_LIVE_TRADES


def _verdict(
    *,
    can_change: bool,
    wait: bool,
    confidence_pct: float,
    reason: str,
) -> dict[str, Any]:
    if not can_change and not wait:
        status = "NO"
    elif wait:
        status = "WAIT"
    else:
        status = "YES"
    return {
        "status": status,
        "confidence_pct": round(confidence_pct, 1),
        "reason": reason,
    }


def build_safe_to_change(report: dict[str, Any]) -> list[dict[str, Any]]:
    live = report.get("live_sample", {})
    stability = report.get("parameter_stability", {}).get("parameters", [])
    sensitivity = report.get("sensitivity_analysis", {}).get("parameters", [])
    overfit = report.get("overfit_detector", {})
    optimizer = report.get("parameter_optimizer", {})

    since = live.get("current_since_change", 0)
    sufficient = live.get("sufficient", False)
    overfit_high = overfit.get("level") == "HIGH"

    stab_by_param = {p["parameter"]: p for p in stability}
    sens_by_param = {p["parameter"]: p for p in sensitivity}
    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})

    def _entry() -> dict[str, Any]:
        if not sufficient or overfit_high:
            return _verdict(
                can_change=False,
                wait=True,
                confidence_pct=90 if overfit_high else 85,
                reason=(
                    f"Need +{live.get('trades_needed', MIN_LIVE_TRADES - since)} trades "
                    f"(have {since}/{MIN_LIVE_TRADES})"
                    if not sufficient
                    else overfit.get("recommendation", "Overfit risk")
                ),
            )
        stab = stab_by_param.get("entry", {})
        sens = sens_by_param.get("entry", {})
        if sens.get("sensitivity") == "HIGH":
            return _verdict(
                can_change=False,
                wait=True,
                confidence_pct=70,
                reason="Entry is highly sensitive — small change swings PF sharply",
            )
        if stab.get("stability_label") == "stable" and opt.get("entry") != cur.get("entry"):
            return _verdict(
                can_change=True,
                wait=False,
                confidence_pct=min(94, stab.get("stability_pct", 70)),
                reason=f"Stable offline edge at entry {opt.get('entry'):.2f}",
            )
        return _verdict(
            can_change=False,
            wait=True,
            confidence_pct=60,
            reason="Difference insignificant or stability medium",
        )

    def _generic(name: str, trades_needed: int) -> dict[str, Any]:
        if not sufficient or since < trades_needed:
            need = max(0, trades_needed - since)
            return _verdict(
                can_change=False,
                wait=True,
                confidence_pct=80,
                reason=f"Need +{need} trades (have {since})",
            )
        if overfit_high:
            return _verdict(
                can_change=False,
                wait=False,
                confidence_pct=85,
                reason="Overfit detector blocks parameter changes",
            )
        sens = sens_by_param.get(name.replace(" ", "_"), {})
        if sens.get("sensitivity") == "HIGH":
            return _verdict(
                can_change=False,
                wait=True,
                confidence_pct=65,
                reason="Difference insignificant — high sensitivity",
            )
        stab = stab_by_param.get(name.replace(" ", "_"), {})
        if stab.get("stability_label") == "stable":
            return _verdict(
                can_change=True,
                wait=False,
                confidence_pct=min(90, stab.get("stability_pct", 75)),
                reason="Stable offline metrics",
            )
        return _verdict(
            can_change=False,
            wait=True,
            confidence_pct=55,
            reason="Difference insignificant",
        )

    return [
        {"parameter": "Entry Threshold", **_entry()},
        {"parameter": "Stop Loss", **_generic("stop_loss", 180)},
        {"parameter": "Trailing", **_generic("trailing", TARGET_LIVE_TRADES)},
        {"parameter": "BTC Filter", **_generic("btc_filter", 350)},
        {"parameter": "Holding Time", **_generic("holding_time", TARGET_LIVE_TRADES)},
    ]
