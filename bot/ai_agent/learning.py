"""Daily learning pipeline — observe-only intelligence sync."""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.ai_agent.confidence import compute_confidence
from bot.ai_agent.counterfactual import build_counterfactual_matrix, classify_counterfactual
from bot.ai_agent.decision import compute_ai_score, compute_decision
from bot.ai_agent.explain import build_explanation
from bot.ai_agent.features import enrich_for_similarity
from bot.ai_agent.journal import lesson_for_counterfactual, write_trade_journal
from bot.ai_agent.memory import (
    SOURCE_TABLE,
    load_ai_decisions,
    upsert_ai_decision,
    upsert_ai_feature,
)
from bot.ai_agent.research import discover_patterns
from bot.ai_agent.similarity import SimilarTradesEngine
from bot.config import BASE_DIR
from bot.perf.feature_store import load_enriched_features, sync_trade_features_incremental


def _state_path() -> Path:
    return BASE_DIR / "data" / "ai_agent_state.json"


def load_agent_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_agent_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _blend_score(rule_score: float, similar: dict[str, Any]) -> float:
    n = similar.get("similar_count", 0)
    if n < 5:
        return rule_score
    pf = similar.get("profit_factor", 1.0)
    if pf == float("inf"):
        pf = 3.0
    wr = similar.get("win_rate", 0.5)
    hist_score = min(100.0, max(0.0, wr * 50 + min(pf, 3) * 15))
    weight = min(0.45, n / 200)
    return round(rule_score * (1 - weight) + hist_score * weight, 1)


def process_all_trades(conn: sqlite3.Connection, *, run_brain: bool = True) -> dict[str, Any]:
    brain_summary: dict[str, Any] = {}
    if run_brain:
        from bot.trading_brain.learning import run_brain_learning

        brain_summary = run_brain_learning(conn)

    from bot.trading_brain.learning import load_trade_context

    sync_trade_features_incremental(conn)
    enriched_all = load_enriched_features(conn, sync=False)
    if not enriched_all:
        return {
            "trades_processed": 0,
            "journals_written": 0,
            "today_new_trades": 0,
            "state": {},
            "counterfactual": {},
            "patterns": [],
            "brain": brain_summary,
        }

    trades = conn.execute(
        f"""
        SELECT * FROM {SOURCE_TABLE}
        WHERE status = 'closed'
        ORDER BY entry_ts ASC
        """
    ).fetchall()
    trade_by_id = {int(t["id"]): t for t in trades}

    engine = SimilarTradesEngine(k=100)
    historical: list[dict[str, Any]] = []
    journals_written = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prev_state = load_agent_state()
    last_trade_id = int(prev_state.get("last_trade_id", 0))
    today_new = 0

    scores: list[float] = []
    confidences: list[float] = []
    pattern_freq: dict[str, int] = {}

    for enriched in enriched_all:
        tid = int(enriched["trade_id"])
        trade = trade_by_id.get(tid)
        if trade is None:
            continue

        if tid <= last_trade_id:
            existing = conn.execute(
                """
                SELECT 1 FROM ai_decisions
                WHERE trade_id = ? AND source_table = ?
                LIMIT 1
                """,
                (tid, SOURCE_TABLE),
            ).fetchone()
            if existing:
                historical.append({**enriched, "pnl": float(enriched.get("pnl") or 0)})
                continue

        engine.fit(historical)
        similar = engine.query(enriched, k=100)

        rule_score = compute_ai_score(enriched)
        ai_score = _blend_score(rule_score, similar)
        decision = compute_decision(ai_score)
        confidence = compute_confidence(ai_score=ai_score, similar=similar)
        explanation = build_explanation(
            enriched,
            similar,
            decision=decision,
            ai_score=ai_score,
            confidence=confidence,
        )
        brain_ctx = load_trade_context(conn, tid)
        if brain_ctx:
            explanation["brain_hints"] = brain_ctx.get("explainability", {})
        pnl = float(enriched.get("pnl") or 0)
        cf = classify_counterfactual(decision, pnl)

        feature_row = {
            **enriched,
            "ai_score": ai_score,
            "decision": decision,
        }
        upsert_ai_feature(conn, feature_row)

        pf = similar.get("profit_factor", 0)
        if pf == float("inf"):
            pf = 99.9

        upsert_ai_decision(
            conn,
            {
                "trade_id": tid,
                "source_table": SOURCE_TABLE,
                "score": ai_score,
                "confidence": confidence,
                "decision": decision,
                "similar_count": similar.get("similar_count", 0),
                "historical_pf": pf if similar.get("similar_count", 0) else None,
                "historical_wr": similar.get("win_rate") if similar.get("similar_count", 0) else None,
                "avg_pnl": similar.get("avg_pnl") if similar.get("similar_count", 0) else None,
                "counterfactual_result": cf,
                "explanation_json": json.dumps(explanation, ensure_ascii=False),
            },
        )

        today_new += 1
        write_trade_journal(
            trade_id=tid,
            payload={
                **enriched,
                "decision": decision,
                "ai_score": ai_score,
                "confidence": confidence,
                "similar_count": similar.get("similar_count", 0),
                "historical_pf": pf,
                "historical_wr": similar.get("win_rate"),
                "market_regime": enriched.get("market_regime"),
                "lesson": lesson_for_counterfactual(cf),
                "explanation": explanation,
            },
            closed_at=str(trade["closed_at"]) if trade["closed_at"] else today,
        )
        journals_written += 1

        scores.append(ai_score)
        confidences.append(confidence)
        for reason in explanation.get("positive", []):
            pattern_freq[reason] = pattern_freq.get(reason, 0) + 1

        historical.append({**enriched, "pnl": pnl})

    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_trade_id": max(int(r["trade_id"]) for r in enriched_all)
        if enriched_all
        else last_trade_id,
        "index_trade_count": len(historical),
        "feature_stats": {
            "avg_score": round(statistics.mean(scores), 2) if scores else 0,
            "avg_confidence": round(statistics.mean(confidences), 2) if confidences else 0,
        },
        "confidence_stats": {
            "mean": round(statistics.mean(confidences), 2) if confidences else 0,
            "min": round(min(confidences), 2) if confidences else 0,
            "max": round(max(confidences), 2) if confidences else 0,
        },
        "pattern_frequency": dict(sorted(pattern_freq.items(), key=lambda x: -x[1])[:20]),
        "today_new_trades": today_new,
    }
    save_agent_state(state)

    decisions = load_ai_decisions(conn)
    cf_matrix = build_counterfactual_matrix(decisions)
    patterns = discover_patterns(load_ai_features_from_decisions(conn))

    return {
        "trades_processed": len(enriched_all),
        "journals_written": journals_written,
        "today_new_trades": today_new,
        "state": state,
        "counterfactual": cf_matrix,
        "patterns": patterns,
        "brain": brain_summary,
        "incremental_from_trade_id": last_trade_id,
    }


def load_ai_features_from_decisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    from bot.ai_agent.memory import load_ai_features

    return load_ai_features(conn)


def run_daily_learning(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = process_all_trades(conn)
    conn.commit()
    return summary
