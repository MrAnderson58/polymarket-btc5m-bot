-- Polymarket BTC 5m paper trading schema

CREATE TABLE IF NOT EXISTS virtual_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    condition_id TEXT,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    strike_price REAL NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    token_id TEXT NOT NULL,
    entry_ask REAL NOT NULL,
    entry_bid REAL,
    btc_price_at_entry REAL NOT NULL,
    btc_delta_at_entry REAL NOT NULL,
    min_delta_after_entry REAL,
    max_delta_after_entry REAL,
    seconds_remaining REAL NOT NULL,
    size_usdc REAL NOT NULL,
    shares REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'settled')),
    outcome TEXT CHECK (outcome IN ('win', 'loss')),
    settlement_btc_price REAL,
    market_end_price REAL,
    distance_at_close REAL,
    payout_usdc REAL,
    pnl_usdc REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    settled_at TEXT,
    UNIQUE (market_slug, side)
);

CREATE INDEX IF NOT EXISTS idx_virtual_trades_status ON virtual_trades (status);
CREATE INDEX IF NOT EXISTS idx_virtual_trades_market_slug ON virtual_trades (market_slug);
CREATE INDEX IF NOT EXISTS idx_virtual_trades_end_ts ON virtual_trades (end_ts);

CREATE TABLE IF NOT EXISTS market_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    seconds_remaining REAL NOT NULL,
    strike_price REAL NOT NULL,
    btc_price REAL NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    signal TEXT CHECK (signal IN ('BUY_YES', 'BUY_NO')),
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_market_checks_slug ON market_checks (market_slug);
CREATE INDEX IF NOT EXISTS idx_market_checks_checked_at ON market_checks (checked_at);

CREATE TABLE IF NOT EXISTS no_c_filter_shadow_counters (
    lookback_sec INTEGER NOT NULL,
    threshold_usd REAL NOT NULL,
    check_count INTEGER NOT NULL DEFAULT 0,
    would_block_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lookback_sec, threshold_usd)
);

CREATE TABLE IF NOT EXISTS no_c_filter_shadow_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    current_btc REAL NOT NULL,
    btc_move_30s REAL,
    btc_move_60s REAL,
    btc_move_90s REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_no_c_filter_shadow_entries_ts
    ON no_c_filter_shadow_entries (entry_ts);

CREATE TABLE IF NOT EXISTS no_c_filter_live_counters (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    normal_entries INTEGER NOT NULL DEFAULT 0,
    strict_entries INTEGER NOT NULL DEFAULT 0,
    skipped_strict_price INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS early_reversion_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL UNIQUE,
    window_start_ts INTEGER NOT NULL,
    min_yes_price REAL,
    max_yes_price REAL,
    min_no_price REAL,
    max_no_price REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS early_reversion_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    target_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    reached_target INTEGER NOT NULL DEFAULT 0,
    max_profit_percent REAL NOT NULL DEFAULT 0,
    max_drawdown_percent REAL NOT NULL DEFAULT 0,
    last_bid REAL,
    time_to_target_seconds REAL,
    exit_price REAL,
    pnl_percent REAL,
    pnl_usdc REAL,
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug, strategy_name)
);

CREATE INDEX IF NOT EXISTS idx_early_reversion_trades_status
    ON early_reversion_trades (status);
CREATE INDEX IF NOT EXISTS idx_early_reversion_trades_market_slug
    ON early_reversion_trades (market_slug);

CREATE TABLE IF NOT EXISTS early_reversion_v2_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    max_price_seen REAL,
    trailing_enabled INTEGER NOT NULL DEFAULT 0,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_stop_price REAL,
    trailing_activation_price REAL,
    highest_price REAL,
    max_profit_pct REAL,
    realized_profit_pct REAL,
    profit_left_on_table_pct REAL,
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP', 'RECOVERY_NO_POSITION')),
    pnl_percent REAL,
    pnl_usdc REAL,
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug, strategy_name)
);

CREATE INDEX IF NOT EXISTS idx_early_reversion_v2_trades_status
    ON early_reversion_v2_trades (status);
CREATE INDEX IF NOT EXISTS idx_early_reversion_v2_trades_market_slug
    ON early_reversion_v2_trades (market_slug);

CREATE TABLE IF NOT EXISTS yes_c_shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    strategy_name TEXT NOT NULL DEFAULT 'YES_C',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    max_price_seen REAL,
    trailing_enabled INTEGER NOT NULL DEFAULT 0,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_stop_price REAL,
    trailing_activation_price REAL,
    highest_price REAL,
    max_profit_pct REAL,
    realized_profit_pct REAL,
    profit_left_on_table_pct REAL,
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP', 'RECOVERY_NO_POSITION')),
    pnl_percent REAL,
    pnl_usdc REAL,
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug, strategy_name)
);

CREATE INDEX IF NOT EXISTS idx_yes_c_shadow_trades_status
    ON yes_c_shadow_trades (status);
CREATE INDEX IF NOT EXISTS idx_yes_c_shadow_trades_market_slug
    ON yes_c_shadow_trades (market_slug);

CREATE TABLE IF NOT EXISTS early_reversion_v3_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    max_price_seen REAL,
    trailing_enabled INTEGER NOT NULL DEFAULT 0,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_stop_price REAL,
    trailing_activation_price REAL,
    highest_price REAL,
    max_profit_pct REAL,
    realized_profit_pct REAL,
    profit_left_on_table_pct REAL,
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP', 'RECOVERY_NO_POSITION')),
    stop_loss_trigger_pnl REAL,
    pnl_percent REAL,
    pnl_usdc REAL,
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug, strategy_name)
);

CREATE INDEX IF NOT EXISTS idx_early_reversion_v3_trades_status
    ON early_reversion_v3_trades (status);
CREATE INDEX IF NOT EXISTS idx_early_reversion_v3_trades_market_slug
    ON early_reversion_v3_trades (market_slug);

CREATE TABLE IF NOT EXISTS early_reversion_v25_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    strategy_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    max_price_seen REAL,
    trailing_enabled INTEGER NOT NULL DEFAULT 0,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_stop_price REAL,
    trailing_activation_price REAL,
    highest_price REAL,
    max_profit_pct REAL,
    realized_profit_pct REAL,
    profit_left_on_table_pct REAL,
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP', 'RECOVERY_NO_POSITION')),
    pnl_percent REAL,
    pnl_usdc REAL,
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug, strategy_name)
);

CREATE INDEX IF NOT EXISTS idx_early_reversion_v25_trades_status
    ON early_reversion_v25_trades (status);
CREATE INDEX IF NOT EXISTS idx_early_reversion_v25_trades_market_slug
    ON early_reversion_v25_trades (market_slug);

CREATE TABLE IF NOT EXISTS order_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    trading_mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    token_id TEXT NOT NULL,
    price REAL NOT NULL,
    size_usdc REAL NOT NULL,
    shares REAL NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'submitted', 'dry_run', 'failed', 'paper')
    ),
    clob_order_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents (status);
CREATE INDEX IF NOT EXISTS idx_order_intents_market ON order_intents (market_slug);

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
);

CREATE INDEX IF NOT EXISTS idx_order_fill_audit_clob_order_id
    ON order_fill_audit (clob_order_id);
CREATE INDEX IF NOT EXISTS idx_order_fill_audit_fill_status
    ON order_fill_audit (fill_status);

CREATE TABLE IF NOT EXISTS er_strategy_counters (
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    checks INTEGER NOT NULL DEFAULT 0,
    price_reached INTEGER NOT NULL DEFAULT 0,
    window_ok INTEGER NOT NULL DEFAULT 0,
    price_ok INTEGER NOT NULL DEFAULT 0,
    window_and_price_ok INTEGER NOT NULL DEFAULT 0,
    already_open_ok INTEGER NOT NULL DEFAULT 0,
    risk_ok INTEGER NOT NULL DEFAULT 0,
    entry_attempt INTEGER NOT NULL DEFAULT 0,
    entry_success INTEGER NOT NULL DEFAULT 0,
    blocked_by_window INTEGER NOT NULL DEFAULT 0,
    blocked_by_price INTEGER NOT NULL DEFAULT 0,
    blocked_by_already_open INTEGER NOT NULL DEFAULT 0,
    blocked_by_risk INTEGER NOT NULL DEFAULT 0,
    blocked_max_open_positions INTEGER NOT NULL DEFAULT 0,
    blocked_daily_loss INTEGER NOT NULL DEFAULT 0,
    blocked_duplicate_entry INTEGER NOT NULL DEFAULT 0,
    blocked_existing_position INTEGER NOT NULL DEFAULT 0,
    blocked_live_mode INTEGER NOT NULL DEFAULT 0,
    blocked_other INTEGER NOT NULL DEFAULT 0,
    blocked_unknown INTEGER NOT NULL DEFAULT 0,
    entries INTEGER NOT NULL DEFAULT 0,
    exits_tp INTEGER NOT NULL DEFAULT 0,
    exits_stop INTEGER NOT NULL DEFAULT 0,
    exits_expiration INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_version, strategy_name)
);

CREATE TABLE IF NOT EXISTS er_ask_level_counters (
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    ask_level REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_version, strategy_name, ask_level)
);

CREATE TABLE IF NOT EXISTS er_timing_counters (
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    seconds_bucket TEXT NOT NULL,
    price_reached_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_version, strategy_name, seconds_bucket)
);

CREATE TABLE IF NOT EXISTS er_funnel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    market_slug TEXT,
    eval_ts INTEGER NOT NULL,
    window_ok INTEGER NOT NULL DEFAULT 0,
    price_ok INTEGER NOT NULL DEFAULT 0,
    already_open_ok INTEGER NOT NULL DEFAULT 0,
    risk_ok INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_er_funnel_events_eval_ts
    ON er_funnel_events (eval_ts);
CREATE INDEX IF NOT EXISTS idx_er_funnel_events_strategy_ts
    ON er_funnel_events (strategy_version, strategy_name, eval_ts);

CREATE TABLE IF NOT EXISTS er_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_er_health_events_type_ts
    ON er_health_events (event_type, event_ts);

CREATE TABLE IF NOT EXISTS v4_shadow_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    seconds_from_start INTEGER NOT NULL,
    seconds_left INTEGER NOT NULL,
    btc_price REAL NOT NULL,
    strike REAL,
    delta REAL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    trend_score REAL,
    trend_side TEXT,
    spread REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_v4_shadow_observations_market_ts
    ON v4_shadow_observations (market_slug, timestamp);

CREATE TABLE IF NOT EXISTS v4_shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    window_start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_score REAL NOT NULL,
    entry_probability REAL NOT NULL,
    entry_reason TEXT NOT NULL,
    max_price_seen REAL,
    trailing_active INTEGER NOT NULL DEFAULT 0,
    trailing_stop_price REAL,
    trailing_activation_price REAL,
    highest_price REAL,
    max_profit_pct REAL,
    realized_profit_pct REAL,
    profit_left_on_table_pct REAL,
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'TIME_STOP')),
    holding_time_seconds REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE (market_slug)
);

CREATE INDEX IF NOT EXISTS idx_v4_shadow_trades_status
    ON v4_shadow_trades (status);
CREATE INDEX IF NOT EXISTS idx_v4_shadow_trades_market_slug
    ON v4_shadow_trades (market_slug);

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
);

CREATE INDEX IF NOT EXISTS idx_trade_features_strategy
    ON trade_features (strategy_name, entry_ts);
