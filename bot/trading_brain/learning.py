"""Learning Engine — sync brain state after each closed trade."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from bot.ai_agent.features import enrich_for_similarity
from bot.perf.feature_store import load_enriched_features, sync_trade_features_incremental
from bot.trading_brain.constants import BRAIN_VERSION, SOURCE_TABLE
from bot.trading_brain.context import build_decision_context, build_explainability_context
from bot.trading_brain.knowledge import (
    discover_causal_knowledge,
    load_knowledge,
    match_knowledge_for_signal,
    persist_knowledge,
)
from bot.trading_brain.memory import get_learning_state, set_learning_state, sync_memory_engine
from bot.trading_brain.similarity import SimilarityEngineV2


def upsert_trade_context(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO brain_trade_context (
            trade_id, source_table,
            decision_context_json, explainability_json,
            similar_stats_json, knowledge_refs_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_id, source_table) DO UPDATE SET
            decision_context_json = excluded.decision_context_json,
            explainability_json = excluded.explainability_json,
            similar_stats_json = excluded.similar_stats_json,
            knowledge_refs_json = excluded.knowledge_refs_json,
            built_at = datetime('now')
        """,
        (
            row["trade_id"],
            row["source_table"],
            json.dumps(row["decision_context"], ensure_ascii=False),
            json.dumps(row["explainability"], ensure_ascii=False),
            json.dumps(row["similar_stats"], ensure_ascii=False),
            json.dumps(row["knowledge_refs"], ensure_ascii=False),
        ),
    )


def load_trade_context(
    conn: sqlite3.Connection,
    trade_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM brain_trade_context
        WHERE trade_id = ? AND source_table = ?
        """,
        (trade_id, SOURCE_TABLE),
    ).fetchone()
    if row is None:
        return None
    return {
        "trade_id": row["trade_id"],
        "decision_context": json.loads(row["decision_context_json"]),
        "explainability": json.loads(row["explainability_json"]),
        "similar_stats": json.loads(row["similar_stats_json"] or "{}"),
        "knowledge_refs": json.loads(row["knowledge_refs_json"] or "[]"),
    }


def run_brain_learning(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Incremental Trading Brain sync:
    feature cache → process only new trades → rebuild knowledge from cache.
    """
    memory_counts = sync_memory_engine(conn)
    prev = get_learning_state(conn, "last_processed_trade_id")
    last_id = int(prev.get("trade_id", 0))

    sync_trade_features_incremental(conn)
    enriched_all = load_enriched_features(conn, sync=False)
    if not enriched_all:
        return {
            "brain_version": BRAIN_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "trades_processed": 0,
            "contexts_built": 0,
            "new_trades_since_last": 0,
            "knowledge_links": 0,
            "memory": memory_counts,
            "mode": "observe_only",
            "incremental_from_trade_id": last_id,
        }

    knowledge_cache = load_knowledge(conn, limit=100)
    engine = SimilarityEngineV2(k=100)
    historical: list[dict[str, Any]] = []
    contexts_built = 0
    new_since_last = 0

    memory_snapshot = {
        "trade_memory_count": memory_counts.get("trade", 0),
        "experiment_memory_count": memory_counts.get("experiment", 0),
        "report_memory_count": memory_counts.get("report", 0),
    }

    for enriched in enriched_all:
        tid = int(enriched["trade_id"])
        row_with_outcome = {**enriched, "pnl": enriched.get("pnl")}

        has_context = conn.execute(
            """
            SELECT 1 FROM brain_trade_context
            WHERE trade_id = ? AND source_table = ?
            LIMIT 1
            """,
            (tid, SOURCE_TABLE),
        ).fetchone()

        if tid <= last_id and has_context:
            historical.append(row_with_outcome)
            continue

        engine.fit(historical)
        similar = engine.query(enriched, k=100)
        knowledge = match_knowledge_for_signal(conn, enriched, cached=knowledge_cache)
        decision_ctx = build_decision_context(
            trade_id=tid,
            features=enriched,
            similar=similar,
            knowledge=knowledge,
            memory_refs=memory_snapshot,
        )
        explain_ctx = build_explainability_context(enriched, similar, knowledge)

        upsert_trade_context(
            conn,
            {
                "trade_id": tid,
                "source_table": SOURCE_TABLE,
                "decision_context": decision_ctx,
                "explainability": explain_ctx,
                "similar_stats": similar,
                "knowledge_refs": knowledge,
            },
        )
        contexts_built += 1
        new_since_last += 1
        historical.append(row_with_outcome)

    all_rows = [{**r, "pnl": r.get("pnl")} for r in enriched_all]
    links = discover_causal_knowledge(all_rows)
    knowledge_count = persist_knowledge(conn, links)

    if enriched_all:
        last_id = max(int(r["trade_id"]) for r in enriched_all)

    summary = {
        "brain_version": BRAIN_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "trades_processed": len(enriched_all),
        "contexts_built": contexts_built,
        "new_trades_since_last": new_since_last,
        "knowledge_links": knowledge_count,
        "memory": memory_counts,
        "mode": "observe_only",
        "incremental_from_trade_id": int(prev.get("trade_id", 0)),
    }
    set_learning_state(conn, "last_processed_trade_id", {"trade_id": last_id})
    set_learning_state(conn, "last_run", summary)
    return summary
