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
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP')),
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
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP')),
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
    last_bid REAL,
    exit_price REAL,
    exit_reason TEXT CHECK (exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'TIME_STOP')),
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
