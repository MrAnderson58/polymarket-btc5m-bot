"""Daily intelligence markdown report."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.research.market_events.expectancy_intelligence.breakdown import (
    build_expectancy_breakdown,
)
from bot.research.market_events.expectancy_intelligence.feature_importance import (
    build_feature_importance,
)
from bot.research.market_events.signal_intelligence.gate_funnel_report import (
    build_fear_greed_bucket_report,
    build_unified_gate_funnel,
)
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    gate_stats_today,
)

_TABLE = "market_events_trade_features_s55"


def _day_start(ts: int | None = None) -> int:
    dt = datetime.fromtimestamp(ts or time.time())
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _avg_field(rows: list[Any], field: str) -> float | None:
    vals: list[float] = []
    for r in rows:
        try:
            if r[field] is not None:
                vals.append(float(r[field]))
        except (KeyError, TypeError, ValueError):
            pass
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def build_daily_intelligence(conn: Any, *, day_ts: int | None = None) -> dict[str, Any]:
    start = _day_start(day_ts)
    end = start + 86400
    day_label = datetime.fromtimestamp(start).strftime("%Y-%m-%d")

    try:
        day_rows = list(
            conn.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE created_at >= ? AND created_at < ?
                """,
                (start, end),
            ).fetchall()
        )
    except Exception:
        day_rows = []

    observed = len(day_rows)
    opened = sum(1 for r in day_rows if r["paper_trade_id"] is not None)
    rejected = sum(
        1 for r in day_rows
        if str(r["gate_decision"] or "").startswith("reject")
        or str(r["gate_decision"] or "") in ("NEGATIVE_EXPECTANCY", "MAX_OPEN", "REGIME_BLOCK")
    )

    gate = gate_stats_today(conn, day_start=start)
    funnel = build_unified_gate_funnel(conn, since_ts=start)
    breakdown = build_expectancy_breakdown(conn, since_hours=24.0)
    fg = build_fear_greed_bucket_report(conn, since_ts=start - 86400 * 30)
    feat_imp = build_feature_importance(conn)

    avg_ev = _avg_field(day_rows, "gate_expected_pnl_pct")
    avg_sim = _avg_field(day_rows, "similar_count")

    conf_vals: list[float] = []
    win_probs: list[float] = []
    for r in day_rows:
        from bot.research.market_events.expectancy_intelligence.row_features import (
            parse_features_json,
        )
        blob = parse_features_json(r["features_json"])
        c = blob.get("decision_confidence") or blob.get("confidence")
        if c is not None:
            try:
                conf_vals.append(float(c))
            except (TypeError, ValueError):
                pass
        est = blob.get("gate_estimate")
        if isinstance(est, dict) and est.get("winrate") is not None:
            try:
                win_probs.append(float(est["winrate"]))
            except (TypeError, ValueError):
                pass

    best_rejected: dict[str, Any] | None = None
    worst_accepted: dict[str, Any] | None = None
    for r in day_rows:
        g = str(r["gate_decision"] or "")
        ev = float(r["gate_expected_pnl_pct"] or 0.0)
        if g == "NEGATIVE_EXPECTANCY":
            if best_rejected is None or ev > float(best_rejected.get("ev") or -1e9):
                best_rejected = {
                    "symbol": r["symbol"],
                    "direction": r["direction"],
                    "ev": ev,
                    "gate": g,
                }
        if r["paper_trade_id"] is not None and g not in ("DISABLED",):
            pnl = float(r["pnl_pct"] or 0.0) if r["pnl_pct"] is not None else ev
            if worst_accepted is None or pnl < float(worst_accepted.get("pnl") or 1e9):
                worst_accepted = {
                    "symbol": r["symbol"],
                    "direction": r["direction"],
                    "pnl": pnl,
                    "gate": g,
                }

    improving = [f["feature"] for f in feat_imp["features"][:3]]
    degrading = [f["feature"] for f in feat_imp["features"][-3:]]

    return {
        "day": day_label,
        "day_start": start,
        "observed": observed,
        "opened": opened,
        "rejected": rejected,
        "top_rejection_reasons": gate.get("reasons") or {},
        "avg_ev": avg_ev,
        "avg_similarity": avg_sim,
        "avg_confidence": round(sum(conf_vals) / len(conf_vals), 4) if conf_vals else None,
        "avg_win_probability": round(sum(win_probs) / len(win_probs), 2) if win_probs else None,
        "best_candidate_rejected": best_rejected,
        "worst_accepted": worst_accepted,
        "funnel": funnel,
        "breakdown_summary": breakdown.get("reason_summary_pct"),
        "fear_greed": fg,
        "feature_top": feat_imp["features"][:5],
        "improving_metrics": improving,
        "degrading_metrics": degrading,
    }


def format_daily_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# Daily Intelligence — {data['day']}",
        "",
        "## Activity",
        f"- Trades observed: {data['observed']}",
        f"- Trades opened: {data['opened']}",
        f"- Trades rejected: {data['rejected']}",
        "",
        "## Top rejection reasons",
    ]
    for k, v in sorted((data.get("top_rejection_reasons") or {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.extend(
        [
            "",
            "## Expectancy & similarity",
            f"- Average EV: {data.get('avg_ev')}",
            f"- Average similarity count: {data.get('avg_similarity')}",
            f"- Average confidence: {data.get('avg_confidence')}",
            f"- Average win probability: {data.get('avg_win_probability')}",
            "",
            "## Candidates",
            f"- Best rejected: {data.get('best_candidate_rejected')}",
            f"- Worst accepted: {data.get('worst_accepted')}",
            "",
            "## Negative EV breakdown (24h)",
        ]
    )
    for label, pct in (data.get("breakdown_summary") or {}).items():
        lines.append(f"- {label}: {pct}%")
    lines.extend(["", "## Fear & Greed (30d closed)"])
    fg = data.get("fear_greed") or {}
    for bucket, stats in (fg.get("buckets") or {}).items():
        lines.append(f"- {bucket}: n={stats.get('n')} avg_pnl={stats.get('avg_pnl_pct')}")
    lines.extend(["", "## Feature influence (top 5)"])
    for f in data.get("feature_top") or []:
        lines.append(f"- {f['feature']}: influence={f['influence']}")
    lines.extend(
        [
            "",
            "## Metric shifts",
            f"- Top improving: {', '.join(data.get('improving_metrics') or [])}",
            f"- Top degrading: {', '.join(data.get('degrading_metrics') or [])}",
        ]
    )
    return "\n".join(lines)


def write_daily_intelligence_report(
    conn: Any,
    *,
    day_ts: int | None = None,
    reports_root: Path | None = None,
) -> Path:
    data = build_daily_intelligence(conn, day_ts=day_ts)
    root = reports_root or Path("reports/daily")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{data['day']}.md"
    path.write_text(format_daily_markdown(data), encoding="utf-8")
    return path


def format_daily_intelligence_cli(conn: Any, *, day_ts: int | None = None) -> str:
    data = build_daily_intelligence(conn, day_ts=day_ts)
    path = write_daily_intelligence_report(conn, day_ts=day_ts, reports_root=Path("reports/daily"))
    return format_daily_markdown(data) + f"\n\nSaved: {path}"
