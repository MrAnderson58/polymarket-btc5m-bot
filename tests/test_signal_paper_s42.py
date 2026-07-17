"""Tests for S4.2 Paper Performance Tracker (observe-only)."""

from __future__ import annotations

import sqlite3
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
    CAPITAL_PER_TRADE_USD,
    LEVERAGE,
    STATUS_CLOSED,
    _close_trade,
    _margin_pnl_usd,
    _max_drawdown_pct_from_pnl_usd,
    format_paper_performance_s42,
    open_paper_trades_from_s40,
    paper_performance_dashboard_s42,
    paper_trade_counts_s42,
    run_paper_performance_cycle_s42,
)


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


class TestPaperPerformanceS42(unittest.TestCase):
    def test_margin_pnl_usd_leverage(self) -> None:
        # +1% price move on $100 margin @ 20x => $20
        self.assertEqual(_margin_pnl_usd(1.0), 20.0)

    def test_max_drawdown_pct_equity_based(self) -> None:
        # Start $100, +$50 peak $150, then -$75 => equity $75 => 50% DD from peak
        dd = _max_drawdown_pct_from_pnl_usd([50.0, -75.0], initial_equity=100.0)
        self.assertEqual(dd, 50.0)
        # Catastrophic streak caps at 100% (not thousands of %)
        dd_many = _max_drawdown_pct_from_pnl_usd([-20.0] * 100, initial_equity=100.0)
        self.assertEqual(dd_many, 100.0)

    def test_paper_trade_counts(self) -> None:
        conn = _mem_conn()
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_signal_learning_s40_signals (
              signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
              timestamp, created_at, updated_at
            ) VALUES ('g3', 99, 'BTC', 'LONG', 100.0, 95.0, 105.0, 110.0, ?, ?, ?)
            """,
            (now, now, now),
        )
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, capital_usd, leverage, updated_at
            ) VALUES ('g3', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
              mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason,
              status, capital_usd, leverage, updated_at
            ) VALUES ('g3', 2, 'ETH', 'LONG', 100, 95, 105, 110, ?, ?, 3600,
              0, 0, 0, 0, 'BE', 'TIMEOUT', ?, 100, 20, ?)
            """,
            (now - 3600, now, STATUS_CLOSED, now),
        )
        conn.commit()
        counts = paper_trade_counts_s42(conn)
        self.assertEqual(counts["open_trades"], 1)
        self.assertEqual(counts["closed_trades"], 1)
        self.assertEqual(counts["breakeven_trades"], 1)
        self.assertEqual(counts["pending_settlement"], 1)

    def test_open_from_s40_signal(self) -> None:
        conn = _mem_conn()
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_signal_learning_s40_signals (
              signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
              timestamp, snapshot_decision_confidence, created_at, updated_at
            ) VALUES ('g3', 1, 'BTC', 'LONG', 100.0, 95.0, 105.0, 110.0, ?, 6.5, ?, ?)
            """,
            (now, now, now),
        )
        conn.commit()
        opened = open_paper_trades_from_s40(conn)
        conn.commit()
        self.assertEqual(opened, 1)
        row = conn.execute(
            "SELECT symbol, status, capital_usd, leverage FROM market_events_paper_trades_s42",
        ).fetchone()
        self.assertEqual(row["symbol"], "BTC")
        self.assertEqual(row["status"], "OPEN")
        self.assertEqual(row["capital_usd"], CAPITAL_PER_TRADE_USD)
        self.assertEqual(row["leverage"], LEVERAGE)

    def test_close_trade_updates_account(self) -> None:
        conn = _mem_conn()
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, capital_usd, leverage, updated_at
            ) VALUES ('g3', 1, 'SOL', 'LONG', 100.0, 95.0, 105.0, 110.0, ?, 'OPEN', 0, 0, 100, 20, ?)
            """,
            (now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
        _close_trade(conn, row=row, exit_price=105.0, exit_reason="TP1", now=now + 3600)
        conn.commit()
        closed = conn.execute(
            "SELECT result, exit_reason, pnl_usd FROM market_events_paper_trades_s42",
        ).fetchone()
        self.assertEqual(closed["result"], "WIN")
        self.assertEqual(closed["exit_reason"], "TP1")
        self.assertEqual(closed["pnl_usd"], 100.0)  # +5% * 20x * $100
        equity = conn.execute(
            "SELECT current_equity FROM market_events_paper_account_s42 WHERE id = 1",
        ).fetchone()["current_equity"]
        self.assertEqual(equity, 200.0)

    def test_dashboard_and_cli_readonly(self) -> None:
        conn = _mem_conn()
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_paper_account_s42 (id, initial_capital, current_equity, updated_at)
            VALUES (1, 100, 120, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
              mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason,
              status, capital_usd, leverage, updated_at
            ) VALUES ('g3', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, ?, 3600,
              5, -1, 5, 20, 'WIN', 'TP1', ?, 100, 20, ?)
            """,
            (now - 3600, now, STATUS_CLOSED, now),
        )
        conn.commit()
        dash = paper_performance_dashboard_s42(conn)
        self.assertEqual(dash["current_equity"], 120.0)
        self.assertEqual(dash["trades"], 1)
        self.assertEqual(dash["winrate_pct"], 100.0)
        text = format_paper_performance_s42(conn)
        self.assertIn("Paper Performance", text)
        self.assertIn("Current Equity", text)
        self.assertIn("Open Trades", text)
        self.assertIn("Pending Settlement", text)

    @patch(
        "bot.research.market_events.signal_intelligence.signal_paper_performance_s42.open_paper_trades_from_s40",
        return_value=0,
    )
    @patch(
        "bot.research.market_events.signal_intelligence.signal_paper_performance_s42.tick_open_paper_trades_s42",
        return_value=0,
    )
    @patch(
        "bot.research.market_events.signal_intelligence.signal_paper_performance_s42.maybe_emit_scheduled_reports_s42",
        return_value={"daily": False, "weekly": False},
    )
    def test_cycle_smoke(self, *_mocks: object) -> None:
        stats = run_paper_performance_cycle_s42()
        self.assertIn("opened", stats)
        self.assertIn("ticked", stats)


if __name__ == "__main__":
    unittest.main()
