"""Phase E.1 SQLite schema — additive, isolated from trades.db and futures_agent."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MIGRATIONS_TABLE = "market_events_migrations"
SCHEMA_VERSION = 59

E1_DDL = """
CREATE TABLE IF NOT EXISTS market_events_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_universe_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_tag TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    selection_reason TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts INTEGER NOT NULL,
    detected_ts INTEGER NOT NULL,
    venue TEXT NOT NULL DEFAULT 'binance_futures',
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'SHOCK',
    direction TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'SHOCK_DETECTED',
    trigger_window_seconds INTEGER,
    return_pct REAL,
    velocity REAL,
    acceleration REAL,
    volume_zscore REAL,
    market_return_pct REAL,
    btc_return_pct REAL,
    relative_return_pct REAL,
    classification TEXT NOT NULL DEFAULT 'UNKNOWN',
    confidence REAL,
    detector_version TEXT NOT NULL,
    detector_triggers_json TEXT NOT NULL DEFAULT '[]',
    dedup_key TEXT,
    raw_metrics_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_me_events_ts ON market_events(event_ts);
CREATE INDEX IF NOT EXISTS idx_me_events_symbol ON market_events(symbol);
CREATE INDEX IF NOT EXISTS idx_me_events_phase ON market_events(phase);

CREATE TABLE IF NOT EXISTS market_event_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    snapshot_ts INTEGER NOT NULL,
    offset_seconds INTEGER NOT NULL,
    price REAL,
    return_from_event REAL,
    volume REAL,
    bid REAL,
    ask REAL,
    spread_bps REAL,
    orderbook_imbalance REAL,
    open_interest REAL,
    funding REAL,
    context_json TEXT,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_snapshots_event ON market_event_snapshots(event_id);

CREATE TABLE IF NOT EXISTS paper_strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    reversal_variant TEXT NOT NULL,
    exit_variant TEXT NOT NULL,
    eligibility INTEGER NOT NULL DEFAULT 0,
    rejection_reason TEXT,
    signal_ts INTEGER,
    entry_ts INTEGER,
    entry_price REAL,
    initial_stop REAL,
    breakeven_trigger REAL,
    trailing_mode TEXT,
    take_profit_mode TEXT,
    exit_ts INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gross_return REAL,
    fee_bps REAL,
    slippage_bps REAL,
    net_return REAL,
    mfe REAL,
    mae REAL,
    be_exit INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, strategy_name, reversal_variant, exit_variant),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_runs_event ON paper_strategy_runs(event_id);
CREATE INDEX IF NOT EXISTS idx_me_runs_strategy ON paper_strategy_runs(strategy_name);

CREATE TABLE IF NOT EXISTS market_event_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    context_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    context_ts INTEGER NOT NULL,
    time_delta_seconds INTEGER NOT NULL,
    relevance_score REAL,
    context_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, context_type, source, source_record_id),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_context_event ON market_event_context(event_id);
CREATE INDEX IF NOT EXISTS idx_me_context_type ON market_event_context(context_type);

CREATE TABLE IF NOT EXISTS market_events_runner_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_poll_ts INTEGER,
    symbols_json TEXT,
    updated_at INTEGER NOT NULL
);
"""


def apply_migrations(conn: Any) -> list[str]:
    from bot.research.market_events.db import connection_is_postgres

    if connection_is_postgres(conn):
        from bot.research.market_events.schema_pg import apply_pg_migrations
        return apply_pg_migrations(conn)

    applied: list[str] = []
    conn.executescript(E1_DDL)
    row = conn.execute(
        f"SELECT MAX(version) AS v FROM {MIGRATIONS_TABLE}",
    ).fetchone()
    current = int(row["v"] or 0)

    if current < 1:
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (1, now, "Phase E.1 initial schema"),
        )
        applied.append("v1")

    if current < 2:
        conn.executescript(E2_DDL)
        for stmt in E2_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (2, now, "Phase E.2 multi-asset instrument registry"),
        )
        applied.append("v2")
        current = 2

    if current < 3:
        for stmt in E21_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (3, now, "Phase E.2.1 activation tiers and observation mode"),
        )
        applied.append("v3")
        current = 3

    if current < 4:
        conn.executescript(E3_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (4, now, "Phase E.3 lifecycle and shadow research tables"),
        )
        applied.append("v4")
        current = 4

    if current < 5:
        conn.executescript(E31_DDL)
        for stmt in E31_ALTER_STATEMENTS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (5, now, "Phase E.3.1 pending reversal watcher and shadow research"),
        )
        applied.append("v5")
        current = 5

    if current < 6:
        conn.executescript(E32_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (6, now, "Phase E.3.2 near-miss summaries and collector observability"),
        )
        applied.append("v6")
        current = 6

    if current < 7:
        conn.executescript(E33_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (7, now, "Phase E.3.3 Telegram alerts and AI analyst shadow"),
        )
        applied.append("v7")
        current = 7

    if current < 8:
        conn.executescript(E4_DDL)
        now = int(time.time())
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
            VALUES (?, datetime(?, 'unixepoch'), ?)
            """,
            (8, now, "Phase E.4 historical replay and context intelligence"),
        )
        applied.append("v8")
        current = 8

    if current < SCHEMA_VERSION:
        if current < 9:
            conn.executescript(E5_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (9, now, "Phase E.5 alert engine dashboard and research ops"),
            )
            applied.append("v9")
            current = 9

        if current < 10:
            conn.executescript(E53_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (10, now, "Phase E.5.3 Telegram delivery log and ops"),
            )
            applied.append("v10")
            current = 10

        if current < 11:
            for stmt in E531_ALTER_STATEMENTS:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (11, now, "Phase E.5.3.1 delivery log message_text for retries"),
            )
            applied.append("v11")
            current = 11

        if current < 12:
            conn.executescript(F0_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (12, now, "Phase F.0 signal intelligence research"),
            )
            applied.append("v12")
            current = 12

        if current < 13:
            conn.executescript(F1_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (13, now, "Phase F.1 Telegram signal intelligence reports"),
            )
            applied.append("v13")
            current = 13

        if current < 14:
            conn.executescript(F2_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (14, now, "Phase F.2 professional trading intelligence"),
            )
            applied.append("v14")
            current = 14

        if current < 15:
            conn.executescript(F3_TREND_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (15, now, "Phase F.3 trend shock intelligence"),
            )
            applied.append("v15")
            current = 15

        if current < 16:
            conn.executescript(F4_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (16, now, "Phase F.4 visual intelligence and trend shock v2"),
            )
            applied.append("v16")
            current = 16

        if current < 17:
            for stmt in F41_ALTER_STATEMENTS:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (17, now, "Phase F.4.1 Telegram dedupe and final alert stages"),
            )
            applied.append("v17")
            current = 17

        if current < 18:
            conn.executescript(F5_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (18, now, "Phase F.5 professional signal engine"),
            )
            applied.append("v18")
            current = 18

        if current < 19:
            conn.executescript(F51_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (19, now, "Phase F.5.1 signal pipeline trace"),
            )
            applied.append("v19")
            current = 19

        if current < 20:
            conn.executescript(F6_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (20, now, "Phase F.6 trader performance learning"),
            )
            applied.append("v20")
            current = 20

        if current < 21:
            conn.executescript(F7_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (21, now, "Phase F.7 market intelligence engine"),
            )
            applied.append("v21")
            current = 21

        if current < 22:
            conn.executescript(F72_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (22, now, "Phase F.7.2 signal outcome engine"),
            )
            applied.append("v22")
            current = 22

        if current < 23:
            conn.executescript(F73_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (23, now, "Phase F.7.3 near miss diagnostics"),
            )
            applied.append("v23")
            current = 23

        if current < 24:
            conn.executescript(G1_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (24, now, "Phase G.1 liquidity and trend engine"),
            )
            applied.append("v24")
            current = 24

        if current < 25:
            conn.executescript(G2_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (25, now, "Phase G.2 Claude research agent"),
            )
            applied.append("v25")
            current = 25

        if current < 26:
            conn.executescript(G2_V26_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (26, now, "Phase G.2 Claude research agent v2"),
            )
            applied.append("v26")
            current = 26

        if current < 27:
            conn.executescript(G2_V27_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (27, now, "Phase G.2 Claude ops and fail-safe"),
            )
            applied.append("v27")
            current = 27

        if current < 28:
            conn.executescript(G2_V28_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (28, now, "Phase G.2 prompt cache and context hash"),
            )
            applied.append("v28")
            current = 28

        if current < 29:
            conn.executescript(G3_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (29, now, "Phase G.3 live signal production engine"),
            )
            applied.append("v29")
            current = 29

        if current < 30:
            conn.executescript(G31_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (30, now, "Phase G.3.1 candidate pipeline"),
            )
            applied.append("v30")
            current = 30

        if current < 31:
            conn.executescript(G32_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (31, now, "Phase G.3.2 candidate replay and threshold optimizer"),
            )
            applied.append("v31")
            current = 31

        if current < 32:
            conn.executescript(G33_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (32, now, "Phase G.3.3 weighted trend coverage"),
            )
            applied.append("v32")
            current = 32

        if current < 33:
            conn.executescript(G34_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (33, now, "Phase G.3.4 score breakdown and calibration"),
            )
            applied.append("v33")
            current = 33

        if current < 34:
            conn.executescript(G35_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (34, now, "Phase G.3.5 Telegram intelligence"),
            )
            applied.append("v34")
            current = 34

        if current < 35:
            conn.executescript(G351_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (35, now, "Phase G.3.5.1 Telegram command trace"),
            )
            applied.append("v35")
            current = 35

        if current < 36:
            conn.executescript(G4_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (36, now, "Phase G.4 auto validation engine"),
            )
            applied.append("v36")
            current = 36

        if current < 37:
            conn.executescript(G50_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (37, now, "Phase G.5.0 Quant Research Analyst"),
            )
            applied.append("v37")
            current = 37

        if current < 38:
            for stmt in G501_ALTER_STATEMENTS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (38, now, "Phase G.5.0.1 Claude reliability and research quality"),
            )
            applied.append("v38")
            current = 38

        if current < 39:
            conn.executescript(G51_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (39, now, "Phase G.5.1 Research Data Lake"),
            )
            applied.append("v39")
            current = 39

        if current < 40:
            conn.executescript(G36_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (40, now, "Phase G.3.6 Telegram Vision and Market Memory"),
            )
            applied.append("v40")
            current = 40

        if current < 41:
            for stmt in G37_ALTER_STATEMENTS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (41, now, "Phase G.3.7 Signal Discovery Diagnostics"),
            )
            applied.append("v41")
            current = 41

        if current < 42:
            conn.executescript(G39_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (42, now, "Phase G.3.9 Experimental Signal Calibration"),
            )
            applied.append("v42")
            current = 42

        if current < 43:
            conn.executescript(G40_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (43, now, "Phase G.4.0 Shadow Signal Lane"),
            )
            applied.append("v43")
            current = 43

        if current < 44:
            conn.executescript(G401_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (44, now, "Phase G.4.0.1 Shadow Pipeline Diagnostics"),
            )
            applied.append("v44")
            current = 44

        if current < 45:
            conn.executescript(G04_INBOUND_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (45, now, "Phase G.0.4 Telegram Inbound Trace"),
            )
            applied.append("v45")
            current = 45

        if current < 46:
            conn.executescript(S11_VALIDATION_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (46, now, "Phase S1.1 Validation Signal Pipeline"),
            )
            applied.append("v46")
            current = 46

        if current < 47:
            conn.executescript(S20_DECISION_ENGINE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (47, now, "Phase S2.0 Trading Decision Engine MVP"),
            )
            applied.append("v47")
            current = 47

        if current < 48:
            conn.executescript(S23_SIGNAL_INBOX_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (48, now, "Phase S2.3 Telegram Signal Inbox"),
            )
            applied.append("v48")
            current = 48

        if current < 49:
            conn.executescript(N11_NEWS_FEED_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (49, now, "Phase N1.1 News Collector MVP"),
            )
            applied.append("v49")
            current = 49

        if current < 50:
            conn.executescript(S40_SIGNAL_LEARNING_OBSERVE_ONLY_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (50, now, "Phase S4.0 Signal Learning Pipeline (Observe Only)"),
            )
            applied.append("v50")
            current = 50

        if current < 51:
            conn.executescript(S42_PAPER_PERFORMANCE_OBSERVE_ONLY_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (51, now, "Phase S4.2 Paper Performance Tracker (Observe Only)"),
            )
            applied.append("v51")
            current = 51

        if current < 52:
            conn.executescript(S41_LEARNING_REVIEW_STATUS_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (52, now, "FIX-S4.2 Learning worker review status / throughput"),
            )
            applied.append("v52")
            current = 52

        if current < 53:
            conn.executescript(S43_LEARNING_DAILY_PERFORMANCE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (53, now, "Phase S4.3 Learning queue scheduling and daily performance"),
            )
            applied.append("v53")
            current = 53

        if current < 54:
            conn.executescript(S44A_LEARNING_PLACEHOLDER_REASON_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (54, now, "FIX-S4.4A Learning analytics validation (placeholder_reason)"),
            )
            applied.append("v54")
            current = 54

        if current < 55:
            conn.executescript(S50_RESEARCH_TERMINAL_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (55, now, "Phase S5.0 AI Research Terminal (artifacts + manual Claude)"),
            )
            applied.append("v55")
            current = 55

        if current < 56:
            # Fresh DBs: create S41 summary/brief tables + S42 intelligence tables together.
            conn.executescript(S42_NARRATIVE_ENGINE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (56, now, "Phase S4.1/S4.2 news summary + narrative engine base tables"),
            )
            applied.append("v56")
            current = 56

        if current < 57:
            # Repair path: DBs that already applied S41-only as v56 still need S42 tables.
            conn.executescript(S42_ASSET_INTELLIGENCE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (57, now, "Phase S4.2 AI Narrative Engine (asset intelligence + top assets)"),
            )
            applied.append("v57")
            current = 57

        if current < 58:
            # S43 Event Intelligence — market_intel_events (core market_events is trading shocks).
            conn.executescript(S43_EVENT_INTELLIGENCE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (58, now, "Phase S4.3 Event Intelligence Engine (clustered intel events)"),
            )
            applied.append("v58")
            current = 58

        if current < 59:
            conn.executescript(S44_MULTI_SOURCE_DDL)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {MIGRATIONS_TABLE} (version, applied_at, description)
                VALUES (?, datetime(?, 'unixepoch'), ?)
                """,
                (59, now, "Phase S4.4 Multi-Source Intelligence Platform"),
            )
            applied.append("v59")
            current = 59

    if not applied:
        conn.commit()
    else:
        conn.commit()
    return applied


E2_DDL = """
CREATE TABLE IF NOT EXISTS market_events_instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    venue_symbol TEXT NOT NULL,
    canonical_asset TEXT NOT NULL,
    reference_asset TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    quote_currency TEXT NOT NULL DEFAULT 'USDT',
    trading_hours_mode TEXT NOT NULL,
    price_source TEXT NOT NULL,
    reference_price_source TEXT NOT NULL,
    contract_type TEXT,
    funding_applicable INTEGER NOT NULL DEFAULT 0,
    leverage_available INTEGER NOT NULL DEFAULT 1,
    liquidity_tier TEXT NOT NULL DEFAULT 'WATCH',
    active INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(venue, venue_symbol)
);

CREATE INDEX IF NOT EXISTS idx_me_inst_asset ON market_events_instruments(canonical_asset);
CREATE INDEX IF NOT EXISTS idx_me_inst_class ON market_events_instruments(asset_class);
CREATE INDEX IF NOT EXISTS idx_me_inst_active ON market_events_instruments(active);

CREATE TABLE IF NOT EXISTS market_events_price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    obs_ts INTEGER NOT NULL,
    trade_price REAL,
    reference_price REAL,
    basis_bps REAL,
    spread_bps REAL,
    lag_seconds INTEGER,
    venue TEXT,
    raw_json TEXT,
    FOREIGN KEY (instrument_id) REFERENCES market_events_instruments(id)
);

CREATE INDEX IF NOT EXISTS idx_me_price_obs_inst ON market_events_price_observations(instrument_id);

CREATE TABLE IF NOT EXISTS market_events_entity_registry (
    entity_id TEXT PRIMARY KEY,
    canonical_asset TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    metadata_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    discovered_count INTEGER NOT NULL,
    run_ts INTEGER NOT NULL,
    provenance_json TEXT
);
"""

E2_ALTER_STATEMENTS = [
    "ALTER TABLE market_events ADD COLUMN instrument_id INTEGER",
    "ALTER TABLE market_events ADD COLUMN asset_class TEXT",
    "ALTER TABLE market_events ADD COLUMN session_regime TEXT",
    "ALTER TABLE market_events ADD COLUMN reference_return_pct REAL",
    "ALTER TABLE market_events ADD COLUMN basis_bps REAL",
    "ALTER TABLE market_events ADD COLUMN cross_classification TEXT",
    "ALTER TABLE market_event_snapshots ADD COLUMN reference_price REAL",
    "ALTER TABLE market_event_snapshots ADD COLUMN basis_bps REAL",
    "ALTER TABLE market_event_snapshots ADD COLUMN tracking_error_bps REAL",
]

E21_ALTER_STATEMENTS = [
    "ALTER TABLE market_events_instruments ADD COLUMN activation_tier TEXT DEFAULT 'INACTIVE'",
    "ALTER TABLE market_events_instruments ADD COLUMN observe_enabled INTEGER DEFAULT 0",
    "ALTER TABLE market_events_instruments ADD COLUMN paper_enabled INTEGER DEFAULT 0",
    "ALTER TABLE market_events_instruments ADD COLUMN reference_provider TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN session_regime TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN turnover_24h REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN volume_24h REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN quote_age_sec REAL",
    "ALTER TABLE market_events_price_observations ADD COLUMN reference_provider TEXT",
    "ALTER TABLE market_events_price_observations ADD COLUMN same_venue_reference INTEGER DEFAULT 1",
]

E3_DDL = """
CREATE TABLE IF NOT EXISTS market_event_lifecycle_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    decision_ts INTEGER NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    details_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_lifecycle_event ON market_event_lifecycle_decisions(event_id);
CREATE INDEX IF NOT EXISTS idx_me_lifecycle_stage ON market_event_lifecycle_decisions(stage);

CREATE TABLE IF NOT EXISTS market_events_shadow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    detector_id TEXT NOT NULL DEFAULT 'SHOCK_F',
    direction TEXT NOT NULL,
    return_pct REAL,
    z_score REAL,
    vol_scale_pct REAL,
    overlap_detectors_json TEXT,
    forward_returns_json TEXT,
    path_class TEXT,
    detector_version TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_shadow_sym ON market_events_shadow_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_me_shadow_ts ON market_events_shadow_candidates(event_ts);
"""

E31_DDL = """
CREATE TABLE IF NOT EXISTS market_events_pending_shocks (
    event_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'MONITORING_REVERSAL',
    detected_ts INTEGER NOT NULL,
    shock_return_pct REAL,
    shock_extreme_price REAL,
    shock_extreme_ts INTEGER,
    monitor_until_ts INTEGER NOT NULL,
    monitor_horizons_json TEXT,
    confirmed_reversal TEXT,
    confirm_ts INTEGER,
    confirm_latency_sec INTEGER,
    confirm_price REAL,
    path_from_extreme_json TEXT,
    r5_first_confirm_ts INTEGER,
    expired_ts INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_pending_phase ON market_events_pending_shocks(phase);
CREATE INDEX IF NOT EXISTS idx_me_pending_sym ON market_events_pending_shocks(symbol);

CREATE TABLE IF NOT EXISTS market_events_profile_shadow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    window_sec INTEGER NOT NULL,
    direction TEXT NOT NULL,
    return_pct REAL,
    threshold_pct REAL,
    min_abs_floor_pct REAL,
    session_regime TEXT,
    asset_class TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_prof_shadow_sym ON market_events_profile_shadow_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_me_prof_shadow_prof ON market_events_profile_shadow_candidates(profile_name);

CREATE TABLE IF NOT EXISTS market_events_counterfactual_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    episode_ts INTEGER NOT NULL,
    direction TEXT NOT NULL,
    shock_return_pct REAL,
    max_continuation_pct REAL,
    time_to_extreme_sec INTEGER,
    max_reversal_pct REAL,
    time_to_25_reclaim_sec INTEGER,
    time_to_50_reclaim_sec INTEGER,
    time_to_full_reclaim_sec INTEGER,
    forward_returns_json TEXT,
    mae_mfe_json TEXT,
    session_regime TEXT,
    has_context INTEGER DEFAULT 0,
    classification TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_cf_sym ON market_events_counterfactual_studies(symbol);
CREATE INDEX IF NOT EXISTS idx_me_cf_ts ON market_events_counterfactual_studies(episode_ts);
"""

E31_ALTER_STATEMENTS = [
    "ALTER TABLE paper_strategy_runs ADD COLUMN stop_history_json TEXT",
    "ALTER TABLE paper_strategy_runs ADD COLUMN be_helped INTEGER",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN profile_name TEXT",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN raw_tick_count INTEGER",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN episode_id TEXT",
    "ALTER TABLE market_events_shadow_candidates ADD COLUMN deduped INTEGER DEFAULT 0",
]

E32_DDL = """
CREATE TABLE IF NOT EXISTS market_events_near_miss_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    window_sec INTEGER NOT NULL,
    direction TEXT NOT NULL,
    session_regime TEXT,
    max_abs_return_pct REAL,
    current_abs_return_pct REAL,
    p95_abs_return_pct REAL,
    p99_abs_return_pct REAL,
    threshold_pct REAL NOT NULL,
    max_threshold_reached_pct REAL,
    max_volume_zscore REAL,
    max_relative_return_pct REAL,
    episodes_25pct INTEGER DEFAULT 0,
    episodes_50pct INTEGER DEFAULT 0,
    episodes_75pct INTEGER DEFAULT 0,
    episodes_90pct INTEGER DEFAULT 0,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_near_miss_sym ON market_events_near_miss_summaries(symbol);
CREATE INDEX IF NOT EXISTS idx_me_near_miss_prof ON market_events_near_miss_summaries(profile_name);
CREATE INDEX IF NOT EXISTS idx_me_near_miss_ts ON market_events_near_miss_summaries(created_at);
"""

E33_DDL = """
CREATE TABLE IF NOT EXISTS market_event_alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    message_text TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL,
    error TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_alert_event ON market_event_alert_log(event_id);
CREATE INDEX IF NOT EXISTS idx_me_alert_type ON market_event_alert_log(alert_type);

CREATE TABLE IF NOT EXISTS market_event_analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'e33_shadow_v1',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    updated_at INTEGER,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_ai_job_event ON market_event_analysis_jobs(event_id);
CREATE INDEX IF NOT EXISTS idx_me_ai_job_status ON market_event_analysis_jobs(status);

CREATE TABLE IF NOT EXISTS market_event_ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    job_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    structured_output_json TEXT NOT NULL,
    movement_interpretation TEXT,
    reversal_bias TEXT,
    confidence REAL,
    context_ids_json TEXT,
    latency_ms REAL,
    token_usage_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id),
    FOREIGN KEY (job_id) REFERENCES market_event_analysis_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_me_ai_analysis_event ON market_event_ai_analyses(event_id);
"""

E4_DDL = """
CREATE TABLE IF NOT EXISTS market_events_historical_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    UNIQUE(venue, symbol, timeframe, open_ts)
);

CREATE INDEX IF NOT EXISTS idx_me_hcandle_sym ON market_events_historical_candles(symbol, timeframe, open_ts);

CREATE TABLE IF NOT EXISTS market_events_candle_backfill_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    last_cursor_ts INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    gaps_found INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(venue, symbol, timeframe, start_ts, end_ts, source)
);

CREATE TABLE IF NOT EXISTS market_events_replay_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL UNIQUE,
    data_source TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    split_config_json TEXT,
    start_ts INTEGER,
    end_ts INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_replay_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL UNIQUE,
    train_end_ts INTEGER NOT NULL,
    validation_end_ts INTEGER NOT NULL,
    holdout_end_ts INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_replay_shocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    event_ts INTEGER NOT NULL,
    direction TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    impulse_pct REAL NOT NULL,
    impulse_duration_sec INTEGER,
    volume_zscore REAL,
    btc_context_pct REAL,
    market_context_pct REAL,
    session_regime TEXT,
    pre_volatility REAL,
    source_provenance TEXT NOT NULL,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES market_events_replay_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_me_replay_shock_run ON market_events_replay_shocks(run_id);
CREATE INDEX IF NOT EXISTS idx_me_replay_shock_sym ON market_events_replay_shocks(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_replay_path_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    horizon_sec INTEGER NOT NULL,
    continuation_pct REAL,
    reversal_pct REAL,
    reclaim_10_sec INTEGER,
    reclaim_25_sec INTEGER,
    reclaim_50_sec INTEGER,
    reclaim_75_sec INTEGER,
    reclaim_100_sec INTEGER,
    mfe_fade_pct REAL,
    mae_fade_pct REAL,
    path_class TEXT,
    raw_json TEXT,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id, horizon_sec)
);

CREATE TABLE IF NOT EXISTS market_events_replay_strategy_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    shock_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    split_bucket TEXT,
    entered INTEGER NOT NULL DEFAULT 0,
    entry_ts INTEGER,
    exit_ts INTEGER,
    gross_return_pct REAL,
    net_return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    exit_reason TEXT,
    fee_bps REAL,
    slippage_bps REAL,
    raw_json TEXT,
    FOREIGN KEY (run_id) REFERENCES market_events_replay_runs(id),
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(run_id, shock_id, strategy_id, fee_bps, slippage_bps)
);

CREATE TABLE IF NOT EXISTS market_events_replay_context_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    context_source TEXT NOT NULL,
    context_record_id TEXT NOT NULL,
    context_ts INTEGER NOT NULL,
    age_at_event_sec INTEGER NOT NULL,
    window_sec INTEGER NOT NULL,
    symbol_match INTEGER NOT NULL DEFAULT 0,
    direction_agreement TEXT,
    catalyst_category TEXT,
    raw_json TEXT,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id, context_source, context_record_id, window_sec)
);

CREATE TABLE IF NOT EXISTS market_events_replay_ai_critic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shock_id INTEGER NOT NULL,
    interpretation TEXT NOT NULL,
    continuation_prob REAL,
    reversal_prob REAL,
    confidence REAL,
    recommended_action TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (shock_id) REFERENCES market_events_replay_shocks(id),
    UNIQUE(shock_id)
);
"""

E5_DDL = """
CREATE TABLE IF NOT EXISTS market_events_opportunity_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    score REAL NOT NULL,
    components_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_ai_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    prediction_json TEXT NOT NULL,
    reality_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    compared_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_digest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    message_text TEXT,
    sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(digest_type, period_key)
);

CREATE TABLE IF NOT EXISTS market_events_timeline_cache (
    event_id INTEGER PRIMARY KEY,
    timeline_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat_telegram_ts INTEGER,
    last_daily_digest_ts INTEGER,
    last_weekly_digest_ts INTEGER,
    updated_at INTEGER NOT NULL
);

INSERT OR IGNORE INTO market_events_scheduler_state (id, updated_at) VALUES (1, 0);
"""

E53_DDL = """
CREATE TABLE IF NOT EXISTS market_event_telegram_delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 0,
    alert_type TEXT NOT NULL,
    chat_id TEXT,
    message_preview TEXT,
    status TEXT NOT NULL,
    latency_ms REAL,
    http_code INTEGER,
    telegram_message_id INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_created
  ON market_event_telegram_delivery_log(created_at);
CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_status
  ON market_event_telegram_delivery_log(status);
CREATE INDEX IF NOT EXISTS idx_me_tg_delivery_alert_type
  ON market_event_telegram_delivery_log(alert_type);
"""

E531_ALTER_STATEMENTS = (
    "ALTER TABLE market_event_telegram_delivery_log ADD COLUMN message_text TEXT",
)

F0_DDL = """
CREATE TABLE IF NOT EXISTS market_events_multitimeframe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    window_minutes INTEGER NOT NULL,
    return_pct REAL NOT NULL,
    atr_multiple REAL,
    volume_multiple REAL,
    direction TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_mtf_detector ON market_events_multitimeframe(detector_id);
CREATE INDEX IF NOT EXISTS idx_me_mtf_symbol_ts ON market_events_multitimeframe(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_exhaustion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'TREND_EXHAUSTION',
    exhaustion_score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    consecutive_candles INTEGER,
    cumulative_return_pct REAL,
    atr_expansion REAL,
    volume_expansion REAL,
    vwap_distance_pct REAL,
    ema20_distance_pct REAL,
    ema50_distance_pct REAL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_exhaustion_symbol ON market_events_exhaustion(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_event_exchange_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_symbol TEXT NOT NULL UNIQUE,
    resolved_venue TEXT,
    resolved_symbol TEXT,
    status TEXT NOT NULL,
    exchange_attempts_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_event_exchange_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    funding REAL,
    open_interest REAL,
    long_short_ratio REAL,
    volume_24h REAL,
    spread_bps REAL,
    vwap REAL,
    atr REAL,
    ema20_distance_pct REAL,
    ema50_distance_pct REAL,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, venue),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_opportunity_scores_v2 (
    event_id INTEGER PRIMARY KEY,
    score REAL NOT NULL,
    score_breakdown_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_event_ai_analyses_f0 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    job_id INTEGER,
    prompt_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    bias TEXT,
    confidence REAL,
    continuation_probability REAL,
    reversal_probability REAL,
    summary_ru TEXT,
    summary_en TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_signal_ranking_weekly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL UNIQUE,
    ranking_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_mtf_paper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mtf_signal_id INTEGER NOT NULL,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry_ts INTEGER,
    exit_ts INTEGER,
    pnl_pct REAL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (mtf_signal_id) REFERENCES market_events_multitimeframe(id)
);

CREATE TABLE IF NOT EXISTS market_events_mtf_replay_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_tag TEXT NOT NULL,
    detector_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_ts INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(run_tag, detector_id, symbol, signal_ts)
);
"""

F1_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_reports_f1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    confidence_score REAL NOT NULL,
    confidence_breakdown_json TEXT NOT NULL,
    reversal_probability REAL NOT NULL,
    explanation_json TEXT NOT NULL,
    entry_recommendation TEXT NOT NULL,
    expected_target_pct REAL NOT NULL,
    expected_stop_pct REAL NOT NULL,
    matching_events_json TEXT NOT NULL,
    historical_reversal_rate REAL NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_f1_event ON market_events_signal_reports_f1(event_id);

CREATE TABLE IF NOT EXISTS market_events_signal_outcomes_f1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    paper_run_id INTEGER,
    confidence_score REAL,
    entry_recommendation TEXT,
    expected_target_pct REAL,
    realized_pnl_pct REAL,
    holding_seconds INTEGER,
    ai_agreed INTEGER,
    historical_matched INTEGER,
    outcome_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_out_f1_event ON market_events_signal_outcomes_f1(event_id);
"""

F2_DDL = """
CREATE TABLE IF NOT EXISTS market_events_funding_oi_history_f2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    venue TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    funding REAL,
    open_interest REAL,
    funding_delta REAL,
    oi_delta REAL,
    funding_regime TEXT,
    oi_regime TEXT,
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id),
    UNIQUE(event_id, venue, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_me_foi_f2_event ON market_events_funding_oi_history_f2(event_id);

CREATE TABLE IF NOT EXISTS market_events_signal_reports_f2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    exchange_consensus TEXT NOT NULL,
    exchange_detail_json TEXT NOT NULL,
    funding_regime TEXT,
    oi_regime TEXT,
    rvol_20 REAL,
    rvol_100 REAL,
    vwap_deviation_pct REAL,
    volume_label TEXT,
    market_structure_json TEXT NOT NULL,
    market_structure_labels TEXT NOT NULL,
    atr_percentile REAL,
    atr_expansion REAL,
    atr_exhaustion INTEGER,
    correlation_snapshot_json TEXT NOT NULL,
    correlation_verdict TEXT NOT NULL,
    historical_count INTEGER NOT NULL,
    historical_reversal_count INTEGER NOT NULL,
    historical_reversal_rate REAL NOT NULL,
    historical_similarity_json TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    confidence_breakdown_json TEXT NOT NULL,
    reversal_probability REAL NOT NULL,
    entry_recommendation TEXT NOT NULL,
    expected_target_pct REAL NOT NULL,
    expected_stop_pct REAL NOT NULL,
    ai_summary_v2_ru TEXT NOT NULL,
    telegram_rendered TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_sig_f2_event ON market_events_signal_reports_f2(event_id);
"""

F3_TREND_DDL = """
CREATE TABLE IF NOT EXISTS market_events_trend_shock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    trend_class TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    cumulative_return_pct REAL NOT NULL,
    accumulated_move_pct REAL NOT NULL,
    consecutive_bars INTEGER NOT NULL,
    atr_multiple REAL NOT NULL,
    volume_multiple REAL NOT NULL,
    trend_score REAL NOT NULL,
    detectors_json TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trend_shock_event ON market_events_trend_shock(event_id);
CREATE INDEX IF NOT EXISTS idx_me_trend_shock_symbol ON market_events_trend_shock(symbol, event_ts);

CREATE TABLE IF NOT EXISTS market_events_entry_stages_f3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    stage_reason TEXT NOT NULL,
    wait_r2 INTEGER NOT NULL,
    wait_r3 INTEGER NOT NULL,
    scale_after_r2_pct INTEGER,
    scale_after_r3_pct INTEGER,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_entry_stages_f3_event ON market_events_entry_stages_f3(event_id);

CREATE TABLE IF NOT EXISTS market_events_alert_rankings_f3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    rank_score REAL NOT NULL,
    rank_position INTEGER NOT NULL,
    window_bucket INTEGER NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, window_bucket)
);

CREATE INDEX IF NOT EXISTS idx_me_alert_rank_f3_bucket ON market_events_alert_rankings_f3(window_bucket, rank_score DESC);
"""

F4_DDL = """
CREATE TABLE IF NOT EXISTS market_events_visual_intel_f4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    has_image INTEGER NOT NULL DEFAULT 0,
    extracted_json TEXT NOT NULL,
    chart_analysis_json TEXT NOT NULL,
    crosscheck_json TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_visual_f4_event ON market_events_visual_intel_f4(event_id);

CREATE TABLE IF NOT EXISTS market_events_trend_shock_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    stage TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    direction TEXT NOT NULL,
    cumulative_return_pct REAL NOT NULL,
    accumulated_move_pct REAL NOT NULL,
    consecutive_bars INTEGER NOT NULL,
    acceleration_ratio REAL NOT NULL,
    atr_multiple REAL NOT NULL,
    volume_multiple REAL NOT NULL,
    funding REAL,
    open_interest_delta REAL,
    liquidations_score REAL,
    trend_score REAL NOT NULL,
    continuation_probability REAL NOT NULL,
    detectors_json TEXT NOT NULL,
    source_event_id INTEGER,
    dedup_key TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trend_v2_event ON market_events_trend_shock_v2(event_id);
CREATE INDEX IF NOT EXISTS idx_me_trend_v2_symbol ON market_events_trend_shock_v2(symbol, event_ts);
"""

F41_ALTER_STATEMENTS = (
    "ALTER TABLE market_event_alert_log ADD COLUMN message_type TEXT",
    "ALTER TABLE market_event_alert_log ADD COLUMN telegram_message_stage TEXT DEFAULT 'NONE'",
    "ALTER TABLE market_event_alert_log ADD COLUMN duplicate_prevented INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_me_alert_event_msgtype ON market_event_alert_log(event_id, message_type)",
)

F5_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_reports_f5 (
    event_id INTEGER PRIMARY KEY,
    dynamic_confidence REAL NOT NULL,
    reversal_probability REAL NOT NULL,
    continuation_probability REAL NOT NULL,
    entry_quality TEXT NOT NULL,
    signal_cause TEXT NOT NULL,
    interest_factors_json TEXT NOT NULL,
    historical_examples_json TEXT NOT NULL,
    risk_reward REAL NOT NULL,
    tp_probabilities_json TEXT NOT NULL,
    tp1_pct REAL NOT NULL,
    tp2_pct REAL NOT NULL,
    tp3_pct REAL NOT NULL,
    stop_pct REAL NOT NULL,
    priority_score REAL NOT NULL,
    telegram_eligible INTEGER NOT NULL DEFAULT 0,
    telegram_skip_reason TEXT,
    ai_summary_ru TEXT NOT NULL,
    telegram_rendered TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_signal_f5_conf ON market_events_signal_reports_f5(dynamic_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_me_signal_f5_quality ON market_events_signal_reports_f5(entry_quality);

CREATE TABLE IF NOT EXISTS market_events_signal_priority_f5 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    dynamic_confidence REAL NOT NULL,
    priority_score REAL NOT NULL,
    rank_position INTEGER NOT NULL DEFAULT 0,
    window_bucket INTEGER NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    telegram_skipped_reason TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(event_id, window_bucket),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_priority_f5_window ON market_events_signal_priority_f5(window_bucket, priority_score DESC);
"""

F51_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_trace_f51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    message_id INTEGER,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    latency_ms INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_trace_f51_event ON market_events_signal_trace_f51(event_id, created_at);
CREATE INDEX IF NOT EXISTS idx_me_trace_f51_stage ON market_events_signal_trace_f51(stage);
"""

F6_DDL = """
CREATE TABLE IF NOT EXISTS market_events_trader_performance_f6 (
    channel_name TEXT PRIMARY KEY,
    signals_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    avg_rr REAL NOT NULL DEFAULT 0,
    avg_pnl REAL NOT NULL DEFAULT 0,
    max_drawdown REAL NOT NULL DEFAULT 0,
    avg_tp_time_sec REAL NOT NULL DEFAULT 0,
    author_score REAL NOT NULL DEFAULT 0,
    last_30_wins INTEGER NOT NULL DEFAULT 0,
    last_30_losses INTEGER NOT NULL DEFAULT 0,
    recent_outcomes_json TEXT NOT NULL DEFAULT '[]',
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_trader_f6_wr ON market_events_trader_performance_f6(win_rate DESC);
CREATE INDEX IF NOT EXISTS idx_me_trader_f6_score ON market_events_trader_performance_f6(author_score DESC);
"""

F7_DDL = """
CREATE TABLE IF NOT EXISTS market_events_market_intelligence_f7 (
    event_id INTEGER PRIMARY KEY,
    market_score REAL NOT NULL DEFAULT 0,
    component_scores_json TEXT NOT NULL DEFAULT '{}',
    final_confidence REAL NOT NULL DEFAULT 0,
    success_probability REAL NOT NULL DEFAULT 0,
    liquidation_regime TEXT,
    liquidation_continuation_prob REAL,
    liquidation_reversal_prob REAL,
    liquidation_intel_json TEXT NOT NULL DEFAULT '{}',
    dominance_regime TEXT,
    dominance_json TEXT NOT NULL DEFAULT '{}',
    whale_score REAL NOT NULL DEFAULT 0,
    whale_intel_json TEXT NOT NULL DEFAULT '{}',
    news_impact TEXT NOT NULL DEFAULT 'LOW',
    news_keywords_json TEXT NOT NULL DEFAULT '[]',
    long_trend_stage TEXT,
    long_trend_json TEXT NOT NULL DEFAULT '{}',
    image_intel_json TEXT NOT NULL DEFAULT '{}',
    interest_factors_json TEXT NOT NULL DEFAULT '[]',
    telegram_rendered TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_intel_f7_score ON market_events_market_intelligence_f7(market_score DESC);
CREATE INDEX IF NOT EXISTS idx_me_intel_f7_conf ON market_events_market_intelligence_f7(final_confidence DESC);
"""

F72_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_outcomes_f72 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    trade_side TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    entry REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    sl REAL NOT NULL,
    entry_time INTEGER NOT NULL,
    exit_price REAL,
    exit_time INTEGER,
    exit_reason TEXT,
    pnl_pct REAL,
    holding_seconds INTEGER,
    max_drawdown_pct REAL NOT NULL DEFAULT 0,
    max_profit_pct REAL NOT NULL DEFAULT 0,
    risk_reward REAL NOT NULL DEFAULT 0,
    position_remaining_pct REAL NOT NULL DEFAULT 100,
    signal_score REAL NOT NULL DEFAULT 0,
    market_score REAL NOT NULL DEFAULT 0,
    author_channel TEXT,
    pattern_json TEXT NOT NULL DEFAULT '{}',
    tp1_hit_time INTEGER,
    tp2_hit_time INTEGER,
    tp3_hit_time INTEGER,
    sl_hit_time INTEGER,
    last_price REAL,
    last_check_time INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_me_outcome_f72_status ON market_events_signal_outcomes_f72(status);
CREATE INDEX IF NOT EXISTS idx_me_outcome_f72_symbol ON market_events_signal_outcomes_f72(symbol, entry_time DESC);

CREATE TABLE IF NOT EXISTS market_events_pattern_stats_f72 (
    pattern_key TEXT PRIMARY KEY,
    signals_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    avg_pnl REAL NOT NULL DEFAULT 0,
    avg_rr REAL NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_f72_ops_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

F73_DDL = """
CREATE TABLE IF NOT EXISTS market_events_near_miss_f73 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    detector TEXT NOT NULL,
    return_pct REAL NOT NULL DEFAULT 0,
    trend_score REAL,
    market_score REAL,
    confidence REAL NOT NULL DEFAULT 0,
    rejection_reason TEXT NOT NULL,
    rejection_category TEXT NOT NULL,
    missing_conditions_json TEXT NOT NULL DEFAULT '{}',
    skip_code TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_near_miss_f73_ts ON market_events_near_miss_f73(event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_near_miss_f73_cat ON market_events_near_miss_f73(rejection_category);

CREATE TABLE IF NOT EXISTS market_events_f73_ops_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

G1_DDL = """
CREATE TABLE IF NOT EXISTS market_events_liquidity_trend_g1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    mtf_windows_json TEXT NOT NULL DEFAULT '{}',
    consecutive_pattern_json TEXT NOT NULL DEFAULT '{}',
    slow_trend_json TEXT,
    liquidity_accum_json TEXT,
    capitulation_json TEXT,
    continuation_probability REAL NOT NULL DEFAULT 0.5,
    reversal_probability REAL NOT NULL DEFAULT 0.5,
    adaptive_threshold_pct REAL,
    historical_reversal_rate REAL,
    plan_action TEXT NOT NULL DEFAULT 'Ждать R2',
    telegram_rendered TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_g1_symbol_ts ON market_events_liquidity_trend_g1(symbol, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_g1_signal_type ON market_events_liquidity_trend_g1(signal_type);

CREATE TABLE IF NOT EXISTS market_events_mtf_trend_g1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    cumulative_return_pct REAL NOT NULL,
    candle_count INTEGER NOT NULL,
    green_pct REAL NOT NULL,
    red_pct REAL NOT NULL,
    max_streak INTEGER NOT NULL,
    streak_direction TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_mtf_g1_event ON market_events_mtf_trend_g1(event_id);

CREATE TABLE IF NOT EXISTS market_events_reversal_learning_g1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    consecutive_red INTEGER,
    consecutive_green INTEGER,
    window_minutes INTEGER,
    candles_to_reversal INTEGER,
    reversal_pct REAL,
    signal_type TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_g1_pattern_stats (
    pattern_key TEXT PRIMARY KEY,
    samples INTEGER NOT NULL DEFAULT 0,
    avg_candles_to_reversal REAL NOT NULL DEFAULT 0,
    reversal_rate REAL NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
"""

G2_DDL = """
CREATE TABLE IF NOT EXISTS market_events_ai_research_g2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    g1_signal_type TEXT,
    g1_reversal_probability REAL,
    why_at_this_point TEXT NOT NULL,
    reversal_signs_json TEXT NOT NULL DEFAULT '[]',
    continuation_signs_json TEXT NOT NULL DEFAULT '[]',
    invalidation_json TEXT NOT NULL DEFAULT '[]',
    conclusion TEXT NOT NULL,
    telegram_block TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'deterministic',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL,
    response_json TEXT,
    latency_ms REAL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_g2_research_event ON market_events_ai_research_g2(event_id);
CREATE INDEX IF NOT EXISTS idx_g2_research_symbol ON market_events_ai_research_g2(symbol, created_at DESC);
"""

G2_V26_DDL = """
ALTER TABLE market_events_ai_research_g2 ADD COLUMN market_story TEXT;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN bullish_factors_json TEXT DEFAULT '[]';
ALTER TABLE market_events_ai_research_g2 ADD COLUMN bearish_factors_json TEXT DEFAULT '[]';
ALTER TABLE market_events_ai_research_g2 ADD COLUMN reversal_probability REAL;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN continuation_probability REAL;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN risks_json TEXT DEFAULT '[]';
ALTER TABLE market_events_ai_research_g2 ADD COLUMN invalidates_json TEXT DEFAULT '[]';
ALTER TABLE market_events_ai_research_g2 ADD COLUMN summary_ru TEXT;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN confidence REAL;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN market_score REAL;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN input_tokens INTEGER DEFAULT 0;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN output_tokens INTEGER DEFAULT 0;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN cost_usd REAL DEFAULT 0;

CREATE TABLE IF NOT EXISTS market_events_g2_visual_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    has_image INTEGER NOT NULL DEFAULT 0,
    platform TEXT,
    bos_choch TEXT,
    demand_supply_json TEXT NOT NULL DEFAULT '{}',
    liquidity_sweep TEXT,
    entry_zones_json TEXT NOT NULL DEFAULT '[]',
    model_agreement TEXT,
    agreement_score REAL,
    visual_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT 'deterministic',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE TABLE IF NOT EXISTS market_events_g2_learning_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    useful_signals_json TEXT NOT NULL DEFAULT '[]',
    false_signals_json TEXT NOT NULL DEFAULT '[]',
    experiments_json TEXT NOT NULL DEFAULT '[]',
    note_json TEXT NOT NULL DEFAULT '{}',
    summary_ru TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g2_learning_event ON market_events_g2_learning_notes(event_id);
"""

G2_V27_DDL = """
CREATE TABLE IF NOT EXISTS market_events_g2_ops_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

ALTER TABLE market_events_ai_research_g2 ADD COLUMN ai_status TEXT;
ALTER TABLE market_events_ai_research_g2 ADD COLUMN skip_error TEXT;
"""

G2_V28_DDL = """
ALTER TABLE market_events_ai_research_g2 ADD COLUMN context_hash TEXT;

CREATE TABLE IF NOT EXISTS market_events_g2_prompt_cache (
    context_hash TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'anthropic',
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g2_research_hash ON market_events_ai_research_g2(context_hash);
"""

G3_DDL = """
CREATE TABLE IF NOT EXISTS market_snapshots_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_uuid TEXT NOT NULL UNIQUE,
    snapshot_ts INTEGER NOT NULL,
    btc_price REAL,
    eth_price REAL,
    sol_price REAL,
    bnb_price REAL,
    total3 REAL,
    btc_dominance REAL,
    funding REAL,
    open_interest REAL,
    liquidations REAL,
    volume REAL,
    atr REAL,
    fear_greed REAL,
    dxy REAL,
    spx REAL,
    qqq REAL,
    vix REAL,
    gold REAL,
    oil REAL,
    usdt_dominance REAL,
    volume_delta REAL,
    exchange_ts INTEGER,
    collector_latency_ms REAL,
    recorder_status TEXT NOT NULL DEFAULT 'ok',
    raw_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g3_snapshots_ts ON market_snapshots_g3(snapshot_ts DESC);

CREATE TABLE IF NOT EXISTS market_trend_windows_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    window_minutes INTEGER NOT NULL,
    pattern_type TEXT NOT NULL,
    consecutive_candles INTEGER NOT NULL DEFAULT 0,
    trend_score REAL NOT NULL DEFAULT 0,
    direction TEXT NOT NULL DEFAULT 'NEUTRAL',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots_g3(id)
);

CREATE INDEX IF NOT EXISTS idx_g3_trend_snap ON market_trend_windows_g3(snapshot_id, symbol);

CREATE TABLE IF NOT EXISTS market_liquidity_state_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL UNIQUE,
    primary_state TEXT NOT NULL,
    accumulation_prob REAL NOT NULL DEFAULT 0,
    distribution_prob REAL NOT NULL DEFAULT 0,
    short_squeeze_prob REAL NOT NULL DEFAULT 0,
    long_squeeze_prob REAL NOT NULL DEFAULT 0,
    capitulation_prob REAL NOT NULL DEFAULT 0,
    exhaustion_prob REAL NOT NULL DEFAULT 0,
    recovery_prob REAL NOT NULL DEFAULT 0,
    continuation_prob REAL NOT NULL DEFAULT 0,
    factors_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots_g3(id)
);

CREATE TABLE IF NOT EXISTS market_live_signals_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    snapshot_id INTEGER,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    probability REAL NOT NULL,
    market_score REAL NOT NULL,
    liquidity_state TEXT NOT NULL,
    liquidity_probability REAL NOT NULL,
    risk_reward REAL NOT NULL,
    btc_context TEXT,
    trend_summary TEXT,
    reason_json TEXT NOT NULL DEFAULT '[]',
    trade_plan_json TEXT NOT NULL DEFAULT '{}',
    claude_summary TEXT,
    historical_json TEXT NOT NULL DEFAULT '[]',
    telegram_rendered TEXT NOT NULL DEFAULT '',
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    dashboard_only INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    position_size_pct REAL,
    model_version TEXT NOT NULL DEFAULT 'g3_v1',
    entry_price REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    sl REAL,
    pnl_pct REAL,
    holding_seconds INTEGER,
    max_drawdown_pct REAL,
    max_profit_pct REAL,
    created_at INTEGER NOT NULL,
    closed_at INTEGER,
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots_g3(id),
    FOREIGN KEY (event_id) REFERENCES market_events(id)
);

CREATE INDEX IF NOT EXISTS idx_g3_signals_ts ON market_live_signals_g3(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_g3_signals_event ON market_live_signals_g3(event_id);
CREATE INDEX IF NOT EXISTS idx_g3_signals_status ON market_live_signals_g3(status);

CREATE TABLE IF NOT EXISTS market_signal_followup_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    followup_type TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    message_text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES market_live_signals_g3(id)
);

CREATE TABLE IF NOT EXISTS market_daily_report_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL UNIQUE,
    report_json TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_history_g3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    funding_weight REAL NOT NULL,
    oi_weight REAL NOT NULL,
    atr_weight REAL NOT NULL,
    trend_weight REAL NOT NULL,
    btc_weight REAL NOT NULL,
    history_weight REAL NOT NULL,
    recommended_funding REAL,
    recommended_oi REAL,
    recommended_atr REAL,
    recommended_trend REAL,
    recommended_btc REAL,
    recommended_history REAL,
    notes_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES market_live_signals_g3(id)
);

CREATE TABLE IF NOT EXISTS market_events_g3_ops_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

G31_DDL = """
CREATE TABLE IF NOT EXISTS market_candidate_g31 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER,
    candidate_ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    trend_score REAL,
    market_score REAL,
    liquidity_score REAL,
    confidence REAL,
    rr REAL,
    btc_alignment TEXT,
    funding_score REAL,
    oi_score REAL,
    volume_score REAL,
    atr_score REAL,
    fear_greed REAL,
    candidate_state TEXT NOT NULL,
    rejection_reason TEXT,
    direction TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES market_snapshots_g3(id)
);

CREATE INDEX IF NOT EXISTS idx_g31_candidates_ts ON market_candidate_g31(candidate_ts DESC);
CREATE INDEX IF NOT EXISTS idx_g31_candidates_sym ON market_candidate_g31(symbol, candidate_ts DESC);
CREATE INDEX IF NOT EXISTS idx_g31_candidates_state ON market_candidate_g31(candidate_state, candidate_ts DESC);
"""

G32_DDL = """
CREATE TABLE IF NOT EXISTS market_candidate_outcomes_g32 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    direction TEXT,
    created_at INTEGER NOT NULL,
    price_entry REAL,
    price_15m REAL,
    price_30m REAL,
    price_1h REAL,
    price_2h REAL,
    price_4h REAL,
    price_24h REAL,
    max_profit_pct REAL DEFAULT 0,
    max_drawdown_pct REAL DEFAULT 0,
    would_hit_tp INTEGER DEFAULT 0,
    would_hit_sl INTEGER DEFAULT 0,
    best_rr REAL,
    replay_status TEXT NOT NULL DEFAULT 'OPEN',
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES market_candidate_g31(id)
);

CREATE INDEX IF NOT EXISTS idx_g32_outcomes_status ON market_candidate_outcomes_g32(replay_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_g32_outcomes_profit ON market_candidate_outcomes_g32(max_profit_pct DESC);
"""

G33_DDL = """
ALTER TABLE market_candidate_g31 ADD COLUMN trend_coverage_pct REAL;
ALTER TABLE market_candidate_g31 ADD COLUMN trend_windows_json TEXT;
"""

G34_DDL = """
CREATE TABLE IF NOT EXISTS market_score_breakdown_g34 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    score_type TEXT NOT NULL,
    factor TEXT NOT NULL,
    raw_value REAL,
    normalized_value REAL,
    weight REAL,
    contribution REAL NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES market_candidate_g31(id)
);

CREATE INDEX IF NOT EXISTS idx_g34_breakdown_candidate ON market_score_breakdown_g34(candidate_id, score_type);
CREATE INDEX IF NOT EXISTS idx_g34_breakdown_factor ON market_score_breakdown_g34(factor, created_at DESC);

CREATE TABLE IF NOT EXISTS market_score_conflicts_g34 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    conflict_type TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES market_candidate_g31(id)
);

CREATE INDEX IF NOT EXISTS idx_g34_conflicts_type ON market_score_conflicts_g34(conflict_type, created_at DESC);

CREATE TABLE IF NOT EXISTS market_calibration_daily_g34 (
    report_date TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""

G35_DDL = """
CREATE TABLE IF NOT EXISTS market_g35_candidate_state (
    symbol TEXT PRIMARY KEY,
    candidate_id INTEGER,
    candidate_ts INTEGER NOT NULL,
    candidate_state TEXT NOT NULL,
    confidence REAL,
    market_score REAL,
    liquidity_score REAL,
    funding_score REAL,
    oi_score REAL,
    volume_score REAL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES market_candidate_g31(id)
);

CREATE INDEX IF NOT EXISTS idx_g35_candidate_state_ts ON market_g35_candidate_state(candidate_ts DESC);

CREATE TABLE IF NOT EXISTS market_g35_daily_research (
    report_date TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""

G351_DDL = """
CREATE TABLE IF NOT EXISTS market_events_command_trace_g351 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    command TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    latency_ms INTEGER,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_cmd_trace_g351_msg ON market_events_command_trace_g351(message_id, created_at);
CREATE INDEX IF NOT EXISTS idx_me_cmd_trace_g351_stage ON market_events_command_trace_g351(stage);
"""

G4_DDL = """
CREATE TABLE IF NOT EXISTS market_validation_records_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT,
    is_win INTEGER NOT NULL DEFAULT 0,
    pnl_pct REAL,
    rr REAL,
    confidence REAL,
    market_score REAL,
    liquidity_score REAL,
    rejection_reason TEXT,
    blocking_filter TEXT,
    factors_json TEXT NOT NULL DEFAULT '{}',
    outcome_ts INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_g4_validation_symbol ON market_validation_records_g4(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_g4_validation_source ON market_validation_records_g4(source_type, outcome_ts DESC);

CREATE TABLE IF NOT EXISTS market_validation_factor_stats_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    factor TEXT NOT NULL,
    win_rate REAL,
    avg_rr REAL,
    profit_factor REAL,
    sample_size INTEGER,
    ci_low REAL,
    ci_high REAL,
    importance REAL,
    rank_order INTEGER,
    predictor_type TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(report_date, factor)
);

CREATE TABLE IF NOT EXISTS market_validation_symbol_stats_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup_label TEXT NOT NULL,
    win_rate REAL,
    avg_rr REAL,
    profit_factor REAL,
    sample_size INTEGER,
    best_detector TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(report_date, symbol, setup_label)
);

CREATE TABLE IF NOT EXISTS market_validation_optimizer_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    min_confidence REAL,
    min_market_score REAL,
    min_liquidity REAL,
    min_rr REAL,
    win_rate REAL,
    profit_factor REAL,
    sample_size INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(report_date, symbol)
);

CREATE TABLE IF NOT EXISTS market_validation_analysis_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    pnl_pct REAL,
    blocking_filter TEXT,
    misleading_factors_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g4_analysis_type ON market_validation_analysis_g4(report_date, analysis_type);

CREATE TABLE IF NOT EXISTS market_validation_daily_g4 (
    report_date TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_validation_recommendations_g4 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    symbol TEXT,
    recommendation TEXT NOT NULL,
    rationale TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g4_recommendations_date ON market_validation_recommendations_g4(report_date);
"""

G50_DDL = """
CREATE TABLE IF NOT EXISTS market_events_quant_reports_g50 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    dataset_hash TEXT NOT NULL,
    claude_model TEXT,
    tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    json TEXT NOT NULL,
    summary TEXT,
    sample_size INTEGER DEFAULT 0,
    raw_response TEXT,
    research_score REAL,
    missing_info_json TEXT,
    debug_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_g50_quant_created ON market_events_quant_reports_g50(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_g50_quant_hash ON market_events_quant_reports_g50(dataset_hash);
"""

G501_ALTER_STATEMENTS = (
    "ALTER TABLE market_events_quant_reports_g50 ADD COLUMN raw_response TEXT",
    "ALTER TABLE market_events_quant_reports_g50 ADD COLUMN research_score REAL",
    "ALTER TABLE market_events_quant_reports_g50 ADD COLUMN missing_info_json TEXT",
    "ALTER TABLE market_events_quant_reports_g50 ADD COLUMN debug_json TEXT",
)

G51_DDL = """
CREATE TABLE IF NOT EXISTS market_events_snapshot_history_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    anchor_ts INTEGER NOT NULL,
    window_key TEXT NOT NULL,
    price REAL,
    return_pct REAL,
    volume REAL,
    atr REAL,
    funding REAL,
    oi REAL,
    liquidations REAL,
    btc_price REAL,
    eth_price REAL,
    dominance REAL,
    fear_greed REAL,
    created_at INTEGER NOT NULL,
    UNIQUE(candidate_id, window_key)
);

CREATE INDEX IF NOT EXISTS idx_g51_snap_hist_cand ON market_events_snapshot_history_g51(candidate_id);

CREATE TABLE IF NOT EXISTS market_events_candle_patterns_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    anchor_ts INTEGER NOT NULL,
    window_key TEXT NOT NULL,
    pattern TEXT,
    green_pct REAL,
    red_pct REAL,
    avg_body REAL,
    avg_wick REAL,
    largest_candle REAL,
    created_at INTEGER NOT NULL,
    UNIQUE(candidate_id, window_key)
);

CREATE INDEX IF NOT EXISTS idx_g51_candle_cand ON market_events_candle_patterns_g51(candidate_id);

CREATE TABLE IF NOT EXISTS market_events_liquidity_history_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    points_json TEXT NOT NULL,
    evolution_text TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(candidate_id, metric)
);

CREATE INDEX IF NOT EXISTS idx_g51_liq_cand ON market_events_liquidity_history_g51(candidate_id);

CREATE TABLE IF NOT EXISTS market_events_replay_timeline_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT,
    window_key TEXT NOT NULL,
    price REAL,
    pnl_pct REAL,
    max_profit_so_far REAL,
    drawdown_so_far REAL,
    created_at INTEGER NOT NULL,
    UNIQUE(candidate_id, window_key)
);

CREATE INDEX IF NOT EXISTS idx_g51_replay_cand ON market_events_replay_timeline_g51(candidate_id);

CREATE TABLE IF NOT EXISTS market_research_dataset_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    anchor_ts INTEGER NOT NULL,
    direction TEXT,
    market_score REAL,
    confidence REAL,
    funding REAL,
    oi REAL,
    volume REAL,
    atr REAL,
    final_pnl REAL,
    snapshot_history_json TEXT,
    candle_patterns_json TEXT,
    liquidity_history_json TEXT,
    replay_timeline_json TEXT,
    market_evolution TEXT,
    funding_evolution TEXT,
    oi_evolution TEXT,
    replay_evolution TEXT,
    dataset_completeness REAL DEFAULT 0,
    row_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g51_dataset_symbol ON market_research_dataset_g51(symbol, anchor_ts DESC);
CREATE INDEX IF NOT EXISTS idx_g51_dataset_ts ON market_research_dataset_g51(anchor_ts DESC);

CREATE TABLE IF NOT EXISTS market_research_lake_builds_g51 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_ts INTEGER NOT NULL,
    candidates_processed INTEGER DEFAULT 0,
    rows_written INTEGER DEFAULT 0,
    completeness_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE VIEW IF NOT EXISTS research_dataset AS
SELECT * FROM market_research_dataset_g51;
"""

G36_DDL = """
CREATE TABLE IF NOT EXISTS market_market_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    price REAL,
    funding REAL,
    oi REAL,
    fear REAL,
    dominance REAL,
    volume REAL,
    atr REAL,
    btc_regime TEXT,
    trend REAL,
    liquidity REAL,
    market_score REAL,
    confidence REAL,
    claude_summary TEXT,
    candidate_state TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_memory_sym_ts ON market_market_memory(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_market_memory_ts ON market_market_memory(ts DESC);

CREATE TABLE IF NOT EXISTS market_watchlist_g36 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    chat_id INTEGER,
    added_at INTEGER NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS market_telegram_vision_g36 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    chat_id INTEGER,
    platform TEXT,
    image_path TEXT,
    ocr_text TEXT,
    analysis_json TEXT,
    claude_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g36_vision_ts ON market_telegram_vision_g36(created_at DESC);
"""

G37_ALTER_STATEMENTS = (
    "ALTER TABLE market_candidate_g31 ADD COLUMN pipeline_trace_json TEXT",
    "ALTER TABLE market_candidate_g31 ADD COLUMN score_source TEXT",
)

G39_DDL = """
CREATE TABLE IF NOT EXISTS market_experimental_signals_g39 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    snapshot_id INTEGER,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    market_score REAL NOT NULL,
    liquidity_probability REAL NOT NULL,
    risk_reward REAL NOT NULL,
    reason TEXT,
    production_rejection_json TEXT,
    telegram_rendered TEXT,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    entry REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    sl REAL,
    pnl_pct REAL,
    max_profit_pct REAL,
    max_drawdown_pct REAL,
    closed_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_g39_exp_ts ON market_experimental_signals_g39(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_g39_exp_symbol ON market_experimental_signals_g39(symbol);
CREATE INDEX IF NOT EXISTS idx_g39_exp_status ON market_experimental_signals_g39(status);

CREATE TABLE IF NOT EXISTS market_experimental_followup_g39 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    followup_type TEXT NOT NULL,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    message_text TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES market_experimental_signals_g39(id)
);

CREATE INDEX IF NOT EXISTS idx_g39_followup_signal ON market_experimental_followup_g39(signal_id);
"""

G40_DDL = """
CREATE TABLE IF NOT EXISTS market_shadow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    snapshot_id INTEGER,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    sl REAL,
    confidence REAL NOT NULL,
    market_score REAL NOT NULL,
    liquidity REAL NOT NULL,
    rr REAL NOT NULL,
    volume REAL,
    btc_regime TEXT,
    claude_summary TEXT,
    production_rejection_json TEXT,
    market_snapshot_json TEXT,
    telegram_rendered TEXT,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    grade TEXT,
    result_label TEXT,
    pnl_pct REAL,
    max_profit REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    holding_time_sec INTEGER DEFAULT 0,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    sl_hit INTEGER NOT NULL DEFAULT 0,
    claude_review_json TEXT,
    closed_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_signals_ts ON market_shadow_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_signals_status ON market_shadow_signals(status);
CREATE INDEX IF NOT EXISTS idx_shadow_signals_symbol ON market_shadow_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_shadow_signals_grade ON market_shadow_signals(grade);

CREATE TABLE IF NOT EXISTS market_shadow_horizons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    horizon_label TEXT NOT NULL,
    horizon_sec INTEGER NOT NULL,
    checked_at INTEGER NOT NULL,
    current_price REAL,
    pnl REAL,
    max_profit REAL,
    max_drawdown REAL,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    sl_hit INTEGER NOT NULL DEFAULT 0,
    holding_time_sec INTEGER,
    FOREIGN KEY (signal_id) REFERENCES market_shadow_signals(id),
    UNIQUE(signal_id, horizon_label)
);

CREATE INDEX IF NOT EXISTS idx_shadow_horizons_signal ON market_shadow_horizons(signal_id);

CREATE TABLE IF NOT EXISTS market_learning_dataset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lane TEXT NOT NULL DEFAULT 'shadow',
    signal_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT,
    grade TEXT,
    result_label TEXT,
    pnl_pct REAL,
    market_snapshot_json TEXT,
    claude_review_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_lane_ts ON market_learning_dataset(lane, created_at DESC);
"""

G401_DDL = """
CREATE TABLE IF NOT EXISTS market_shadow_pipeline_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_ts INTEGER NOT NULL,
    snapshot_id INTEGER,
    symbol TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    signal_id INTEGER,
    outcome TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_trace_ts ON market_shadow_pipeline_trace(candidate_ts DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_trace_symbol ON market_shadow_pipeline_trace(symbol);
"""

G04_INBOUND_DDL = """
CREATE TABLE IF NOT EXISTS market_events_inbound_trace_g04 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    chat_id INTEGER,
    preview TEXT,
    received TEXT,
    router TEXT,
    command TEXT,
    parser TEXT,
    taxonomy TEXT,
    signal_label TEXT,
    reply TEXT,
    error TEXT,
    stages_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_me_inbound_trace_g04_ts
  ON market_events_inbound_trace_g04(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_me_inbound_trace_g04_msg
  ON market_events_inbound_trace_g04(message_id, created_at DESC);
"""

S11_VALIDATION_DDL = """
CREATE TABLE IF NOT EXISTS market_validation_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uuid TEXT NOT NULL UNIQUE,
    signal_type TEXT NOT NULL DEFAULT 'VALIDATION_SIGNAL',
    snapshot_id INTEGER,
    event_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    sl REAL,
    confidence REAL NOT NULL,
    market_score REAL NOT NULL,
    liquidity REAL NOT NULL,
    rr REAL NOT NULL,
    volume REAL,
    funding_score REAL,
    btc_regime TEXT,
    production_rejection_json TEXT,
    market_snapshot_json TEXT,
    telegram_rendered TEXT,
    telegram_sent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    grade TEXT,
    result_label TEXT,
    pnl_pct REAL,
    max_profit REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    holding_time_sec INTEGER DEFAULT 0,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    sl_hit INTEGER NOT NULL DEFAULT 0,
    result_telegram_sent INTEGER NOT NULL DEFAULT 0,
    closed_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_signals_ts ON market_validation_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_validation_signals_status ON market_validation_signals(status);
CREATE INDEX IF NOT EXISTS idx_validation_signals_symbol ON market_validation_signals(symbol);

CREATE TABLE IF NOT EXISTS market_validation_horizons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    horizon_label TEXT NOT NULL,
    horizon_sec INTEGER NOT NULL,
    checked_at INTEGER NOT NULL,
    current_price REAL,
    pnl REAL,
    max_profit REAL,
    max_drawdown REAL,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    sl_hit INTEGER NOT NULL DEFAULT 0,
    holding_time_sec INTEGER,
    FOREIGN KEY (signal_id) REFERENCES market_validation_signals(id),
    UNIQUE(signal_id, horizon_label)
);

CREATE INDEX IF NOT EXISTS idx_validation_horizons_signal ON market_validation_horizons(signal_id);
"""

S20_DECISION_ENGINE_DDL = """
CREATE TABLE IF NOT EXISTS market_decision_runs_s20 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    probability INTEGER NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT,
    risks_json TEXT NOT NULL DEFAULT '[]',
    telegram_rendered TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_runs_s20_ts ON market_decision_runs_s20(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_runs_s20_symbol ON market_decision_runs_s20(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS market_decision_agent_outputs_s20 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES market_decision_runs_s20(id)
);

CREATE INDEX IF NOT EXISTS idx_decision_agent_s20_run ON market_decision_agent_outputs_s20(run_id);
CREATE INDEX IF NOT EXISTS idx_decision_agent_s20_name ON market_decision_agent_outputs_s20(agent_name);
"""

S23_SIGNAL_INBOX_DDL = """
CREATE TABLE IF NOT EXISTS market_signal_inbox_s23 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at INTEGER NOT NULL,
    telegram_user TEXT,
    chat_id INTEGER,
    raw_text TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    entry REAL,
    stop REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    parsed_ok INTEGER NOT NULL DEFAULT 0,
    parser_reason TEXT,
    decision_run_id INTEGER,
    decision_label TEXT,
    decision_probability INTEGER,
    decision_summary TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    source TEXT NOT NULL DEFAULT 'telegram'
);

CREATE INDEX IF NOT EXISTS idx_signal_inbox_s23_ts ON market_signal_inbox_s23(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_inbox_s23_symbol ON market_signal_inbox_s23(symbol, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_inbox_s23_parsed ON market_signal_inbox_s23(parsed_ok, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_inbox_s23_decision ON market_signal_inbox_s23(decision_label, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_inbox_s23_status ON market_signal_inbox_s23(status);
"""

S40_SIGNAL_LEARNING_OBSERVE_ONLY_DDL = """
CREATE TABLE IF NOT EXISTS market_events_signal_learning_s40_ops_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_signal_learning_s40_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT,
    entry REAL,
    stop REAL,
    tp1 REAL,
    tp2 REAL,
    timestamp INTEGER,
    snapshot_funding REAL,
    snapshot_open_interest REAL,
    snapshot_volume REAL,
    snapshot_atr REAL,
    snapshot_fear_greed REAL,
    snapshot_trend TEXT,
    snapshot_news_score REAL,
    snapshot_news_impact TEXT,
    snapshot_pattern_json TEXT,
    snapshot_decision_confidence REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(signal_type, signal_id)
);

CREATE INDEX IF NOT EXISTS idx_s40_signals_type_ts ON market_events_signal_learning_s40_signals(signal_type, timestamp DESC);

CREATE TABLE IF NOT EXISTS market_events_signal_learning_s40_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    horizon_key TEXT NOT NULL,
    checkpoint_ts INTEGER NOT NULL,
    max_profit_pct REAL,
    max_drawdown_pct REAL,
    reached_tp1 INTEGER NOT NULL DEFAULT 0,
    reached_tp2 INTEGER NOT NULL DEFAULT 0,
    stopped INTEGER NOT NULL DEFAULT 0,
    expired INTEGER NOT NULL DEFAULT 0,
    holding_time_seconds INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(signal_type, signal_id, horizon_key)
);

CREATE INDEX IF NOT EXISTS idx_s40_cp_signal_horizon ON market_events_signal_learning_s40_checkpoints(signal_type, signal_id, horizon_key);

CREATE TABLE IF NOT EXISTS market_events_signal_learning_s40_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_type TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    reviewed_at INTEGER NOT NULL,
    analysis_text TEXT NOT NULL,
    win_loss_be TEXT,
    pnl_pct REAL,
    rr_achieved REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(signal_type, signal_id)
);

CREATE INDEX IF NOT EXISTS idx_s40_reviews_type_sig ON market_events_signal_learning_s40_reviews(signal_type, signal_id);
"""

S41_LEARNING_REVIEW_STATUS_DDL = """
ALTER TABLE market_events_signal_learning_s40_reviews
    ADD COLUMN review_status TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE market_events_signal_learning_s40_reviews
    ADD COLUMN review_type TEXT NOT NULL DEFAULT 'claude';
"""

S43_LEARNING_DAILY_PERFORMANCE_DDL = """
CREATE TABLE IF NOT EXISTS market_events_learning_daily_s43 (
    day_key TEXT PRIMARY KEY,
    signals INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    pnl_usd REAL NOT NULL DEFAULT 0,
    best_symbol TEXT,
    worst_symbol TEXT,
    best_pattern TEXT,
    worst_pattern TEXT,
    avg_hold_hours REAL NOT NULL DEFAULT 0,
    avg_rr REAL NOT NULL DEFAULT 0,
    claude_review_text TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending_ai',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

S44A_LEARNING_PLACEHOLDER_REASON_DDL = """
ALTER TABLE market_events_signal_learning_s40_reviews
    ADD COLUMN placeholder_reason TEXT;
"""

S50_RESEARCH_TERMINAL_DDL = """
CREATE TABLE IF NOT EXISTS market_events_research_artifacts_s50 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_type TEXT NOT NULL,
    symbol TEXT,
    telegram_user TEXT,
    chat_id INTEGER,
    message_id INTEGER,
    caption TEXT,
    content_text TEXT,
    url TEXT,
    file_path TEXT,
    mime_type TEXT,
    source_domain TEXT,
    metadata_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_s50_artifacts_symbol ON market_events_research_artifacts_s50(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_s50_artifacts_type ON market_events_research_artifacts_s50(artifact_type, created_at DESC);

CREATE TABLE IF NOT EXISTS market_events_claude_requests_s50 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    symbol TEXT,
    prompt_chars INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms REAL,
    estimated_cost_usd REAL,
    artifacts_used_json TEXT,
    model TEXT,
    telegram_user TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_s50_claude_requests_day ON market_events_claude_requests_s50(created_at DESC);
"""

S42_PAPER_PERFORMANCE_OBSERVE_ONLY_DDL = """
CREATE TABLE IF NOT EXISTS market_events_paper_account_s42 (
    id INTEGER PRIMARY KEY,
    initial_capital REAL NOT NULL,
    current_equity REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events_paper_trades_s42 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    s40_signal_type TEXT NOT NULL,
    s40_signal_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL,
    tp1 REAL,
    tp2 REAL,
    created_at INTEGER NOT NULL,
    closed_at INTEGER,
    holding_seconds INTEGER,
    mfe_pct REAL NOT NULL DEFAULT 0,
    mae_pct REAL NOT NULL DEFAULT 0,
    pnl_pct REAL,
    pnl_usd REAL,
    result TEXT,
    exit_reason TEXT,
    exit_price REAL,
    rr_achieved REAL,
    status TEXT NOT NULL,
    decision_confidence REAL,
    pattern_json TEXT,
    news_category TEXT,
    capital_usd REAL NOT NULL,
    leverage INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(s40_signal_type, s40_signal_id)
);

CREATE INDEX IF NOT EXISTS idx_s42_trades_status ON market_events_paper_trades_s42(status, created_at);
CREATE INDEX IF NOT EXISTS idx_s42_trades_closed ON market_events_paper_trades_s42(closed_at DESC);

CREATE TABLE IF NOT EXISTS market_events_paper_reports_s42 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    body_text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(report_type, period_key)
);

CREATE TABLE IF NOT EXISTS market_events_paper_ops_state_s42 (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

N11_NEWS_FEED_DDL = """
CREATE TABLE IF NOT EXISTS market_news_feed_n11 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    published_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    symbols TEXT,
    category TEXT,
    raw_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_feed_n11_published ON market_news_feed_n11(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_feed_n11_source ON market_news_feed_n11(source, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_feed_n11_created ON market_news_feed_n11(created_at DESC);
"""

# S4.2 Narrative Engine — also ensures S4.1 summary/brief tables if missing.
S42_NARRATIVE_ENGINE_DDL = """
CREATE TABLE IF NOT EXISTS market_news_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    summary TEXT NOT NULL,
    bullish_score REAL NOT NULL DEFAULT 0,
    bearish_score REAL NOT NULL DEFAULT 0,
    neutral_score REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0,
    sources TEXT,
    headline_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_summary_period
    ON market_news_summary(period_end DESC, symbol);

CREATE TABLE IF NOT EXISTS market_daily_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    global_narrative TEXT NOT NULL,
    top_bullish_json TEXT,
    top_bearish_json TEXT,
    macro_events_json TEXT,
    fed_json TEXT,
    etf_json TEXT,
    whales_json TEXT,
    polymarket_json TEXT,
    risk_level TEXT NOT NULL,
    risk_score REAL NOT NULL DEFAULT 0,
    headline_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_briefs_created
    ON market_daily_briefs(created_at DESC);

CREATE TABLE IF NOT EXISTS market_asset_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    news_count INTEGER NOT NULL DEFAULT 0,
    bullish_score REAL NOT NULL DEFAULT 0,
    bearish_score REAL NOT NULL DEFAULT 0,
    neutral_score REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0,
    top_headlines_json TEXT,
    summary TEXT NOT NULL,
    narrative TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    macro_score REAL NOT NULL DEFAULT 0,
    whale_score REAL NOT NULL DEFAULT 0,
    polymarket_score REAL NOT NULL DEFAULT 0,
    market_score REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_asset_intel_symbol_ts
    ON market_asset_intelligence(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_intel_created
    ON market_asset_intelligence(created_at DESC);

CREATE TABLE IF NOT EXISTS market_top_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    top_bullish_json TEXT,
    top_bearish_json TEXT,
    most_discussed_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_top_assets_ts
    ON market_top_assets(timestamp DESC);
"""

# S4.2 tables only — used when v56 already recorded S41 summaries without these.
S42_ASSET_INTELLIGENCE_DDL = """
CREATE TABLE IF NOT EXISTS market_asset_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    news_count INTEGER NOT NULL DEFAULT 0,
    bullish_score REAL NOT NULL DEFAULT 0,
    bearish_score REAL NOT NULL DEFAULT 0,
    neutral_score REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0,
    top_headlines_json TEXT,
    summary TEXT NOT NULL,
    narrative TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    macro_score REAL NOT NULL DEFAULT 0,
    whale_score REAL NOT NULL DEFAULT 0,
    polymarket_score REAL NOT NULL DEFAULT 0,
    market_score REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_asset_intel_symbol_ts
    ON market_asset_intelligence(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_intel_created
    ON market_asset_intelligence(created_at DESC);

CREATE TABLE IF NOT EXISTS market_top_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    top_bullish_json TEXT,
    top_bearish_json TEXT,
    most_discussed_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_top_assets_ts
    ON market_top_assets(timestamp DESC);
"""

# S4.3 Event Intelligence — clustered news events.
# Core `market_events` is reserved for trading shocks; intel uses market_intel_events.
S43_EVENT_INTELLIGENCE_DDL = """
CREATE TABLE IF NOT EXISTS market_intel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    narrative TEXT NOT NULL,
    symbols_json TEXT,
    sentiment REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    headline_count INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    sources_json TEXT,
    freshness REAL NOT NULL DEFAULT 0,
    article_ids_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_intel_events_updated
    ON market_intel_events(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_intel_events_last_seen
    ON market_intel_events(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_intel_events_confidence
    ON market_intel_events(confidence DESC, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_intel_events_importance
    ON market_intel_events(importance DESC, last_seen DESC);
"""

# S4.4 Multi-Source Intelligence Platform
S44_MULTI_SOURCE_DDL = """
CREATE TABLE IF NOT EXISTS market_polymarket_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    name TEXT NOT NULL,
    query TEXT,
    question TEXT,
    probability REAL,
    condition_id TEXT,
    tags_json TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_poly_signals_created
    ON market_polymarket_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_poly_signals_name
    ON market_polymarket_signals(name, created_at DESC);

CREATE TABLE IF NOT EXISTS market_macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    value REAL,
    unit TEXT,
    tags_json TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_macro_events_created
    ON market_macro_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_macro_events_name
    ON market_macro_events(name, created_at DESC);

CREATE TABLE IF NOT EXISTS market_source_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    last_update INTEGER NOT NULL,
    error TEXT,
    latency_ms REAL,
    items INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    UNIQUE(source_type, source_name)
);

CREATE INDEX IF NOT EXISTS idx_source_health_type
    ON market_source_health(source_type, source_name);
"""
