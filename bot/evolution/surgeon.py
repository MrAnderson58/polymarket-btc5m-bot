"""Trading Strategy Surgeon — daily analysis of last 500 trades.

Answers 6 questions and produces exactly ONE actionable recommendation.
Read-only: does not change execution or strategy.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.evolution.metrics import lane_metrics

SURGEON_WINDOW = 500

ENTRY_BUCKETS = [round(x * 0.01, 2) for x in range(34, 41)]
HOLDING_BUCKETS_SEC = (10, 20, 30, 45, 60, 90)
STOP_LOSS_LEVELS = (-8.0, -10.0, -12.0, -15.0, -18.0, -20.0)
TRAILING_ACTIVATION_LEVELS = (0.02, 0.03, 0.04, 0.05)


def _trade_pnl(trade: Any) -> float:
    if trade["pnl_percent"] is not None:
        return float(trade["pnl_percent"])
    entry = float(trade["entry_price"])
    exit_p = float(trade["exit_price"] if trade["exit_price"] is not None else entry)
    return (exit_p - entry) / entry * 100.0


def _pf(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p <= 0))
    if not losses:
        return 99.0 if wins > 0 else 0.0
    return round(wins / losses, 3)


def fetch_last_n_trades(conn: sqlite3.Connection, n: int = SURGEON_WINDOW) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM early_reversion_v2_trades
        WHERE status = 'closed'
        ORDER BY entry_ts DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()


def _biggest_loss_source(trades: list[Any]) -> dict[str, Any]:
    """What currently produces the most losses?"""
    by_reason: dict[str, list[float]] = {}
    for t in trades:
        reason = t["exit_reason"] or "OTHER"
        by_reason.setdefault(reason, []).append(_trade_pnl(t))

    worst_reason = None
    worst_total_loss = 0.0
    for reason, pnls in by_reason.items():
        total_loss = abs(sum(p for p in pnls if p <= 0))
        if total_loss > worst_total_loss:
            worst_total_loss = total_loss
            worst_reason = reason

    by_entry: dict[float, list[float]] = {}
    for t in trades:
        price = round(float(t["entry_price"]), 2)
        nearest = min(ENTRY_BUCKETS, key=lambda p: abs(p - price))
        if abs(nearest - price) <= 0.005:
            by_entry.setdefault(nearest, []).append(_trade_pnl(t))

    worst_entry = None
    worst_entry_pf = 99.0
    for price, pnls in by_entry.items():
        if len(pnls) >= 5:
            pf = _pf(pnls)
            if pf < worst_entry_pf:
                worst_entry_pf = pf
                worst_entry = price

    return {
        "exit_reason": worst_reason,
        "exit_reason_loss": round(worst_total_loss, 2),
        "worst_entry_price": worst_entry,
        "worst_entry_pf": worst_entry_pf,
    }


def _biggest_profit_source(trades: list[Any]) -> dict[str, Any]:
    """What currently produces the most profit?"""
    by_reason: dict[str, list[float]] = {}
    for t in trades:
        reason = t["exit_reason"] or "OTHER"
        by_reason.setdefault(reason, []).append(_trade_pnl(t))

    best_reason = None
    best_total_profit = 0.0
    for reason, pnls in by_reason.items():
        total_profit = sum(p for p in pnls if p > 0)
        if total_profit > best_total_profit:
            best_total_profit = total_profit
            best_reason = reason

    by_entry: dict[float, list[float]] = {}
    for t in trades:
        price = round(float(t["entry_price"]), 2)
        nearest = min(ENTRY_BUCKETS, key=lambda p: abs(p - price))
        if abs(nearest - price) <= 0.005:
            by_entry.setdefault(nearest, []).append(_trade_pnl(t))

    best_entry = None
    best_entry_pf = 0.0
    for price, pnls in by_entry.items():
        if len(pnls) >= 5:
            pf = _pf(pnls)
            if pf > best_entry_pf:
                best_entry_pf = pf
                best_entry = price

    return {
        "exit_reason": best_reason,
        "exit_reason_profit": round(best_total_profit, 2),
        "best_entry_price": best_entry,
        "best_entry_pf": best_entry_pf,
    }


def _parameter_impact(trades: list[Any]) -> dict[str, Any]:
    """Which parameter hurts PF most? Which helps most?"""
    all_pnls = [_trade_pnl(t) for t in trades]
    baseline_pf = _pf(all_pnls)

    # Entry threshold impact: compare PF excluding worst entry bucket
    entry_pfs: dict[float, float] = {}
    by_entry: dict[float, list[Any]] = {}
    for t in trades:
        price = round(float(t["entry_price"]), 2)
        nearest = min(ENTRY_BUCKETS, key=lambda p: abs(p - price))
        if abs(nearest - price) <= 0.005:
            by_entry.setdefault(nearest, []).append(t)

    for price, bucket_trades in by_entry.items():
        if len(bucket_trades) >= 5:
            entry_pfs[price] = _pf([_trade_pnl(t) for t in bucket_trades])

    # Holding time impact
    holding_pfs: dict[int, float] = {}
    for max_sec in HOLDING_BUCKETS_SEC:
        subset = [t for t in trades if t["holding_time_seconds"] and float(t["holding_time_seconds"]) <= max_sec]
        if len(subset) >= 10:
            holding_pfs[max_sec] = _pf([_trade_pnl(t) for t in subset])

    # Exit reason impact
    exit_pfs: dict[str, float] = {}
    by_exit: dict[str, list[float]] = {}
    for t in trades:
        reason = t["exit_reason"] or "OTHER"
        by_exit.setdefault(reason, []).append(_trade_pnl(t))
    for reason, pnls in by_exit.items():
        if len(pnls) >= 5:
            exit_pfs[reason] = _pf(pnls)

    # Find worst and best
    hurts_pf = None
    hurts_pf_val = baseline_pf
    helps_pf = None
    helps_pf_val = baseline_pf

    for price, pf in entry_pfs.items():
        if pf < hurts_pf_val:
            hurts_pf_val = pf
            hurts_pf = {"parameter": "entry_threshold", "value": price, "pf": pf}
        if pf > helps_pf_val:
            helps_pf_val = pf
            helps_pf = {"parameter": "entry_threshold", "value": price, "pf": pf}

    for reason, pf in exit_pfs.items():
        if pf < hurts_pf_val:
            hurts_pf_val = pf
            hurts_pf = {"parameter": "exit_reason", "value": reason, "pf": pf}
        if pf > helps_pf_val:
            helps_pf_val = pf
            helps_pf = {"parameter": "exit_reason", "value": reason, "pf": pf}

    return {
        "baseline_pf": baseline_pf,
        "hurts_pf": hurts_pf,
        "helps_pf": helps_pf,
        "entry_pfs": entry_pfs,
        "exit_pfs": exit_pfs,
        "holding_pfs": holding_pfs,
    }


def _fresh_start_recommendation(
    trades: list[Any],
    impact: dict[str, Any],
) -> str:
    """If launching today from scratch, what would I change?"""
    hurts = impact.get("hurts_pf")
    helps = impact.get("helps_pf")
    baseline = impact.get("baseline_pf", 0)

    if hurts and hurts["parameter"] == "entry_threshold":
        return (
            f"Exclude entry ≥{hurts['value']:.2f} (PF {hurts['pf']:.2f} vs baseline {baseline:.2f})"
        )
    if hurts and hurts["parameter"] == "exit_reason":
        return (
            f"Reduce {hurts['value']} exits (PF {hurts['pf']:.2f} vs baseline {baseline:.2f})"
        )
    if helps and helps["parameter"] == "entry_threshold":
        return (
            f"Focus entries at {helps['value']:.2f} (PF {helps['pf']:.2f} vs baseline {baseline:.2f})"
        )
    return "No clear improvement — current params are near optimal."


def _next_shadow_recommendation(
    impact: dict[str, Any],
    loss_source: dict[str, Any],
    profit_source: dict[str, Any],
) -> dict[str, Any]:
    """Single recommendation for what to try in Shadow next."""
    hurts = impact.get("hurts_pf")
    baseline = impact.get("baseline_pf", 0)
    entry_pfs = impact.get("entry_pfs", {})

    # Find optimal entry (highest PF with sufficient volume)
    if entry_pfs:
        best_price = max(entry_pfs.keys(), key=lambda p: entry_pfs[p])
        worst_price = min(entry_pfs.keys(), key=lambda p: entry_pfs[p])
        if entry_pfs[best_price] > baseline * 1.1:
            return {
                "parameter": "entry_threshold",
                "action": f"Lower max entry to {best_price:.2f}",
                "reason": f"PF {entry_pfs[best_price]:.2f} at {best_price:.2f} vs {baseline:.2f} baseline",
                "from_value": worst_price,
                "to_value": best_price,
            }

    if hurts and hurts["parameter"] == "exit_reason" and hurts["value"] == "TIME_STOP":
        return {
            "parameter": "time_stop",
            "action": "Reduce TIME_STOP duration",
            "reason": f"TIME_STOP PF {hurts['pf']:.2f} drags baseline {baseline:.2f}",
        }

    if hurts and hurts["parameter"] == "exit_reason" and hurts["value"] == "STOP_LOSS":
        return {
            "parameter": "stop_loss",
            "action": "Widen stop loss (less triggered)",
            "reason": f"STOP_LOSS PF {hurts['pf']:.2f} drags baseline {baseline:.2f}",
        }

    return {
        "parameter": "none",
        "action": "No clear next shadow — accumulate more data",
        "reason": f"Baseline PF {baseline:.2f} is stable",
    }


def run_surgeon(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full surgeon analysis on last 500 trades."""
    trades = fetch_last_n_trades(conn, SURGEON_WINDOW)
    if not trades:
        return {
            "sample_size": 0,
            "recommendation": {
                "parameter": None,
                "value": None,
                "decision": "KEEP",
                "reason": "Insufficient data — no closed trades.",
                "confidence": 0,
            },
        }

    all_pnls = [_trade_pnl(t) for t in trades]
    metrics = lane_metrics(all_pnls)
    loss_source = _biggest_loss_source(trades)
    profit_source = _biggest_profit_source(trades)
    impact = _parameter_impact(trades)
    fresh_start = _fresh_start_recommendation(trades, impact)
    next_shadow = _next_shadow_recommendation(impact, loss_source, profit_source)

    recommendation = _build_structured_recommendation(next_shadow, impact)

    return {
        "sample_size": len(trades),
        "metrics": metrics,
        "q1_fresh_start": fresh_start,
        "q2_biggest_loss": loss_source,
        "q3_biggest_profit": profit_source,
        "q4_hurts_pf": impact.get("hurts_pf"),
        "q5_helps_pf": impact.get("helps_pf"),
        "q6_next_shadow": next_shadow,
        "recommendation": recommendation,
        "detail": {
            "baseline_pf": impact["baseline_pf"],
            "entry_pfs": impact["entry_pfs"],
            "exit_pfs": impact["exit_pfs"],
        },
    }


def _build_structured_recommendation(
    next_shadow: dict[str, Any],
    impact: dict[str, Any],
) -> dict[str, Any]:
    """Build a structured recommendation dict for Council consumption."""
    param = next_shadow.get("parameter")
    if param in (None, "none"):
        return {
            "parameter": None,
            "value": None,
            "decision": "KEEP",
            "reason": next_shadow.get("reason", "No clear improvement"),
            "confidence": 0,
        }

    from_value = next_shadow.get("from_value")
    to_value = next_shadow.get("to_value")

    # Normalize parameter names
    param_map = {
        "entry_threshold": "entry",
        "stop_loss": "stop_loss",
        "time_stop": "stop_loss",
        "trailing_activation": "trailing_activation",
        "trailing_distance": "trailing_distance",
    }
    normalized_param = param_map.get(param, param)

    confidence = 0
    baseline = impact.get("baseline_pf", 1.0)
    if to_value is not None and baseline > 0:
        entry_pfs = impact.get("entry_pfs", {})
        if to_value in entry_pfs:
            improvement = (entry_pfs[to_value] - baseline) / baseline
            confidence = min(95, int(improvement * 200))

    return {
        "parameter": normalized_param,
        "value": float(to_value) if to_value is not None else None,
        "from_value": float(from_value) if from_value is not None else None,
        "to_value": float(to_value) if to_value is not None else None,
        "direction": "lower" if to_value is not None and from_value is not None and to_value < from_value else "higher",
        "decision": "CHANGE",
        "reason": next_shadow.get("reason", next_shadow.get("action", "")),
        "confidence": max(confidence, 50),
    }


def _recommendation_text(rec: Any) -> str:
    """Convert recommendation to human-readable string (handles both dict and str)."""
    if isinstance(rec, str):
        return rec
    if isinstance(rec, dict):
        if rec.get("decision") == "KEEP" or rec.get("parameter") is None:
            return rec.get("reason", "KEEP")
        param = rec.get("parameter", "?")
        value = rec.get("value")
        reason = rec.get("reason", "")
        if value is not None:
            return f"{param} → {value} ({reason})"
        return reason or f"Change {param}"
    return str(rec)


def render_surgeon_block(surgeon: dict[str, Any]) -> str:
    """CLI output for daily pipeline."""
    if not surgeon.get("sample_size"):
        return ""
    lines = [
        "==========================",
        "",
        "STRATEGY SURGEON",
        "",
        f"Sample: last {surgeon['sample_size']} trades",
        f"PF {surgeon['metrics']['pf']} | WR {surgeon['metrics']['wr']:.0f}% | DD {surgeon['metrics']['dd']:.1f}",
        "",
    ]

    lines.append("1. Fresh start:")
    lines.append(f"   {surgeon['q1_fresh_start']}")
    lines.append("")

    loss = surgeon["q2_biggest_loss"]
    lines.append("2. Biggest loss source:")
    lines.append(f"   {loss['exit_reason']} (total −{loss['exit_reason_loss']:.1f}%)")
    lines.append("")

    profit = surgeon["q3_biggest_profit"]
    lines.append("3. Biggest profit source:")
    lines.append(f"   {profit['exit_reason']} (total +{profit['exit_reason_profit']:.1f}%)")
    lines.append("")

    hurts = surgeon.get("q4_hurts_pf")
    if hurts:
        lines.append("4. Hurts PF:")
        lines.append(f"   {hurts['parameter']} = {hurts['value']} → PF {hurts['pf']:.2f}")
    else:
        lines.append("4. Hurts PF: none identified")
    lines.append("")

    helps = surgeon.get("q5_helps_pf")
    if helps:
        lines.append("5. Helps PF:")
        lines.append(f"   {helps['parameter']} = {helps['value']} → PF {helps['pf']:.2f}")
    else:
        lines.append("5. Helps PF: none identified")
    lines.append("")

    shadow = surgeon["q6_next_shadow"]
    lines.append("6. Next shadow:")
    lines.append(f"   {shadow['action']}")
    lines.append(f"   ({shadow['reason']})")
    lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"RECOMMENDATION: {_recommendation_text(surgeon['recommendation'])}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("==========================")
    return "\n".join(lines)


def render_surgeon_section(surgeon: dict[str, Any]) -> list[str]:
    """Report §48 section."""
    if not surgeon.get("sample_size"):
        return ["", "_No data for Strategy Surgeon._", ""]

    lines = [
        "",
        f"**Sample:** last {surgeon['sample_size']} trades",
        f"**Baseline PF:** {surgeon['metrics']['pf']} | "
        f"**WR:** {surgeon['metrics']['wr']:.0f}% | "
        f"**DD:** {surgeon['metrics']['dd']:.1f}",
        "",
        f"**1. Fresh start:** {surgeon['q1_fresh_start']}",
        "",
    ]

    loss = surgeon["q2_biggest_loss"]
    lines.append(
        f"**2. Biggest loss:** {loss['exit_reason']} "
        f"(−{loss['exit_reason_loss']:.1f}%)"
    )
    if loss.get("worst_entry_price"):
        lines.append(f"   Worst entry bucket: {loss['worst_entry_price']:.2f} (PF {loss['worst_entry_pf']:.2f})")
    lines.append("")

    profit = surgeon["q3_biggest_profit"]
    lines.append(
        f"**3. Biggest profit:** {profit['exit_reason']} "
        f"(+{profit['exit_reason_profit']:.1f}%)"
    )
    if profit.get("best_entry_price"):
        lines.append(f"   Best entry bucket: {profit['best_entry_price']:.2f} (PF {profit['best_entry_pf']:.2f})")
    lines.append("")

    hurts = surgeon.get("q4_hurts_pf")
    if hurts:
        lines.append(f"**4. Hurts PF:** {hurts['parameter']} = {hurts['value']} → PF {hurts['pf']:.2f}")
    else:
        lines.append("**4. Hurts PF:** none identified")
    lines.append("")

    helps = surgeon.get("q5_helps_pf")
    if helps:
        lines.append(f"**5. Helps PF:** {helps['parameter']} = {helps['value']} → PF {helps['pf']:.2f}")
    else:
        lines.append("**5. Helps PF:** none identified")
    lines.append("")

    shadow = surgeon["q6_next_shadow"]
    lines.append(f"**6. Next shadow:** {shadow['action']}")
    lines.append(f"   _{shadow['reason']}_")
    lines.append("")
    lines.append(f"**RECOMMENDATION:** {_recommendation_text(surgeon['recommendation'])}")
    lines.append("")
    lines.append("_Strategy Surgeon: observe-only, never changes execution._")
    return lines
