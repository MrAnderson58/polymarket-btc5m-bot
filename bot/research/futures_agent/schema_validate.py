"""Validate Stage 1 futures_agent schema."""

from __future__ import annotations

from typing import Any

from bot.research.futures_agent.db import connection_is_postgres

STAGE1_TABLES = {
    "futures_agent_migrations": frozenset({"version", "applied_at", "description"}),
    "futures_agent_inputs": frozenset({
        "id", "source", "telegram_message_id", "raw_message_id", "raw_text",
        "received_at", "input_type", "processing_status", "status_detail", "created_at",
    }),
    "futures_agent_signals": frozenset({
        "id", "input_id", "parser_version", "taxonomy", "symbol", "direction",
        "entry_low", "entry_high", "stop_loss", "leverage", "timeframe",
        "explicit_confidence", "parse_status", "passes_gate", "gate_reason",
        "parse_json", "created_at",
    }),
    "futures_agent_targets": frozenset({
        "id", "signal_id", "target_index", "target_price",
    }),
}

_FK_QUERY_POSTGRES = """
SELECT
    tc.table_name,
    kcu.column_name,
    ccu.table_name AS foreign_table
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_schema = kcu.constraint_schema
 AND tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_schema = tc.constraint_schema
 AND ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
  AND tc.table_name IN (
    'futures_agent_signals', 'futures_agent_targets',
    'futures_agent_market_snapshots', 'futures_agent_btc_context',
    'futures_agent_relative_strength'
  )
"""

STAGE2_TABLES = {
    "futures_agent_market_snapshots": frozenset({
        "id", "signal_id", "snapshot_ts", "symbol", "exchange",
        "spot_price", "futures_price",
        "return_1m", "return_5m", "return_15m", "return_30m", "return_1h", "return_4h", "return_24h",
        "ema_fast", "ema_slow", "ema_slope", "atr", "realized_vol",
        "distance_from_local_high", "distance_from_local_low", "volume_ratio",
        "funding_rate", "open_interest", "basis", "data_quality", "raw_metadata_json", "created_at",
    }),
    "futures_agent_btc_context": frozenset({
        "id", "signal_id", "snapshot_ts", "btc_price",
        "return_5m", "return_15m", "return_1h", "return_4h", "return_24h",
        "trend_15m", "trend_1h", "trend_4h",
        "volatility_regime", "momentum_regime", "market_regime", "created_at",
    }),
    "futures_agent_relative_strength": frozenset({
        "id", "signal_id", "symbol", "snapshot_ts",
        "alt_return_5m", "alt_return_15m", "alt_return_1h",
        "btc_return_5m", "btc_return_15m", "btc_return_1h",
        "excess_return_5m", "excess_return_15m", "excess_return_1h",
        "correlation_to_btc", "beta_to_btc", "relative_strength_label", "created_at",
    }),
}

STAGE2_FK_EXPECTED = {
    ("futures_agent_market_snapshots", "signal_id", "futures_agent_signals"),
    ("futures_agent_btc_context", "signal_id", "futures_agent_signals"),
    ("futures_agent_relative_strength", "signal_id", "futures_agent_signals"),
}

STAGE3_TABLES = {
    "futures_agent_trader_posts": frozenset({
        "id", "source_message_id", "channel_name", "message_ts", "raw_text",
        "content_hash", "content_type", "symbols_json", "deterministic_confidence", "created_at",
    }),
    "futures_agent_trader_theses": frozenset({
        "id", "post_id", "symbol", "direction", "thesis_text", "horizon",
        "condition_text", "invalidation_text", "confidence", "created_at",
    }),
    "futures_agent_trader_levels": frozenset({
        "id", "thesis_id", "level_type", "price", "ordinal", "confidence",
    }),
    "futures_agent_thesis_outcomes": frozenset({
        "id", "thesis_id", "evaluation_horizon", "price_at_thesis",
        "mfe_pct", "mae_pct", "return_pct", "direction_correct",
        "target_hit", "stop_hit", "evaluated_at",
    }),
    "futures_agent_source_scores": frozenset({
        "id", "channel_name", "content_type", "symbol_group", "horizon",
        "sample_size", "directional_accuracy", "avg_mfe", "avg_mae",
        "expectancy_proxy", "wilson_lower_bound", "recency_weighted_score",
        "calculated_as_of", "updated_at",
    }),
}

STAGE4_TABLES = {
    "futures_agent_thesis_outcomes": frozenset({
        "id", "thesis_id", "evaluation_horizon", "price_at_thesis",
        "mfe_pct", "mae_pct", "return_pct", "direction_correct",
        "target_hit", "stop_hit", "evaluated_at",
        "time_to_target_sec", "time_to_stop_sec", "final_outcome",
    }),
    "futures_agent_source_scores_v2": frozenset({
        "id", "channel_name", "content_type", "symbol", "direction", "timeframe", "horizon",
        "sample_size", "win_rate", "avg_return", "avg_mfe", "avg_mae",
        "profit_factor", "sharpe", "bayesian_mean", "wilson_lower_bound",
        "recency_weighted_score", "calculated_as_of", "updated_at",
    }),
}


def validate_stage1_schema(conn: Any) -> dict[str, Any]:
    postgres = connection_is_postgres(conn)
    errors: list[str] = []
    tables_ok: list[str] = []

    for table, required_cols in STAGE1_TABLES.items():
        cols = _table_columns(conn, table, postgres=postgres)
        if cols is None:
            errors.append(f"missing table: {table}")
            continue
        missing = required_cols - set(cols)
        if missing:
            errors.append(f"{table} missing columns: {sorted(missing)}")
        else:
            tables_ok.append(table)

    if postgres:
        _check_fk_postgres(conn, errors)

    return {
        "valid": len(errors) == 0,
        "tables_ok": tables_ok,
        "errors": errors,
    }


def _table_columns(conn: Any, table: str, *, postgres: bool) -> list[str] | None:
    if postgres:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table,),
        ).fetchall()
        if not rows:
            return None
        return [r["column_name"] for r in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return None
    return [r["name"] for r in rows]


def _check_fk_postgres(conn: Any, errors: list[str]) -> None:
    rows = conn.execute(_FK_QUERY_POSTGRES).fetchall()
    fk_pairs = {(r["table_name"], r["column_name"], r["foreign_table"]) for r in rows}
    expected = {
        ("futures_agent_signals", "input_id", "futures_agent_inputs"),
        ("futures_agent_targets", "signal_id", "futures_agent_signals"),
    }
    for exp in expected:
        if exp not in fk_pairs:
            errors.append(f"missing FK: {exp[0]}.{exp[1]} -> {exp[2]}")


def validate_stage2_schema(conn: Any) -> dict[str, Any]:
    postgres = connection_is_postgres(conn)
    errors: list[str] = []
    tables_ok: list[str] = []

    for table, required_cols in STAGE2_TABLES.items():
        cols = _table_columns(conn, table, postgres=postgres)
        if cols is None:
            errors.append(f"missing table: {table}")
            continue
        missing = required_cols - set(cols)
        if missing:
            errors.append(f"{table} missing columns: {sorted(missing)}")
        else:
            tables_ok.append(table)

    if postgres:
        rows = conn.execute(_FK_QUERY_POSTGRES).fetchall()
        fk_pairs = {(r["table_name"], r["column_name"], r["foreign_table"]) for r in rows}
    return {
        "valid": len(errors) == 0,
        "tables_ok": tables_ok,
        "errors": errors,
    }


def validate_stage3_schema(conn: Any) -> dict[str, Any]:
    postgres = connection_is_postgres(conn)
    errors: list[str] = []
    tables_ok: list[str] = []

    for table, required_cols in STAGE3_TABLES.items():
        cols = _table_columns(conn, table, postgres=postgres)
        if cols is None:
            errors.append(f"missing table: {table}")
            continue
        missing = required_cols - set(cols)
        if missing:
            errors.append(f"{table} missing columns: {sorted(missing)}")
        else:
            tables_ok.append(table)

    return {
        "valid": len(errors) == 0,
        "tables_ok": tables_ok,
        "errors": errors,
    }


def validate_stage4_schema(conn: Any) -> dict[str, Any]:
    postgres = connection_is_postgres(conn)
    errors: list[str] = []
    tables_ok: list[str] = []

    for table, required_cols in STAGE4_TABLES.items():
        cols = _table_columns(conn, table, postgres=postgres)
        if cols is None:
            errors.append(f"missing table: {table}")
            continue
        missing = required_cols - set(cols)
        if missing:
            errors.append(f"{table} missing columns: {sorted(missing)}")
        else:
            tables_ok.append(table)

    return {"valid": len(errors) == 0, "tables_ok": tables_ok, "errors": errors}
