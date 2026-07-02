"""Parameter interaction analysis — display only, never recommend combos."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.report.advanced import _simulate_stop_pnl, _simulate_trailing_pnl
from bot.report.analytics import _metrics, fetch_v2_trades
from bot.strategy_review.entry import analyze_entry
from bot.strategy_review.stop_loss import analyze_stop
from bot.strategy_review.trailing import analyze_trailing


def _combo_metrics(
    conn: sqlite3.Connection,
    closed: list,
    *,
    entry_cap: float,
    stop_pct: float,
    activation: float,
    distance: float,
) -> dict[str, Any]:
    pnls: list[float] = []
    for trade in closed:
        if float(trade["entry_price"]) > entry_cap + 0.005:
            continue
        stop_pnl = _simulate_stop_pnl(conn, trade, stop_pct)
        trail_pnl = _simulate_trailing_pnl(conn, trade, activation, distance)
        pnls.append(max(stop_pnl, trail_pnl))
    return _metrics(pnls)


def analyze_combinations(
    conn: sqlite3.Connection,
    *,
    closed: list | None = None,
    entry_analysis: dict[str, Any] | None = None,
    stop_analysis: dict[str, Any] | None = None,
    trailing_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    closed = closed if closed is not None else fetch_v2_trades(conn, closed_only=True)
    entry_analysis = entry_analysis or analyze_entry(conn, closed=closed)
    stop_analysis = stop_analysis or analyze_stop(conn, closed=closed)
    trailing_analysis = trailing_analysis or analyze_trailing(conn, closed=closed)

    cur_entry = entry_analysis["current_entry"]
    cur_stop = stop_analysis["current_stop_pct"]
    cur_act = trailing_analysis["current"]["activation"]
    cur_dist = trailing_analysis["current"]["distance"]

    entry_rec = entry_analysis["recommendation"].get("recommended", cur_entry)
    stop_rec = stop_analysis["recommendation"].get("recommended_stop_pct", cur_stop)
    trail_rec = trailing_analysis["recommendation"]

    baseline = _combo_metrics(
        conn, closed, entry_cap=cur_entry, stop_pct=cur_stop,
        activation=cur_act, distance=cur_dist,
    )
    individual_entry = _combo_metrics(
        conn, closed, entry_cap=entry_rec, stop_pct=cur_stop,
        activation=cur_act, distance=cur_dist,
    )
    individual_stop = _combo_metrics(
        conn, closed, entry_cap=cur_entry, stop_pct=stop_rec,
        activation=cur_act, distance=cur_dist,
    )
    individual_trail = _combo_metrics(
        conn, closed, entry_cap=cur_entry, stop_pct=cur_stop,
        activation=trail_rec.get("recommended_activation", cur_act),
        distance=trail_rec.get("recommended_distance", cur_dist),
    )

    combos = [
        {
            "label": f"Entry {entry_rec:.2f} + Stop {stop_rec:.0f}%",
            "entry": entry_rec,
            "stop_pct": stop_rec,
            "activation": cur_act,
            "distance": cur_dist,
            **_combo_metrics(
                conn, closed, entry_cap=entry_rec, stop_pct=stop_rec,
                activation=cur_act, distance=cur_dist,
            ),
        },
        {
            "label": f"Entry {entry_rec:.2f} + Trailing {trail_rec.get('recommended_activation', cur_act):.2f}",
            "entry": entry_rec,
            "stop_pct": cur_stop,
            "activation": trail_rec.get("recommended_activation", cur_act),
            "distance": trail_rec.get("recommended_distance", cur_dist),
            **_combo_metrics(
                conn, closed, entry_cap=entry_rec, stop_pct=cur_stop,
                activation=trail_rec.get("recommended_activation", cur_act),
                distance=trail_rec.get("recommended_distance", cur_dist),
            ),
        },
        {
            "label": f"Entry {entry_rec:.2f} + Stop {stop_rec:.0f}% + Trailing {trail_rec.get('recommended_activation', cur_act):.2f}",
            "entry": entry_rec,
            "stop_pct": stop_rec,
            "activation": trail_rec.get("recommended_activation", cur_act),
            "distance": trail_rec.get("recommended_distance", cur_dist),
            **_combo_metrics(
                conn, closed, entry_cap=entry_rec, stop_pct=stop_rec,
                activation=trail_rec.get("recommended_activation", cur_act),
                distance=trail_rec.get("recommended_distance", cur_dist),
            ),
        },
    ]

    interactions: list[dict[str, Any]] = []
    for combo in combos:
        expected_additive = baseline["avg_pnl"]
        if combo["entry"] != cur_entry:
            expected_additive += individual_entry["avg_pnl"] - baseline["avg_pnl"]
        if combo["stop_pct"] != cur_stop:
            expected_additive += individual_stop["avg_pnl"] - baseline["avg_pnl"]
        if combo["activation"] != cur_act or combo["distance"] != cur_dist:
            expected_additive += individual_trail["avg_pnl"] - baseline["avg_pnl"]
        synergy = combo["avg_pnl"] - expected_additive
        interactions.append(
            {
                "label": combo["label"],
                "combo_avg_pnl": round(combo["avg_pnl"], 3),
                "combo_pf": round(combo["profit_factor"], 3)
                if combo["profit_factor"] != float("inf")
                else 99.9,
                "expected_additive_avg": round(expected_additive, 3),
                "synergy": round(synergy, 3),
                "interaction": "positive" if synergy > 0.5 else "negative" if synergy < -0.5 else "neutral",
                "recommend": False,
                "note": "Combinations shown for interaction only — not recommended",
            }
        )

    return {
        "baseline_avg_pnl": round(baseline["avg_pnl"], 3),
        "interactions": interactions,
        "policy": "Do not apply multiple parameter changes at once",
    }
