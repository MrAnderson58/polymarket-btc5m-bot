"""Persist AI Agent features and decisions to SQLite."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

SOURCE_TABLE = "early_reversion_v2_trades"

_AI_COLUMNS = (
    "trade_id",
    "source_table",
    "market_slug",
    "strategy_name",
    "side",
    "entry_ts",
    "entry_price",
    "exit_price",
    "seconds_open",
    "spread",
    "ask",
    "bid",
    "distance_to_strike",
    "btc_move_5s",
    "btc_move_10s",
    "btc_move_15s",
    "btc_move_20s",
    "btc_move_30s",
    "btc_move_45s",
    "btc_move_60s",
    "btc_move_90s",
    "volatility_15s",
    "volatility_30s",
    "volatility_60s",
    "regime_label",
    "stop_loss_pct",
    "trailing_activation",
    "trailing_distance",
    "time_stop_sec",
    "entry_threshold",
    "position_size_usdc",
    "mfe",
    "mae",
    "holding_time",
    "features_json",
    "ai_score",
    "decision",
    "observe_mode",
    "outcome",
    "pnl",
    "pnl_usdc",
    "exit_reason",
)

_DECISION_COLUMNS = (
    "trade_id",
    "source_table",
    "score",
    "confidence",
    "decision",
    "similar_count",
    "historical_pf",
    "historical_wr",
    "avg_pnl",
    "counterfactual_result",
    "explanation_json",
)


def upsert_ai_feature(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [c for c in _AI_COLUMNS if c in row]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("trade_id", "source_table"))
    conn.execute(
        f"""
        INSERT INTO ai_features ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(trade_id, source_table) DO UPDATE SET {updates}
        """,
        tuple(row[c] for c in cols),
    )


def upsert_ai_decision(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [c for c in _DECISION_COLUMNS if c in row]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("trade_id", "source_table"))
    conn.execute(
        f"""
        INSERT INTO ai_decisions ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(trade_id, source_table) DO UPDATE SET {updates}
        """,
        tuple(row[c] for c in cols),
    )


def sync_ai_features(conn: sqlite3.Connection) -> int:
    """v2: full intelligence pipeline (observe-only)."""
    from bot.ai_agent.learning import process_all_trades

    result = process_all_trades(conn)
    return int(result.get("trades_processed", 0))


def load_ai_features(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM ai_features
        WHERE source_table = ?
        ORDER BY entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()


def load_ai_decisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.*, f.pnl, f.outcome, f.strategy_name, f.regime_label
        FROM ai_decisions d
        LEFT JOIN ai_features f
          ON f.trade_id = d.trade_id AND f.source_table = d.source_table
        WHERE d.source_table = ?
        ORDER BY d.trade_id ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()
