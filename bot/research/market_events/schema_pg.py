"""Phase G.0 — PostgreSQL DDL for market_events (schema v13)."""

from __future__ import annotations

import re
import time
from typing import Any

from bot.research.market_events.event_schema import (
    E1_DDL,
    E2_DDL,
    E21_ALTER_STATEMENTS,
    E2_ALTER_STATEMENTS,
    E31_ALTER_STATEMENTS,
    E31_DDL,
    E32_DDL,
    E33_DDL,
    E3_DDL,
    E4_DDL,
    E531_ALTER_STATEMENTS,
    E53_DDL,
    E5_DDL,
    F0_DDL,
    F1_DDL,
    F2_DDL,
    MIGRATIONS_TABLE,
    SCHEMA_VERSION,
)

MIGRATION_DESCRIPTIONS: dict[int, str] = {
    1: "Phase E.1 initial schema",
    2: "Phase E.2 multi-asset instrument registry",
    3: "Phase E.2.1 activation tiers and observation mode",
    4: "Phase E.3 lifecycle and shadow research tables",
    5: "Phase E.3.1 pending reversal watcher and shadow research",
    6: "Phase E.3.2 near-miss summaries and collector observability",
    7: "Phase E.3.3 Telegram alerts and AI analyst shadow",
    8: "Phase E.4 historical replay and context intelligence",
    9: "Phase E.5 alert engine dashboard and research ops",
    10: "Phase E.5.3 Telegram delivery log and ops",
    11: "Phase E.5.3.1 delivery log message_text for retries",
    12: "Phase F.0 signal intelligence research",
    13: "Phase F.1 Telegram signal intelligence reports",
    14: "Phase F.2 professional trading intelligence",
}


def _split_ddl(ddl: str) -> list[str]:
    parts: list[str] = []
    for chunk in ddl.split(";"):
        stmt = chunk.strip()
        if stmt:
            parts.append(stmt)
    return parts


def sqlite_ddl_to_pg(ddl: str) -> str:
    """Convert SQLite CREATE statements to PostgreSQL."""
    out = ddl
    out = out.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out)
    out = out.replace("INSERT OR IGNORE INTO market_events_scheduler_state",
                      "INSERT INTO market_events_scheduler_state")
    return out


def pg_alter_add_column(stmt: str) -> str:
    """SQLite ALTER ADD COLUMN → PostgreSQL IF NOT EXISTS."""
    m = re.match(
        r"ALTER TABLE (\w+) ADD COLUMN (\w+) (.+)",
        stmt.strip().rstrip(";"),
        re.IGNORECASE,
    )
    if not m:
        return stmt
    table, col, rest = m.group(1), m.group(2), m.group(3)
    return f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {rest}"


def full_pg_ddl() -> str:
    blocks = [
        E1_DDL,
        E2_DDL,
        E3_DDL,
        E31_DDL,
        E32_DDL,
        E33_DDL,
        E4_DDL,
        E5_DDL.replace(
            "INSERT INTO market_events_scheduler_state (id, updated_at) VALUES (1, 0);",
            "INSERT INTO market_events_scheduler_state (id, updated_at) VALUES (1, 0) "
            "ON CONFLICT (id) DO NOTHING;",
        ),
        E53_DDL,
        F0_DDL,
        F1_DDL,
        F2_DDL,
    ]
    ddl = sqlite_ddl_to_pg("\n".join(blocks))
    alters = [pg_alter_add_column(s) for s in (
        E2_ALTER_STATEMENTS
        + E21_ALTER_STATEMENTS
        + E31_ALTER_STATEMENTS
        + list(E531_ALTER_STATEMENTS)
    )]
    return ddl + "\n" + ";\n".join(alters) + ";"


def _executescript(conn: Any, ddl: str) -> None:
    for stmt in _split_ddl(ddl):
        conn.execute(stmt)


def _record_migration(conn: Any, version: int, description: str) -> None:
    now = int(time.time())
    conn.execute(
        f"""
        INSERT INTO {MIGRATIONS_TABLE} (version, applied_at, description)
        VALUES (?, ?, ?)
        ON CONFLICT (version) DO UPDATE SET
          applied_at = EXCLUDED.applied_at,
          description = EXCLUDED.description
        """,
        (version, str(now), description),
    )


def apply_pg_migrations(conn: Any) -> list[str]:
    """Apply full PostgreSQL schema up to SCHEMA_VERSION."""
    applied: list[str] = []
    row = conn.execute(
        f"SELECT MAX(version) AS v FROM {MIGRATIONS_TABLE}",
    ).fetchone()
    current = int(row["v"] or 0)

    if current < SCHEMA_VERSION:
        _executescript(conn, full_pg_ddl())
        for ver in range(current + 1, SCHEMA_VERSION + 1):
            desc = MIGRATION_DESCRIPTIONS.get(ver, f"v{ver}")
            _record_migration(conn, ver, desc)
            applied.append(f"v{ver}")

    conn.commit()
    return applied


ALL_TABLES: tuple[str, ...] = (
    "market_events_migrations",
    "market_events_universe_log",
    "market_events",
    "market_event_snapshots",
    "paper_strategy_runs",
    "market_event_context",
    "market_events_runner_state",
    "market_events_instruments",
    "market_events_price_observations",
    "market_events_entity_registry",
    "market_events_discovery_runs",
    "market_event_lifecycle_decisions",
    "market_events_shadow_candidates",
    "market_events_pending_shocks",
    "market_events_profile_shadow_candidates",
    "market_events_counterfactual_studies",
    "market_events_near_miss_summaries",
    "market_event_alert_log",
    "market_event_analysis_jobs",
    "market_event_ai_analyses",
    "market_events_historical_candles",
    "market_events_candle_backfill_checkpoints",
    "market_events_replay_runs",
    "market_events_replay_splits",
    "market_events_replay_shocks",
    "market_events_replay_path_metrics",
    "market_events_replay_strategy_results",
    "market_events_replay_context_links",
    "market_events_replay_ai_critic",
    "market_events_opportunity_scores",
    "market_events_ai_comparisons",
    "market_events_digest_log",
    "market_events_timeline_cache",
    "market_events_scheduler_state",
    "market_event_telegram_delivery_log",
    "market_events_multitimeframe",
    "market_events_exhaustion",
    "market_event_exchange_symbols",
    "market_event_exchange_context",
    "market_events_opportunity_scores_v2",
    "market_event_ai_analyses_f0",
    "market_events_signal_ranking_weekly",
    "market_events_mtf_paper_runs",
    "market_events_mtf_replay_results",
    "market_events_signal_reports_f1",
    "market_events_signal_outcomes_f1",
    "market_events_funding_oi_history_f2",
    "market_events_signal_reports_f2",
)
