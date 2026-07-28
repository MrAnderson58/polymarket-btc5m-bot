"""Trade Intelligence V1 SQLite schema — additive, idempotent."""

from __future__ import annotations

from typing import Any

TI_V1_DDL = """
CREATE TABLE IF NOT EXISTS ti_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_ts INTEGER,
    exit_ts INTEGER,
    entry_price REAL,
    exit_price REAL,
    size REAL,
    pnl_usd REAL,
    pnl_pct REAL,
    strategy TEXT,
    status TEXT NOT NULL DEFAULT 'closed',
    raw_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_ti_trades_symbol ON ti_trades(symbol, entry_ts DESC);
CREATE INDEX IF NOT EXISTS idx_ti_trades_source ON ti_trades(source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ti_trades_status ON ti_trades(status, exit_ts DESC);

CREATE TABLE IF NOT EXISTS ti_market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    snapshot_ts INTEGER,
    price REAL,
    funding REAL,
    oi REAL,
    volatility REAL,
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_snap_trade ON ti_market_snapshots(trade_id, snapshot_ts);

CREATE TABLE IF NOT EXISTS ti_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    news_ts INTEGER,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    source TEXT,
    url TEXT,
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_news_trade ON ti_news(trade_id, news_ts);

CREATE TABLE IF NOT EXISTS ti_telegram (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    message_ts INTEGER,
    channel TEXT,
    message_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_telegram_trade ON ti_telegram(trade_id, message_ts);

CREATE TABLE IF NOT EXISTS ti_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    context_key TEXT NOT NULL,
    context_value TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_context_trade ON ti_context(trade_id, context_key);

CREATE TABLE IF NOT EXISTS ti_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL UNIQUE,
    outcome_ts INTEGER,
    result TEXT,
    pnl_usd REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ti_ai_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    model TEXT,
    summary_text TEXT NOT NULL DEFAULT '',
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_ai_trade ON ti_ai_summaries(trade_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ti_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(trade_id, tag),
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_tags_tag ON ti_tags(tag, trade_id);

CREATE TABLE IF NOT EXISTS ti_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    note_ts INTEGER,
    author TEXT,
    text TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trade_id) REFERENCES ti_trades(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ti_notes_trade ON ti_notes(trade_id, note_ts DESC);

CREATE TABLE IF NOT EXISTS ti_paper_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_trade_id INTEGER NOT NULL UNIQUE,
    knowledge_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ti_paper_knowledge_updated ON ti_paper_knowledge(updated_at DESC);
"""


def ensure_trade_intelligence_schema(conn: Any) -> None:
    """Create Trade Intelligence V1 tables if missing (live-safe)."""
    try:
        conn.executescript(TI_V1_DDL)
    except Exception:
        pass
