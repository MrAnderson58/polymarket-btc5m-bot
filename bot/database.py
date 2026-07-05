import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR, DATABASE_PATH
from bot.er_trailing_stop import trailing_enabled_at_entry


def _schema_path() -> Path:
    return BASE_DIR / "schema.sql"


_MIGRATIONS = (
    "ALTER TABLE virtual_trades ADD COLUMN market_end_price REAL",
    "ALTER TABLE virtual_trades ADD COLUMN distance_at_close REAL",
    "ALTER TABLE virtual_trades ADD COLUMN min_delta_after_entry REAL",
    "ALTER TABLE virtual_trades ADD COLUMN max_delta_after_entry REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN last_bid REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN exit_price REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN pnl_percent REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN pnl_usdc REAL",
    "ALTER TABLE early_reversion_trades ADD COLUMN holding_time_seconds REAL",
)

_ER_TRAILING_COLUMNS = (
    ("trailing_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("trailing_active", "INTEGER NOT NULL DEFAULT 0"),
    ("trailing_stop_price", "REAL"),
    ("trailing_activation_price", "REAL"),
    ("highest_price", "REAL"),
    ("max_profit_pct", "REAL"),
    ("realized_profit_pct", "REAL"),
    ("profit_left_on_table_pct", "REAL"),
)

_ER_TRADE_TABLES = (
    "early_reversion_v2_trades",
    "early_reversion_v25_trades",
    "early_reversion_v3_trades",
)

_ER_REJECTION_COLUMNS = (
    ("window_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("price_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("window_and_price_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("already_open_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("risk_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("entry_attempt", "INTEGER NOT NULL DEFAULT 0"),
    ("entry_success", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_by_window", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_by_price", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_by_already_open", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_by_risk", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_max_open_positions", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_daily_loss", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_duplicate_entry", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_existing_position", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_live_mode", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_other", "INTEGER NOT NULL DEFAULT 0"),
    ("blocked_unknown", "INTEGER NOT NULL DEFAULT 0"),
)

_ER_EXIT_REASON_CHECK_OLD = (
    "exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP')"
)
_ER_EXIT_REASON_CHECK_NEW = (
    "exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP', 'RECOVERY_NO_POSITION')"
)


def _migrate_er_exit_reason_recovery(conn: sqlite3.Connection) -> None:
    """Allow RECOVERY_NO_POSITION when reconciling stuck exits after failed live sell."""
    for table in _ER_TRADE_TABLES:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None:
            continue
        create_sql = row["sql"]
        if _ER_EXIT_REASON_CHECK_OLD not in create_sql:
            continue
        new_sql = create_sql.replace(_ER_EXIT_REASON_CHECK_OLD, _ER_EXIT_REASON_CHECK_NEW)
        temp_table = f"{table}_exit_reason_migration"
        conn.execute(f"ALTER TABLE {table} RENAME TO {temp_table}")
        conn.execute(new_sql)
        conn.execute(f"INSERT INTO {table} SELECT * FROM {temp_table}")
        conn.execute(f"DROP TABLE {temp_table}")
        for index_row in conn.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type='index' AND tbl_name=? AND sql IS NOT NULL
            """,
            (table,),
        ).fetchall():
            conn.execute(index_row["sql"])


def _apply_migrations(conn: sqlite3.Connection) -> None:
    virtual_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(virtual_trades)").fetchall()
    }
    early_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(early_reversion_trades)").fetchall()
    }
    for sql in _MIGRATIONS:
        parts = sql.split()
        column = parts[parts.index("COLUMN") + 1]
        table = parts[2]
        existing = virtual_cols if table == "virtual_trades" else early_cols
        if column not in existing:
            conn.execute(sql)
            existing.add(column)

    for table in _ER_TRADE_TABLES:
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in _ER_TRAILING_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                existing.add(column)

    rejection_existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(er_strategy_counters)").fetchall()
    }
    for column, definition in _ER_REJECTION_COLUMNS:
        if column not in rejection_existing:
            conn.execute(
                f"ALTER TABLE er_strategy_counters ADD COLUMN {column} {definition}"
            )
            rejection_existing.add(column)

    _migrate_er_exit_reason_recovery(conn)
    _ensure_order_fill_audit_table(conn)
    _ensure_er_health_events_table(conn)
    _ensure_no_c_filter_live_counters_table(conn)
    _ensure_trade_features_table(conn)
    _ensure_ai_features_table(conn)
    _ensure_ai_decisions_table(conn)
    _ensure_trading_brain_tables(conn)
    _ensure_scientist_tables(conn)
    _ensure_portfolio_tables(conn)
    _ensure_evolution_shadow_tables(conn)
    _ensure_bidirectional_shadow_tables(conn)
    _ensure_bidirectional_shadow_v12_tables(conn)
    _ensure_mtf_snapshot_tables(conn)
    _ensure_futures_research_tables(conn)
    _ensure_perf_indexes(conn)


def _ensure_portfolio_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            market_slug TEXT,
            phase TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_live_journal_trade
        ON live_journal (trade_id, phase)
        """
    )


def _ensure_evolution_shadow_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_shadow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parameter TEXT NOT NULL,
            current_value REAL NOT NULL,
            shadow_value REAL NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'CANCELLED')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            sample_size INTEGER NOT NULL DEFAULT 0,
            target_sample_size INTEGER NOT NULL DEFAULT 200,
            shadow_pf REAL,
            live_pf REAL,
            shadow_wr REAL,
            live_wr REAL,
            shadow_dd REAL,
            live_dd REAL,
            verdict TEXT CHECK (verdict IN ('PROMOTE', 'REJECT', 'CANCELLED') OR verdict IS NULL)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_shadow_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_id INTEGER NOT NULL,
            trade_id INTEGER NOT NULL,
            market_slug TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            shadow_decision TEXT NOT NULL CHECK (shadow_decision IN ('WOULD_ENTER', 'WOULD_SKIP')),
            live_pnl REAL NOT NULL,
            shadow_pnl REAL NOT NULL,
            evaluated_at TEXT NOT NULL,
            UNIQUE (shadow_id, trade_id),
            FOREIGN KEY (shadow_id) REFERENCES evolution_shadow(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_evolution_shadow_status
        ON evolution_shadow (status, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_evolution_shadow_eval_shadow
        ON evolution_shadow_evaluations (shadow_id, entry_ts ASC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_regime_shadow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filter_name TEXT NOT NULL,
            regimes_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            sample_size INTEGER NOT NULL DEFAULT 0,
            target_sample_size INTEGER NOT NULL DEFAULT 200,
            skipped INTEGER NOT NULL DEFAULT 0,
            saved_losses INTEGER NOT NULL DEFAULT 0,
            missed_winners INTEGER NOT NULL DEFAULT 0,
            saved_loss_pnl REAL NOT NULL DEFAULT 0.0,
            missed_profit_pnl REAL NOT NULL DEFAULT 0.0,
            net_pf_improvement_pct REAL,
            verdict TEXT CHECK (verdict IN ('PROMOTE_FILTER', 'REJECT_FILTER') OR verdict IS NULL)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_regime_shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            regime_shadow_id INTEGER NOT NULL,
            trade_id INTEGER NOT NULL,
            regime_label TEXT NOT NULL,
            in_filter INTEGER NOT NULL,
            pnl_percent REAL NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('saved_loss', 'missed_profit', 'normal')),
            evaluated_at TEXT NOT NULL,
            UNIQUE (regime_shadow_id, trade_id),
            FOREIGN KEY (regime_shadow_id) REFERENCES evolution_regime_shadow(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_regime_shadow_status
        ON evolution_regime_shadow (status, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL,
            experiment_type TEXT NOT NULL,
            parameter TEXT NOT NULL,
            from_value TEXT,
            to_value TEXT,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('RUNNING', 'PROMOTED', 'REJECTED')),
            shadow_id INTEGER,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            metrics_json TEXT
        )
        """
    )
    _migrate_evolution_shadow_metadata(conn)


def _migrate_evolution_shadow_metadata(conn: sqlite3.Connection) -> None:
    """Add creator/observability columns to evolution shadow tables."""
    for table, col, col_type in (
        ("evolution_shadow", "created_by", "TEXT"),
        ("evolution_shadow", "creator_decision", "TEXT"),
        ("evolution_shadow", "creator_confidence", "REAL"),
        ("evolution_shadow", "creator_reason", "TEXT"),
        ("evolution_regime_shadow", "created_by", "TEXT"),
        ("evolution_regime_shadow", "creator_decision", "TEXT"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evolution_observe_stats (
            hook TEXT PRIMARY KEY,
            last_run_at TEXT,
            last_success_at TEXT,
            last_error_at TEXT,
            last_error TEXT,
            error_count INTEGER NOT NULL DEFAULT 0,
            total_parameter_evals INTEGER NOT NULL DEFAULT 0,
            total_regime_evals INTEGER NOT NULL DEFAULT 0,
            last_parameter_eval_at TEXT,
            last_regime_eval_at TEXT
        )
        """
    )


def _ensure_bidirectional_shadow_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bidirectional_shadow_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            decision TEXT NOT NULL,
            confidence REAL,
            probability_yes REAL,
            probability_no REAL,
            regime TEXT,
            reason TEXT,
            btc_move_30s REAL,
            entry_price REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bidirectional_shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_regime TEXT,
            entry_confidence REAL,
            entry_reason TEXT,
            max_price_seen REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            holding_time_seconds REAL,
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_obs_market
            ON bidirectional_shadow_observations(market_slug, timestamp);
        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_trades_status
            ON bidirectional_shadow_trades(status);
        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_trades_market_status
            ON bidirectional_shadow_trades(market_slug, status);
        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_trades_entry_ts
            ON bidirectional_shadow_trades(entry_ts);
        CREATE INDEX IF NOT EXISTS idx_bidi_shadow_trades_side
            ON bidirectional_shadow_trades(side);
    """)
    _ensure_bidirectional_shadow_unique_market(conn)


def _ensure_bidirectional_shadow_unique_market(conn: sqlite3.Connection) -> None:
    has_dupes = conn.execute(
        """
        SELECT 1 FROM bidirectional_shadow_trades
        GROUP BY market_slug HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if has_dupes is None:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_bidi_shadow_trades_market_unique
            ON bidirectional_shadow_trades(market_slug)
            """
        )


def _ensure_bidirectional_shadow_v12_tables(conn: sqlite3.Connection) -> None:
    from bot.strategy.bidirectional_shadow_v12 import ensure_tables
    ensure_tables(conn)


def _ensure_futures_research_tables(conn: sqlite3.Connection) -> None:
    from bot.research.futures.schema import ensure_tables
    ensure_tables(conn)


def _ensure_mtf_snapshot_tables(conn: sqlite3.Connection) -> None:
    from bot.research.mtf.metadata import ensure_metadata_table
    from bot.research.mtf.snapshots import ensure_tables
    ensure_tables(conn)
    ensure_metadata_table(conn)


def _ensure_perf_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_er_v2_entry_ts
        ON early_reversion_v2_trades (entry_ts)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_er_v2_entry_price
        ON early_reversion_v2_trades (entry_price)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_er_v2_status_entry_ts
        ON early_reversion_v2_trades (status, entry_ts)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_checks_slug_checked_at
        ON market_checks (market_slug, checked_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_checks_ts
        ON market_checks (checked_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_features_trade
        ON trade_features (trade_id, source_table)
        """
    )


def _ensure_scientist_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scientist_hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            source TEXT NOT NULL,
            sample_n INTEGER NOT NULL,
            confidence REAL NOT NULL,
            expected_improvement REAL,
            expected_pf REAL,
            expected_wr REAL,
            hypothesis_type TEXT NOT NULL,
            params_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scientist_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'WAITING',
            priority TEXT NOT NULL DEFAULT 'LOW',
            confidence REAL NOT NULL,
            expected_pf REAL,
            expected_wr REAL,
            risk_level TEXT,
            validation_json TEXT,
            ranking_score REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (hypothesis_id) REFERENCES scientist_hypotheses(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scientist_experiments_status
        ON scientist_experiments (status, ranking_score DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scientist_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,
            description TEXT NOT NULL,
            feature TEXT,
            effect_json TEXT NOT NULL,
            sample_n INTEGER NOT NULL,
            confidence REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (pattern_type, description)
        )
        """
    )


def _ensure_trading_brain_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            ref_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (category, ref_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature TEXT NOT NULL,
            condition_text TEXT NOT NULL,
            outcome_metric TEXT NOT NULL,
            effect_value REAL NOT NULL,
            causal_direction TEXT NOT NULL,
            confidence REAL NOT NULL,
            sample_n INTEGER NOT NULL,
            built_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_brain_knowledge_feature
        ON brain_knowledge (feature, confidence DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_trade_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'early_reversion_v2_trades',
            decision_context_json TEXT NOT NULL,
            explainability_json TEXT NOT NULL,
            similar_stats_json TEXT,
            knowledge_refs_json TEXT,
            built_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (trade_id, source_table)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS brain_learning_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _ensure_ai_features_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'early_reversion_v2_trades',
            market_slug TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            seconds_open REAL,
            spread REAL,
            ask REAL,
            bid REAL,
            distance_to_strike REAL,
            btc_move_5s REAL,
            btc_move_10s REAL,
            btc_move_15s REAL,
            btc_move_20s REAL,
            btc_move_30s REAL,
            btc_move_45s REAL,
            btc_move_60s REAL,
            btc_move_90s REAL,
            volatility_15s REAL,
            volatility_30s REAL,
            volatility_60s REAL,
            regime_label TEXT,
            stop_loss_pct REAL,
            trailing_activation REAL,
            trailing_distance REAL,
            time_stop_sec REAL,
            entry_threshold REAL,
            position_size_usdc REAL,
            mfe REAL,
            mae REAL,
            holding_time REAL,
            features_json TEXT,
            ai_score REAL NOT NULL,
            decision TEXT NOT NULL,
            observe_mode INTEGER NOT NULL DEFAULT 1,
            outcome TEXT,
            pnl REAL,
            pnl_usdc REAL,
            exit_reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (trade_id, source_table)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_features_decision
        ON ai_features (decision, entry_ts)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_features_score
        ON ai_features (ai_score)
        """
    )


def _ensure_ai_decisions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'early_reversion_v2_trades',
            score REAL NOT NULL,
            confidence REAL NOT NULL,
            decision TEXT NOT NULL,
            similar_count INTEGER NOT NULL DEFAULT 0,
            historical_pf REAL,
            historical_wr REAL,
            avg_pnl REAL,
            counterfactual_result TEXT,
            explanation_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (trade_id, source_table)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_decisions_decision
        ON ai_decisions (decision, created_at)
        """
    )


def _ensure_trade_features_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            source_table TEXT NOT NULL DEFAULT 'early_reversion_v2_trades',
            market_slug TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            pnl REAL,
            pnl_usdc REAL,
            btc_move_5s REAL,
            btc_move_10s REAL,
            btc_move_15s REAL,
            btc_move_20s REAL,
            btc_move_30s REAL,
            btc_move_45s REAL,
            btc_move_60s REAL,
            btc_move_90s REAL,
            seconds_open REAL,
            spread REAL,
            ask REAL,
            bid REAL,
            distance_to_strike REAL,
            volatility_15s REAL,
            volatility_30s REAL,
            volatility_60s REAL,
            stop_loss_pct REAL,
            trailing_activation REAL,
            trailing_distance REAL,
            holding_time REAL,
            mfe REAL,
            mae REAL,
            is_win INTEGER NOT NULL DEFAULT 0,
            is_loss INTEGER NOT NULL DEFAULT 0,
            is_stop INTEGER NOT NULL DEFAULT 0,
            is_time_stop INTEGER NOT NULL DEFAULT 0,
            is_trailing INTEGER NOT NULL DEFAULT 0,
            exit_reason TEXT,
            built_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (trade_id, source_table)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_features_strategy
        ON trade_features (strategy_name, entry_ts)
        """
    )
    try:
        conn.execute("ALTER TABLE trade_features ADD COLUMN regime_label TEXT")
    except sqlite3.OperationalError:
        pass


def _ensure_no_c_filter_live_counters_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS no_c_filter_live_counters (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            normal_entries INTEGER NOT NULL DEFAULT 0,
            strict_entries INTEGER NOT NULL DEFAULT 0,
            skipped_strict_price INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _ensure_er_health_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS er_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            event_ts INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_er_health_events_type_ts
            ON er_health_events (event_type, event_ts)
        """
    )


def _ensure_order_fill_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_fill_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            clob_order_id TEXT,
            clob_side TEXT NOT NULL,
            token_id TEXT NOT NULL,
            submitted_price REAL NOT NULL,
            submitted_shares REAL NOT NULL,
            submitted_notional REAL NOT NULL,
            fill_price REAL,
            fill_shares REAL,
            fill_notional REAL,
            average_fill_price REAL,
            fees REAL,
            price_difference REAL,
            slippage REAL,
            fill_status TEXT NOT NULL DEFAULT 'pending',
            last_logged_fill_shares REAL NOT NULL DEFAULT 0,
            raw_order_json TEXT,
            raw_trades_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_order_fill_audit_clob_order_id
            ON order_fill_audit (clob_order_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_order_fill_audit_fill_status
            ON order_fill_audit (fill_status)
        """
    )


def backfill_early_reversion_trades(conn: sqlite3.Connection) -> int:
    """Recalculate settlement fields for closed trades missing exit data."""
    from bot.config import EARLY_REVERSION_POSITION_SIZE_USDC

    rows = conn.execute(
        """
        SELECT id, entry_price, target_price, entry_ts, end_ts,
               reached_target, last_bid, time_to_target_seconds,
               exit_price, pnl_percent, pnl_usdc, holding_time_seconds
        FROM early_reversion_trades
        WHERE status = 'closed'
          AND (
            exit_price IS NULL
            OR pnl_percent IS NULL
            OR pnl_usdc IS NULL
            OR holding_time_seconds IS NULL
          )
        ORDER BY id ASC
        """
    ).fetchall()

    updated = 0
    for row in rows:
        entry_price = float(row["entry_price"])
        reached_target = bool(row["reached_target"])

        if reached_target:
            exit_price = float(row["target_price"])
            holding_time = row["time_to_target_seconds"]
            if holding_time is None:
                holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))
        elif row["last_bid"] is not None:
            exit_price = float(row["last_bid"])
            holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))
        else:
            exit_price = entry_price
            holding_time = float(max(row["end_ts"] - row["entry_ts"], 0))

        pnl_percent = (exit_price - entry_price) / entry_price * 100
        shares = EARLY_REVERSION_POSITION_SIZE_USDC / entry_price
        pnl_usdc = shares * (exit_price - entry_price)

        conn.execute(
            """
            UPDATE early_reversion_trades
            SET exit_price = ?,
                pnl_percent = ?,
                pnl_usdc = ?,
                holding_time_seconds = ?
            WHERE id = ?
            """,
            (exit_price, pnl_percent, pnl_usdc, holding_time, row["id"]),
        )
        updated += 1

    return updated


def init_db(db_path: Path | None = None) -> None:
    path = db_path or DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = _schema_path().read_text(encoding="utf-8")
    with connect(path) as conn:
        conn.executescript(schema)
        _apply_migrations(conn)
        conn.commit()


@contextmanager
def connect(db_path: Path | None = None):
    path = db_path or DATABASE_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_market_check(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    seconds_remaining: float,
    strike_price: float,
    btc_price: float,
    yes_bid: float | None,
    yes_ask: float | None,
    no_bid: float | None,
    no_ask: float | None,
    signal: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_checks (
            market_slug, seconds_remaining, strike_price, btc_price,
            yes_bid, yes_ask, no_bid, no_ask, signal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            seconds_remaining,
            strike_price,
            btc_price,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            signal,
        ),
    )


def has_open_trade_for_market(conn: sqlite3.Connection, market_slug: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM virtual_trades WHERE market_slug = ? LIMIT 1",
        (market_slug,),
    ).fetchone()
    return row is not None


def insert_virtual_trade(
    conn: sqlite3.Connection,
    trade: dict[str, Any],
) -> int:
    entry_delta = trade["btc_delta_at_entry"]
    side = trade["side"]
    min_delta = entry_delta if side == "YES" else None
    max_delta = entry_delta if side == "NO" else None

    cursor = conn.execute(
        """
        INSERT INTO virtual_trades (
            market_slug, condition_id, window_start_ts, end_ts, strike_price,
            side, token_id, entry_ask, entry_bid, btc_price_at_entry,
            btc_delta_at_entry, min_delta_after_entry, max_delta_after_entry,
            seconds_remaining, size_usdc, shares
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade["market_slug"],
            trade.get("condition_id"),
            trade["window_start_ts"],
            trade["end_ts"],
            trade["strike_price"],
            side,
            trade["token_id"],
            trade["entry_ask"],
            trade.get("entry_bid"),
            trade["btc_price_at_entry"],
            entry_delta,
            min_delta,
            max_delta,
            trade["seconds_remaining"],
            trade["size_usdc"],
            trade["shares"],
        ),
    )
    return int(cursor.lastrowid)


def get_open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM virtual_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC
        """
    ).fetchall()


def get_open_trades_due_for_settlement(
    conn: sqlite3.Connection,
    now_ts: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM virtual_trades
        WHERE status = 'open' AND end_ts <= ?
        ORDER BY end_ts ASC
        """,
        (now_ts,),
    ).fetchall()


def settle_virtual_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    outcome: str,
    market_end_price: float,
    strike_price: float,
    payout_usdc: float,
    pnl_usdc: float,
) -> None:
    distance_at_close = abs(market_end_price - strike_price)
    conn.execute(
        """
        UPDATE virtual_trades
        SET status = 'settled',
            outcome = ?,
            settlement_btc_price = ?,
            market_end_price = ?,
            distance_at_close = ?,
            payout_usdc = ?,
            pnl_usdc = ?,
            settled_at = datetime('now')
        WHERE id = ?
        """,
        (
            outcome,
            market_end_price,
            market_end_price,
            distance_at_close,
            payout_usdc,
            pnl_usdc,
            trade_id,
        ),
    )


def upsert_early_reversion_market_prices(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    min_yes_price: float | None,
    max_yes_price: float | None,
    min_no_price: float | None,
    max_no_price: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO early_reversion_markets (
            market_slug, window_start_ts,
            min_yes_price, max_yes_price, min_no_price, max_no_price
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_slug) DO UPDATE SET
            min_yes_price = excluded.min_yes_price,
            max_yes_price = excluded.max_yes_price,
            min_no_price = excluded.min_no_price,
            max_no_price = excluded.max_no_price,
            updated_at = datetime('now')
        """,
        (
            market_slug,
            window_start_ts,
            min_yes_price,
            max_yes_price,
            min_no_price,
            max_no_price,
        ),
    )


def get_early_reversion_market(
    conn: sqlite3.Connection,
    market_slug: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM early_reversion_markets WHERE market_slug = ?",
        (market_slug,),
    ).fetchone()


def has_early_reversion_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    target_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, target_price, entry_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            target_price,
            entry_ts,
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def close_early_reversion_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    reached_target: bool,
    max_profit_percent: float,
    max_drawdown_percent: float,
    time_to_target_seconds: float | None,
    exit_price: float,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_trades
        SET status = 'closed',
            reached_target = ?,
            max_profit_percent = ?,
            max_drawdown_percent = ?,
            time_to_target_seconds = ?,
            exit_price = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            int(reached_target),
            max_profit_percent,
            max_drawdown_percent,
            time_to_target_seconds,
            exit_price,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trade_id,
        ),
    )


def has_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_v2_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_v2_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, entry_ts, max_price_seen, trailing_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            entry_ts,
            entry_price,
            int(trailing_enabled_at_entry()),
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_v2_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_v2_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_v2_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def update_early_reversion_v2_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
    trailing_active: bool = False,
    trailing_stop_price: float | None = None,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v2_trades
        SET max_price_seen = ?,
            last_bid = ?,
            trailing_active = ?,
            trailing_stop_price = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = ?,
            max_profit_pct = COALESCE(?, max_profit_pct)
        WHERE id = ?
        """,
        (
            max_price_seen,
            last_bid,
            int(trailing_active),
            trailing_stop_price,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            trade_id,
        ),
    )


def close_early_reversion_v2_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
    realized_profit_pct: float | None = None,
    profit_left_on_table_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v2_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = COALESCE(?, highest_price),
            max_profit_pct = COALESCE(?, max_profit_pct),
            realized_profit_pct = COALESCE(?, realized_profit_pct),
            profit_left_on_table_pct = COALESCE(?, profit_left_on_table_pct),
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            realized_profit_pct,
            profit_left_on_table_pct,
            trade_id,
        ),
    )


def has_yes_c_shadow_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str = "YES_C",
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM yes_c_shadow_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_yes_c_shadow_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO yes_c_shadow_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, entry_ts, max_price_seen, trailing_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            entry_ts,
            entry_price,
            int(trailing_enabled_at_entry()),
        ),
    )
    return int(cursor.lastrowid)


def get_open_yes_c_shadow_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM yes_c_shadow_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM yes_c_shadow_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def update_yes_c_shadow_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
    trailing_active: bool = False,
    trailing_stop_price: float | None = None,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE yes_c_shadow_trades
        SET max_price_seen = ?,
            last_bid = ?,
            trailing_active = ?,
            trailing_stop_price = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = ?,
            max_profit_pct = COALESCE(?, max_profit_pct)
        WHERE id = ?
        """,
        (
            max_price_seen,
            last_bid,
            int(trailing_active),
            trailing_stop_price,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            trade_id,
        ),
    )


def close_yes_c_shadow_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
    realized_profit_pct: float | None = None,
    profit_left_on_table_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE yes_c_shadow_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = COALESCE(?, highest_price),
            max_profit_pct = COALESCE(?, max_profit_pct),
            realized_profit_pct = COALESCE(?, realized_profit_pct),
            profit_left_on_table_pct = COALESCE(?, profit_left_on_table_pct),
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            realized_profit_pct,
            profit_left_on_table_pct,
            trade_id,
        ),
    )


def has_early_reversion_v3_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_v3_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_v3_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_v3_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, entry_ts, max_price_seen, trailing_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            entry_ts,
            entry_price,
            int(trailing_enabled_at_entry()),
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_v3_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_v3_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_v3_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def update_early_reversion_v3_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
    trailing_active: bool = False,
    trailing_stop_price: float | None = None,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v3_trades
        SET max_price_seen = ?,
            last_bid = ?,
            trailing_active = ?,
            trailing_stop_price = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = ?,
            max_profit_pct = COALESCE(?, max_profit_pct)
        WHERE id = ?
        """,
        (
            max_price_seen,
            last_bid,
            int(trailing_active),
            trailing_stop_price,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            trade_id,
        ),
    )


def close_early_reversion_v3_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    stop_loss_trigger_pnl: float | None,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
    realized_profit_pct: float | None = None,
    profit_left_on_table_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v3_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            stop_loss_trigger_pnl = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = COALESCE(?, highest_price),
            max_profit_pct = COALESCE(?, max_profit_pct),
            realized_profit_pct = COALESCE(?, realized_profit_pct),
            profit_left_on_table_pct = COALESCE(?, profit_left_on_table_pct),
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            stop_loss_trigger_pnl,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            realized_profit_pct,
            profit_left_on_table_pct,
            trade_id,
        ),
    )


def has_early_reversion_v25_trade(
    conn: sqlite3.Connection,
    market_slug: str,
    strategy_name: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM early_reversion_v25_trades
        WHERE market_slug = ? AND strategy_name = ?
        LIMIT 1
        """,
        (market_slug, strategy_name),
    ).fetchone()
    return row is not None


def insert_early_reversion_v25_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    strategy_name: str,
    entry_price: float,
    entry_ts: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO early_reversion_v25_trades (
            market_slug, window_start_ts, end_ts, side, strategy_name,
            entry_price, entry_ts, max_price_seen, trailing_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            strategy_name,
            entry_price,
            entry_ts,
            entry_price,
            int(trailing_enabled_at_entry()),
        ),
    )
    return int(cursor.lastrowid)


def get_open_early_reversion_v25_trades(
    conn: sqlite3.Connection,
    market_slug: str | None = None,
) -> list[sqlite3.Row]:
    if market_slug:
        return conn.execute(
            """
            SELECT * FROM early_reversion_v25_trades
            WHERE status = 'open' AND market_slug = ?
            ORDER BY entry_ts ASC
            """,
            (market_slug,),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM early_reversion_v25_trades
        WHERE status = 'open'
        ORDER BY end_ts ASC, entry_ts ASC
        """
    ).fetchall()


def update_early_reversion_v25_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
    trailing_active: bool = False,
    trailing_stop_price: float | None = None,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v25_trades
        SET max_price_seen = ?,
            last_bid = ?,
            trailing_active = ?,
            trailing_stop_price = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = ?,
            max_profit_pct = COALESCE(?, max_profit_pct)
        WHERE id = ?
        """,
        (
            max_price_seen,
            last_bid,
            int(trailing_active),
            trailing_stop_price,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            trade_id,
        ),
    )


def close_early_reversion_v25_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    pnl_percent: float,
    pnl_usdc: float,
    holding_time_seconds: float,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
    realized_profit_pct: float | None = None,
    profit_left_on_table_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE early_reversion_v25_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            pnl_percent = ?,
            pnl_usdc = ?,
            holding_time_seconds = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = COALESCE(?, highest_price),
            max_profit_pct = COALESCE(?, max_profit_pct),
            realized_profit_pct = COALESCE(?, realized_profit_pct),
            profit_left_on_table_pct = COALESCE(?, profit_left_on_table_pct),
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            pnl_percent,
            pnl_usdc,
            holding_time_seconds,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            realized_profit_pct,
            profit_left_on_table_pct,
            trade_id,
        ),
    )


def insert_order_intent(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    trading_mode: str,
    strategy_version: str,
    strategy_name: str,
    market_slug: str,
    side: str,
    token_id: str,
    price: float,
    size_usdc: float,
    shares: float,
    status: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO order_intents (
            idempotency_key, trading_mode, strategy_version, strategy_name,
            market_slug, side, token_id, price, size_usdc, shares, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            idempotency_key,
            trading_mode,
            strategy_version,
            strategy_name,
            market_slug,
            side,
            token_id,
            price,
            size_usdc,
            shares,
            status,
        ),
    )
    return int(cursor.lastrowid)


def has_order_intent(conn: sqlite3.Connection, idempotency_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM order_intents WHERE idempotency_key = ? LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    return row is not None


def get_order_intent(conn: sqlite3.Connection, idempotency_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM order_intents WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def update_order_intent_status(
    conn: sqlite3.Connection,
    idempotency_key: str,
    *,
    status: str,
    clob_order_id: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE order_intents
        SET status = ?,
            clob_order_id = COALESCE(?, clob_order_id),
            error_message = COALESCE(?, error_message),
            updated_at = datetime('now')
        WHERE idempotency_key = ?
        """,
        (status, clob_order_id, error_message, idempotency_key),
    )


def clear_order_intent_error(conn: sqlite3.Connection, idempotency_key: str) -> None:
    conn.execute(
        """
        UPDATE order_intents
        SET error_message = NULL,
            updated_at = datetime('now')
        WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    )


def delete_order_intent(conn: sqlite3.Connection, idempotency_key: str) -> None:
    conn.execute(
        "DELETE FROM order_intents WHERE idempotency_key = ?",
        (idempotency_key,),
    )


def upsert_order_fill_submitted(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    clob_order_id: str,
    clob_side: str,
    token_id: str,
    submitted_price: float,
    submitted_shares: float,
    submitted_notional: float,
) -> None:
    conn.execute(
        """
        INSERT INTO order_fill_audit (
            idempotency_key,
            clob_order_id,
            clob_side,
            token_id,
            submitted_price,
            submitted_shares,
            submitted_notional
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO UPDATE SET
            clob_order_id = COALESCE(excluded.clob_order_id, order_fill_audit.clob_order_id),
            clob_side = excluded.clob_side,
            token_id = excluded.token_id,
            submitted_price = excluded.submitted_price,
            submitted_shares = excluded.submitted_shares,
            submitted_notional = excluded.submitted_notional,
            updated_at = datetime('now')
        """,
        (
            idempotency_key,
            clob_order_id,
            clob_side,
            token_id,
            submitted_price,
            submitted_shares,
            submitted_notional,
        ),
    )


def update_order_fill_audit(
    conn: sqlite3.Connection,
    idempotency_key: str,
    *,
    fill_price: float | None,
    fill_shares: float | None,
    fill_notional: float | None,
    average_fill_price: float | None,
    fees: float | None,
    price_difference: float | None,
    slippage: float | None,
    fill_status: str,
    last_logged_fill_shares: float | None = None,
    raw_order_json: str | None = None,
    raw_trades_json: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE order_fill_audit
        SET fill_price = ?,
            fill_shares = ?,
            fill_notional = ?,
            average_fill_price = ?,
            fees = ?,
            price_difference = ?,
            slippage = ?,
            fill_status = ?,
            last_logged_fill_shares = COALESCE(?, last_logged_fill_shares),
            raw_order_json = COALESCE(?, raw_order_json),
            raw_trades_json = COALESCE(?, raw_trades_json),
            updated_at = datetime('now')
        WHERE idempotency_key = ?
        """,
        (
            fill_price,
            fill_shares,
            fill_notional,
            average_fill_price,
            fees,
            price_difference,
            slippage,
            fill_status,
            last_logged_fill_shares,
            raw_order_json,
            raw_trades_json,
            idempotency_key,
        ),
    )


def get_order_fill_audit(
    conn: sqlite3.Connection,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM order_fill_audit WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def fetch_submitted_orders_pending_fill_audit(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT o.*
        FROM order_intents o
        LEFT JOIN order_fill_audit f ON f.idempotency_key = o.idempotency_key
        WHERE o.status = 'submitted'
          AND o.clob_order_id IS NOT NULL
          AND o.clob_order_id != ''
          AND (f.fill_status IS NULL OR f.fill_status IN ('pending', 'partial'))
        ORDER BY o.updated_at ASC
        """
    ).fetchall()


def insert_v4_shadow_observation(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    timestamp: int,
    seconds_from_start: int,
    seconds_left: int,
    btc_price: float,
    strike: float | None,
    delta: float | None,
    yes_bid: float | None,
    yes_ask: float | None,
    no_bid: float | None,
    no_ask: float | None,
    trend_score: float | None,
    trend_side: str | None,
    spread: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO v4_shadow_observations (
            market_slug, window_start_ts, timestamp, seconds_from_start,
            seconds_left, btc_price, strike, delta,
            yes_bid, yes_ask, no_bid, no_ask,
            trend_score, trend_side, spread
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            timestamp,
            seconds_from_start,
            seconds_left,
            btc_price,
            strike,
            delta,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            trend_score,
            trend_side,
            spread,
        ),
    )


def has_v4_shadow_trade(conn: sqlite3.Connection, market_slug: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM v4_shadow_trades
        WHERE market_slug = ?
        LIMIT 1
        """,
        (market_slug,),
    ).fetchone()
    return row is not None


def get_open_v4_shadow_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM v4_shadow_trades
        WHERE market_slug = ? AND status = 'open'
        LIMIT 1
        """,
        (market_slug,),
    ).fetchone()


def insert_v4_shadow_trade(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    window_start_ts: int,
    end_ts: int,
    side: str,
    entry_price: float,
    entry_ts: int,
    entry_score: float,
    entry_probability: float,
    entry_reason: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO v4_shadow_trades (
            market_slug, window_start_ts, end_ts, side,
            entry_price, entry_ts, entry_score, entry_probability,
            entry_reason, max_price_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            window_start_ts,
            end_ts,
            side,
            entry_price,
            entry_ts,
            entry_score,
            entry_probability,
            entry_reason,
            entry_price,
        ),
    )
    return int(cursor.lastrowid)


def update_v4_shadow_trade_tracking(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    max_price_seen: float,
    last_bid: float,
    trailing_active: bool = False,
    trailing_stop_price: float | None = None,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE v4_shadow_trades
        SET max_price_seen = ?,
            last_bid = ?,
            trailing_active = ?,
            trailing_stop_price = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = ?,
            max_profit_pct = COALESCE(?, max_profit_pct)
        WHERE id = ?
        """,
        (
            max_price_seen,
            last_bid,
            int(trailing_active),
            trailing_stop_price,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            trade_id,
        ),
    )


def close_v4_shadow_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price: float,
    exit_reason: str,
    holding_time_seconds: float,
    trailing_activation_price: float | None = None,
    highest_price: float | None = None,
    max_profit_pct: float | None = None,
    realized_profit_pct: float | None = None,
    profit_left_on_table_pct: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE v4_shadow_trades
        SET status = 'closed',
            exit_price = ?,
            exit_reason = ?,
            holding_time_seconds = ?,
            trailing_activation_price = COALESCE(?, trailing_activation_price),
            highest_price = COALESCE(?, highest_price),
            max_profit_pct = COALESCE(?, max_profit_pct),
            realized_profit_pct = COALESCE(?, realized_profit_pct),
            profit_left_on_table_pct = COALESCE(?, profit_left_on_table_pct),
            closed_at = datetime('now')
        WHERE id = ?
        """,
        (
            exit_price,
            exit_reason,
            holding_time_seconds,
            trailing_activation_price,
            highest_price,
            max_profit_pct,
            realized_profit_pct,
            profit_left_on_table_pct,
            trade_id,
        ),
    )


def count_all_open_positions(conn: sqlite3.Connection) -> int:
    total = 0
    total += conn.execute(
        "SELECT COUNT(*) AS c FROM virtual_trades WHERE status = 'open'"
    ).fetchone()["c"]
    for table in (
        "early_reversion_trades",
        "early_reversion_v2_trades",
        "early_reversion_v25_trades",
        "early_reversion_v3_trades",
    ):
        total += conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE status = 'open'"
        ).fetchone()["c"]
    return int(total)


def daily_realized_pnl_usdc(conn: sqlite3.Connection) -> float:
    total = 0.0
    for table, ts_col in (
        ("virtual_trades", "settled_at"),
        ("early_reversion_trades", "closed_at"),
        ("early_reversion_v2_trades", "closed_at"),
        ("early_reversion_v25_trades", "closed_at"),
        ("early_reversion_v3_trades", "closed_at"),
    ):
        if table == "virtual_trades":
            status_filter = "status = 'settled'"
        else:
            status_filter = "status = 'closed'"
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(pnl_usdc), 0) AS pnl
            FROM {table}
            WHERE {status_filter}
              AND {ts_col} IS NOT NULL
              AND date({ts_col}) = date('now')
            """
        ).fetchone()
        total += float(row["pnl"])
    return total
