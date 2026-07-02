"""Daily AI Agent report and intelligence section."""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from bot.ai_agent.counterfactual import build_counterfactual_matrix
from bot.ai_agent.learning import load_agent_state
from bot.ai_agent.memory import load_ai_decisions, load_ai_features
from bot.ai_agent.models import list_models
from bot.ai_agent.research import discover_patterns, shadow_experiment_recommendations


def _score_distribution(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    buckets = [
        ("0-20", 0, 20),
        ("21-40", 21, 40),
        ("41-60", 41, 60),
        ("61-80", 61, 80),
        ("81-100", 81, 100),
    ]
    out = []
    for label, lo, hi in buckets:
        count = sum(1 for r in rows if lo <= float(r["ai_score"]) <= hi)
        out.append({"bucket": label, "count": count})
    return out


def _top_reasons(decisions: list[sqlite3.Row], limit: int = 5) -> list[dict[str, Any]]:
    freq: Counter[str] = Counter()
    for row in decisions:
        if not row["explanation_json"]:
            continue
        expl = json.loads(row["explanation_json"])
        for reason in expl.get("reasons", []):
            freq[reason] += 1
    return [{"reason": k, "count": v} for k, v in freq.most_common(limit)]


def build_intelligence_section(conn: sqlite3.Connection) -> dict[str, Any]:
    decisions = load_ai_decisions(conn)
    features = load_ai_features(conn)
    state = load_agent_state()
    cf = build_counterfactual_matrix(decisions)

    scores = [float(d["score"]) for d in decisions]
    confs = [float(d["confidence"]) for d in decisions]

    regimes: dict[str, list[float]] = {}
    for f in features:
        regime = f["regime_label"] or "Unknown"
        regimes.setdefault(regime, []).append(float(f["pnl"] or 0))

    regime_pf = {}
    for name, pnls in regimes.items():
        wins = sum(p for p in pnls if p > 0)
        losses = abs(sum(p for p in pnls if p <= 0))
        regime_pf[name] = {
            "trades": len(pnls),
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "profit_factor": wins / losses if losses else float("inf"),
        }

    best_regime = None
    worst_regime = None
    if regime_pf:
        ranked = [r for r in regime_pf.items() if r[1]["trades"] >= 3]
        if ranked:
            best_regime = max(ranked, key=lambda x: x[1]["profit_factor"])[0]
            worst_regime = min(ranked, key=lambda x: x[1]["profit_factor"])[0]

    similar_pattern = None
    if decisions:
        top = max(decisions, key=lambda d: d["similar_count"] or 0)
        similar_pattern = {
            "trade_id": top["trade_id"],
            "similar_count": top["similar_count"],
            "historical_pf": top["historical_pf"],
        }

    return {
        "average_ai_score": round(statistics.mean(scores), 1) if scores else 0,
        "average_confidence": round(statistics.mean(confs), 1) if confs else 0,
        "top_reasons": _top_reasons(decisions),
        "top_similar_pattern": similar_pattern,
        "counterfactual_matrix": cf,
        "false_allow_pct": cf.get("false_allow_pct", 0),
        "false_skip_pct": cf.get("false_skip_pct", 0),
        "best_regime": best_regime,
        "worst_regime": worst_regime,
        "regime_stats": regime_pf,
        "learning_trend": state.get("feature_stats", {}),
        "pattern_frequency": state.get("pattern_frequency", {}),
        "today_new_trades": state.get("today_new_trades", 0),
    }


def build_daily_report(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = load_ai_features(conn)
    patterns = discover_patterns(rows)
    decisions = load_ai_decisions(conn)
    cf = build_counterfactual_matrix(decisions)

    decision_counts = {"ALLOW": 0, "SKIP": 0, "SHADOW": 0}
    for row in rows:
        d = row["decision"]
        if d in decision_counts:
            decision_counts[d] += 1

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "agent_version": "2.0",
            "mode": "observe_only",
            "signals_recorded": len(rows),
        },
        "models": list_models(),
        "score_distribution": _score_distribution(rows),
        "decisions": decision_counts,
        "counterfactual": cf,
        "patterns": patterns,
        "shadow_recommendations": shadow_experiment_recommendations(patterns),
        "intelligence": build_intelligence_section(conn),
        "disclaimer": (
            "Agent decisions are SHADOW-only. They do NOT affect paper or live trades "
            "until independently validated."
        ),
    }


def run_daily_sync(conn: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
    from bot.ai_agent.learning import run_daily_learning

    summary = run_daily_learning(conn)
    report = build_daily_report(conn)
    report["meta"]["synced_trades"] = summary.get("trades_processed", 0)
    report["meta"]["journals_written"] = summary.get("journals_written", 0)
    report["learning"] = summary
    return int(summary.get("trades_processed", 0)), report
