"""S47 — SQLite persistence for AI paper trading."""

from __future__ import annotations

import json
from typing import Any

from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.models import Fill, PaperTrade, STATUS_CLOSED, STATUS_OPEN
from bot.research.ai_analyst.paper_trading.signals import TradingSignal

S47_PAPER_DDL = """
CREATE TABLE IF NOT EXISTS ai_paper_account_s47 (
    id INTEGER PRIMARY KEY,
    initial_equity REAL NOT NULL,
    current_equity REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_paper_signals_s47 (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry_low REAL NOT NULL,
    entry_high REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    risk_pct REAL NOT NULL,
    confidence REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_paper_trades_s47 (
    trade_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    strategy TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    risk_pct REAL NOT NULL,
    confidence REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    size_remaining REAL NOT NULL,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    mfe_pct REAL NOT NULL DEFAULT 0,
    mae_pct REAL NOT NULL DEFAULT 0,
    mfe_r REAL NOT NULL DEFAULT 0,
    mae_r REAL NOT NULL DEFAULT 0,
    pnl_usd REAL NOT NULL DEFAULT 0,
    r_multiple REAL NOT NULL DEFAULT 0,
    holding_seconds INTEGER NOT NULL DEFAULT 0,
    closed_at INTEGER,
    exit_reason TEXT,
    exit_price REAL,
    fills_json TEXT NOT NULL,
    account_equity_at_open REAL NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_paper_trades_s47_status
  ON ai_paper_trades_s47(status, opened_at);
CREATE INDEX IF NOT EXISTS idx_ai_paper_trades_s47_strategy
  ON ai_paper_trades_s47(strategy, status);
"""


def ensure_account(conn: Any, *, equity: float, now: int) -> None:
    row = conn.execute("SELECT id FROM ai_paper_account_s47 WHERE id = 1").fetchone()
    if row:
        conn.execute(
            "UPDATE ai_paper_account_s47 SET current_equity = ?, updated_at = ? WHERE id = 1",
            (equity, now),
        )
    else:
        conn.execute(
            """
            INSERT INTO ai_paper_account_s47 (id, initial_equity, current_equity, updated_at)
            VALUES (1, ?, ?, ?)
            """,
            (equity, equity, now),
        )


def upsert_signal(conn: Any, signal: TradingSignal) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_paper_signals_s47 (
          signal_id, symbol, direction, strategy, entry_low, entry_high,
          stop_loss, tp1, tp2, tp3, risk_pct, confidence, reasons_json,
          status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.signal_id,
            signal.symbol,
            signal.direction,
            signal.strategy,
            signal.entry_low,
            signal.entry_high,
            signal.stop_loss,
            signal.tp1,
            signal.tp2,
            signal.tp3,
            signal.risk_pct,
            signal.confidence,
            json.dumps(signal.reasons, ensure_ascii=False),
            signal.status,
            signal.created_at,
        ),
    )


def upsert_trade(conn: Any, trade: PaperTrade, *, now: int) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_paper_trades_s47 (
          trade_id, signal_id, symbol, direction, strategy, entry, stop_loss,
          tp1, tp2, tp3, risk_pct, confidence, reasons_json, opened_at, status,
          size_remaining, tp1_hit, tp2_hit, tp3_hit, mfe_pct, mae_pct, mfe_r, mae_r,
          pnl_usd, r_multiple, holding_seconds, closed_at, exit_reason, exit_price,
          fills_json, account_equity_at_open, updated_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            trade.trade_id,
            trade.signal_id,
            trade.symbol,
            trade.direction,
            trade.strategy,
            trade.entry,
            trade.stop_loss,
            trade.tp1,
            trade.tp2,
            trade.tp3,
            trade.risk_pct,
            trade.confidence,
            json.dumps(trade.reasons, ensure_ascii=False),
            trade.opened_at,
            trade.status,
            trade.size_remaining,
            int(trade.tp1_hit),
            int(trade.tp2_hit),
            int(trade.tp3_hit),
            trade.mfe_pct,
            trade.mae_pct,
            trade.mfe_r,
            trade.mae_r,
            trade.pnl_usd,
            trade.r_multiple,
            trade.holding_seconds,
            trade.closed_at,
            trade.exit_reason,
            trade.exit_price,
            json.dumps(
                [
                    {
                        "level": f.level,
                        "price": f.price,
                        "fraction": f.fraction,
                        "pnl_usd": f.pnl_usd,
                        "r_multiple": f.r_multiple,
                        "ts": f.ts,
                    }
                    for f in trade.fills
                ],
                ensure_ascii=False,
            ),
            trade.account_equity_at_open,
            now,
        ),
    )


def persist_engine(conn: Any, engine: PaperTradingEngine, *, now: int) -> None:
    ensure_account(conn, equity=engine.equity, now=now)
    for sig in engine.signals.values():
        upsert_signal(conn, sig)
    for trade in engine.trades.values():
        upsert_trade(conn, trade, now=now)


def load_engine(conn: Any) -> PaperTradingEngine:
    row = conn.execute(
        "SELECT initial_equity, current_equity FROM ai_paper_account_s47 WHERE id = 1",
    ).fetchone()
    initial = float(row["initial_equity"]) if row else 10_000.0
    equity = float(row["current_equity"]) if row else initial
    engine = PaperTradingEngine(initial_equity=initial)
    engine.equity = equity

    for r in conn.execute("SELECT * FROM ai_paper_signals_s47").fetchall():
        d = dict(r)
        d["reasons"] = json.loads(d.pop("reasons_json") or "[]")
        sig = TradingSignal.from_dict(d)
        engine.signals[sig.signal_id] = sig

    for r in conn.execute("SELECT * FROM ai_paper_trades_s47").fetchall():
        d = dict(r)
        reasons = json.loads(d.pop("reasons_json") or "[]")
        fills_raw = json.loads(d.pop("fills_json") or "[]")
        fills = [
            Fill(
                level=str(f["level"]),
                price=float(f["price"]),
                fraction=float(f["fraction"]),
                pnl_usd=float(f["pnl_usd"]),
                r_multiple=float(f["r_multiple"]),
                ts=int(f["ts"]),
            )
            for f in fills_raw
        ]
        trade = PaperTrade(
            trade_id=str(d["trade_id"]),
            signal_id=str(d["signal_id"]),
            symbol=str(d["symbol"]),
            direction=str(d["direction"]),
            strategy=str(d["strategy"]),
            entry=float(d["entry"]),
            stop_loss=float(d["stop_loss"]),
            tp1=float(d["tp1"]),
            tp2=float(d["tp2"]),
            tp3=float(d["tp3"]),
            risk_pct=float(d["risk_pct"]),
            confidence=float(d["confidence"]),
            reasons=list(reasons),
            opened_at=int(d["opened_at"]),
            status=str(d["status"]),
            size_remaining=float(d["size_remaining"]),
            tp1_hit=bool(d["tp1_hit"]),
            tp2_hit=bool(d["tp2_hit"]),
            tp3_hit=bool(d["tp3_hit"]),
            mfe_pct=float(d["mfe_pct"] or 0),
            mae_pct=float(d["mae_pct"] or 0),
            mfe_r=float(d["mfe_r"] or 0),
            mae_r=float(d["mae_r"] or 0),
            pnl_usd=float(d["pnl_usd"] or 0),
            r_multiple=float(d["r_multiple"] or 0),
            holding_seconds=int(d["holding_seconds"] or 0),
            closed_at=int(d["closed_at"]) if d["closed_at"] is not None else None,
            exit_reason=d["exit_reason"],
            exit_price=float(d["exit_price"]) if d["exit_price"] is not None else None,
            fills=fills,
            account_equity_at_open=float(d["account_equity_at_open"]),
        )
        if trade.status not in (STATUS_OPEN, STATUS_CLOSED):
            trade.status = STATUS_OPEN
        engine.trades[trade.trade_id] = trade
    return engine
