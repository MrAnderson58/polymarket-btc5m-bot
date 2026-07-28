"""Expectancy breakdown for S55 NEGATIVE_EXPECTANCY candidates."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.expectancy_intelligence.stats import (
    avg_winner_loser,
    mean,
    profit_factor_from_pnls,
    stdev,
)
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    GATE_NEGATIVE_EXPECTANCY,
    S55_MIN_SIMILAR,
    estimate_from_neighbors,
    find_similar_trades,
)

_TABLE = "market_events_trade_features_s55"

_REASON_LABELS = {
    "low_win_probability": "Low win probability",
    "average_loss_too_large": "Average loss too large",
    "average_win_too_small": "Average win too small",
    "too_few_similar": "Too few similar trades",
    "high_variance": "High variance",
    "other": "Other",
}


def classify_negative_ev_reason(
    neighbors: list[dict[str, Any]],
    estimate: dict[str, Any],
) -> str:
    n = int(estimate.get("n") or 0)
    if n < S55_MIN_SIMILAR:
        return "too_few_similar"
    winrate = float(estimate.get("winrate") or 0.0)
    pnls = [float(r.get("pnl_pct") or 0.0) for r in neighbors]
    if winrate < 45.0:
        return "low_win_probability"
    avg_win, avg_loss = avg_winner_loser(pnls)
    if abs(avg_loss) > abs(avg_win) and abs(avg_loss) > 1.0:
        return "average_loss_too_large"
    if abs(avg_win) < 0.5 and winrate >= 45.0:
        return "average_win_too_small"
    if len(pnls) >= 3 and stdev(pnls) > 3.0:
        return "high_variance"
    return "other"


def neighbor_metrics(neighbors: list[dict[str, Any]], estimate: dict[str, Any]) -> dict[str, Any]:
    pnls = [float(r.get("pnl_pct") or 0.0) for r in neighbors]
    avg_win, avg_loss = avg_winner_loser(pnls)
    sims = [float(r.get("similarity") or 0.0) for r in neighbors]
    pf = profit_factor_from_pnls(pnls)
    return {
        "expected_pnl_pct": float(estimate.get("expected_pnl_pct") or 0.0),
        "win_probability": float(estimate.get("winrate") or 0.0),
        "avg_winner_pct": avg_win,
        "avg_loser_pct": avg_loss,
        "profit_factor": pf,
        "expectancy": float(estimate.get("expected_pnl_pct") or 0.0),
        "similar_count": int(estimate.get("n") or 0),
        "similarity_score": round(mean(sims), 4) if sims else 0.0,
    }


def load_negative_expectancy_rows(conn: Any, since_ts: int) -> list[Any]:
    try:
        return list(
            conn.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE created_at >= ?
                  AND gate_decision = ?
                ORDER BY created_at DESC
                """,
                (since_ts, GATE_NEGATIVE_EXPECTANCY),
            ).fetchall()
        )
    except Exception:
        return []


def build_expectancy_breakdown(conn: Any, *, since_hours: float = 24.0) -> dict[str, Any]:
    since_ts = int(time.time()) - int(since_hours * 3600)
    rows = load_negative_expectancy_rows(conn, since_ts)
    items: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        feats = row_to_features(row)
        neighbors = find_similar_trades(conn, feats)
        estimate = estimate_from_neighbors(neighbors)
        metrics = neighbor_metrics(neighbors, estimate)
        reason_key = classify_negative_ev_reason(neighbors, estimate)
        reasons[reason_key] += 1
        items.append(
            {
                "symbol": str(row["symbol"] or feats.get("symbol") or ""),
                "direction": str(row["direction"] or feats.get("direction") or ""),
                **metrics,
                "primary_reason": _REASON_LABELS[reason_key],
                "primary_reason_key": reason_key,
                "created_at": int(row["created_at"]),
            }
        )
    total = len(items)
    summary_pct: dict[str, float] = {}
    for key, label in _REASON_LABELS.items():
        summary_pct[label] = round(100.0 * reasons.get(key, 0) / total, 1) if total else 0.0
    return {
        "since_hours": since_hours,
        "since_ts": since_ts,
        "n_rejected": total,
        "items": items,
        "reason_counts": dict(reasons),
        "reason_summary_pct": summary_pct,
    }


def format_expectancy_breakdown(conn: Any, *, since_hours: float = 24.0) -> str:
    data = build_expectancy_breakdown(conn, since_hours=since_hours)
    lines = [
        "EXPECTANCY BREAKDOWN (NEGATIVE_EXPECTANCY candidates)",
        f"  window_hours={data['since_hours']:.0f}  n={data['n_rejected']}",
        "",
    ]
    for it in data["items"]:
        pf = it.get("profit_factor")
        pf_s = "n/a" if pf is None else (f"{pf:.2f}" if pf != float("inf") else "inf")
        lines.append(
            f"{it['symbol']} {it['direction']}  "
            f"EV={it['expectancy']:+.3f}%  win={it['win_probability']:.1f}%  "
            f"avgW={it['avg_winner_pct']:+.3f}% avgL={it['avg_loser_pct']:+.3f}%  "
            f"PF={pf_s}  similar={it['similar_count']} sim={it['similarity_score']:.3f}  "
            f"reason={it['primary_reason']}"
        )
    lines.extend(["", "NEGATIVE EXPECTANCY", ""])
    for label in _REASON_LABELS.values():
        pct = data["reason_summary_pct"].get(label, 0.0)
        dots = "." * max(1, 28 - len(label))
        lines.append(f"{label} {dots} {pct:.0f}%")
    return "\n".join(lines)
