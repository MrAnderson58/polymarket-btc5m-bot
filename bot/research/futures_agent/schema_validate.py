"""Validate Stage 1 futures_agent schema."""

from __future__ import annotations

from typing import Any

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
  AND tc.table_name IN ('futures_agent_signals', 'futures_agent_targets')
"""


def validate_stage1_schema(conn: Any, *, postgres: bool) -> dict[str, Any]:
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
