"""FINAL ACTION PLAN (Report v3 §30)."""

from __future__ import annotations

from typing import Any

from bot.analytics.live_sample import MIN_LIVE_TRADES, TARGET_LIVE_TRADES


def build_final_action_plan(report: dict[str, Any]) -> dict[str, Any]:
    decision = report.get("ai_decision", {})
    live = report.get("live_sample", {})
    safe = report.get("safe_to_change", [])
    positions = report.get("current_positions", {})

    if positions.get("warnings"):
        return {
            "today": "RECOVER STUCK TRADE",
            "priority": 5,
            "details": positions["warnings"][0].get("message", ""),
            "do_not_touch": [],
            "expected_review": None,
        }

    if not live.get("sufficient"):
        return {
            "today": "DO NOT CHANGE STRATEGY",
            "priority": 5,
            "need_trades": live.get("trades_needed", MIN_LIVE_TRADES),
            "current_sample": live.get("current_since_change", 0),
            "expected_review": f"after {TARGET_LIVE_TRADES} trades",
            "do_not_touch": [
                "Stop",
                "Trailing",
                "BTC Filter",
                "Holding Time",
                "Entry Threshold",
            ],
            "reason": live.get("message", "INSUFFICIENT LIVE DATA"),
        }

    rec = decision.get("recommendation", "KEEP CURRENT SETTINGS")
    if rec == "KEEP CURRENT SETTINGS":
        return {
            "today": "DO NOT CHANGE STRATEGY",
            "priority": 4,
            "expected_review": f"after {TARGET_LIVE_TRADES} trades",
            "do_not_touch": ["Stop", "Trailing", "BTC Filter", "Holding Time"],
            "reason": decision.get("reasons", ["No high-confidence change"])[0]
            if decision.get("reasons")
            else decision.get("reason", ""),
        }

    change_param = decision.get("change_parameter")
    safe_yes = [s for s in safe if s.get("status") == "YES"]
    do_not = [s["parameter"] for s in safe if s.get("status") != "YES"]

    if change_param and decision.get("change_from") is not None:
        return {
            "today": f"Change {change_param}",
            "change_from": decision.get("change_from"),
            "change_to": decision.get("change_to"),
            "only": True,
            "expected_improvement_pct": decision.get("expected_improvement_pct"),
            "confidence_pct": decision.get("confidence_pct"),
            "do_not_touch": do_not or ["Stop", "Trailing", "BTC Filter", "Holding Time"],
            "priority": 3 if safe_yes else 2,
        }

    return {
        "today": "MONITOR — NO PARAMETER CHANGES",
        "priority": 3,
        "do_not_touch": ["Stop", "Trailing", "BTC Filter", "Holding Time", "Entry Threshold"],
        "reason": decision.get("reason", ""),
    }
