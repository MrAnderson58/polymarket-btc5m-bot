"""Trading Brain report section for unified analytics report."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bot.trading_brain.constants import BRAIN_VERSION, SOURCE_TABLE
from bot.trading_brain.knowledge import load_knowledge
from bot.trading_brain.memory import get_learning_state, load_memory


def build_brain_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summarize brain tables for report §43 (observe-only)."""
    last_run = get_learning_state(conn, "last_run")
    memory_sync = get_learning_state(conn, "memory_sync")
    memory_counts = memory_sync.get("counts") or {}

    contexts_row = conn.execute(
        "SELECT COUNT(*) AS n FROM brain_trade_context WHERE source_table = ?",
        (SOURCE_TABLE,),
    ).fetchone()
    contexts_stored = int(contexts_row["n"]) if contexts_row else 0

    knowledge_rows = load_knowledge(conn, limit=10)
    top_knowledge = [
        {
            "feature": row["feature"],
            "condition": row["condition_text"],
            "effect": row["effect_value"],
            "direction": row["causal_direction"],
            "confidence": row["confidence"],
            "sample_n": row["sample_n"],
        }
        for row in knowledge_rows
    ]

    memory_by_category: dict[str, int] = {}
    for row in load_memory(conn):
        memory_by_category[row["category"]] = memory_by_category.get(row["category"], 0) + 1

    sample_ctx = conn.execute(
        """
        SELECT trade_id, explainability_json, similar_stats_json
        FROM brain_trade_context
        WHERE source_table = ?
        ORDER BY built_at DESC
        LIMIT 1
        """,
        (SOURCE_TABLE,),
    ).fetchone()

    latest_hints: dict[str, Any] = {}
    if sample_ctx:
        latest_hints = json.loads(sample_ctx["explainability_json"] or "{}")

    return {
        "version": BRAIN_VERSION,
        "mode": "observe_only",
        "disclaimer": (
            "Trading Brain reads SQLite and writes analytics tables only. "
            "No impact on paper/live execution."
        ),
        "last_run": last_run,
        "memory_sync_counts": memory_counts,
        "memory_entries": memory_by_category,
        "contexts_stored": contexts_stored,
        "knowledge_links": len(knowledge_rows),
        "top_knowledge": top_knowledge,
        "latest_explainability_sample": {
            "trade_id": sample_ctx["trade_id"] if sample_ctx else None,
            "supportive": latest_hints.get("supportive_reasons", [])[:3],
            "opposing": latest_hints.get("opposing_reasons", [])[:3],
        },
    }
