"""SQLite schema for Portfolio Simulator V1."""

from __future__ import annotations

from typing import Any

SIM_TABLE = "portfolio_simulations_v1"
EQUITY_TABLE = "portfolio_equity_v1"

SIM_DDL = f"""
CREATE TABLE IF NOT EXISTS {SIM_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    capital REAL NOT NULL,
    risk_model TEXT NOT NULL,
    risk_param REAL,
    n_trades INTEGER,
    pnl REAL,
    ret_pct REAL,
    cagr REAL,
    sharpe REAL,
    sortino REAL,
    calmar REAL,
    pf REAL,
    wr REAL,
    max_dd REAL,
    ulcer REAL,
    mar REAL,
    recovery REAL,
    exposure REAL,
    rank_score REAL,
    meta_json TEXT,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_psim_v1_mode ON {SIM_TABLE}(mode, sharpe DESC);
CREATE INDEX IF NOT EXISTS idx_psim_v1_rank ON {SIM_TABLE}(rank_score DESC);

CREATE TABLE IF NOT EXISTS {EQUITY_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_key TEXT NOT NULL,
    step INTEGER NOT NULL,
    equity REAL NOT NULL,
    trade_id INTEGER,
    pnl REAL,
    UNIQUE(sim_key, step)
);
CREATE INDEX IF NOT EXISTS idx_peq_v1_key ON {EQUITY_TABLE}(sim_key, step);
"""


def ensure_portfolio_sim_schema(conn: Any) -> None:
    conn.executescript(SIM_DDL)
    try:
        conn.commit()
    except Exception:
        pass


__all__ = [
    "EQUITY_TABLE",
    "SIM_DDL",
    "SIM_TABLE",
    "ensure_portfolio_sim_schema",
]
