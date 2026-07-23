"""S55.2 trade regression audit tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    record_trade_features_on_open,
)
from bot.research.market_events.signal_intelligence.trade_regression_audit_s55 import (
    code_change_summary,
    format_trade_regression_audit,
    gate_diagnostics,
    run_trade_regression_audit,
)


class TradeRegressionAuditS55Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s55_audit.db"
        configure_unit_test_db_isolation(self.db)
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            # Legacy backlog: many OPEN trades pre-S55
            for i in range(30):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      mfe_pct, mae_pct, capital_usd, leverage, updated_at
                    ) VALUES ('legacy', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                    """,
                    (i, self.now - 86400 * 10, self.now),
                )
            # Closed winners/losers
            for i, (sym, pnl, reason) in enumerate(
                [
                    ("BTC", 25.0, "TP2"),
                    ("ETH", -40.0, "STOP"),
                    ("SOL", 10.0, "TRAILING"),
                    ("DOGE", -55.0, "TIMEOUT"),
                ]
            ):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                      mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason,
                      status, capital_usd, leverage, updated_at
                    ) VALUES ('sig', ?, ?, 'LONG', 1, 0.9, 1.1, 1.2, ?, ?, 60,
                      0, 0, ?, ?, ?, ?, 'CLOSED', 100, 20, ?)
                    """,
                    (
                        100 + i,
                        sym,
                        self.now - 3600,
                        self.now - 60,
                        pnl,
                        pnl,
                        "WIN" if pnl > 0 else "LOSS",
                        reason,
                        self.now,
                    ),
                )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_code_change_summary_flags(self) -> None:
        cc = code_change_summary()
        self.assertTrue(cc["open_criteria_changed"])
        self.assertFalse(cc["stops_tp_changed"])
        self.assertFalse(cc["position_size_changed"])
        self.assertTrue(cc["management_changed"])

    def test_gate_paradox_explanation(self) -> None:
        with market_events_connection() as conn:
            diag = gate_diagnostics(conn)
        self.assertEqual(diag["current_open"], 30)
        self.assertGreater(diag["current_open"], diag["max_open"])
        self.assertEqual(diag["features_total"], 0)
        self.assertTrue(any("max_open" in e for e in diag["explanations"]))

    def test_audit_report_shape(self) -> None:
        with market_events_connection() as conn:
            text = format_trade_regression_audit(conn, deploy_ts=self.now - 7200, top_n=10)
        self.assertIn("S55.3 Trade Recovery & Regression Audit", text)
        self.assertIn("Gate diagnostics", text)
        self.assertIn("Top 10 losers", text)

    def test_pnl_breakdown(self) -> None:
        with market_events_connection() as conn:
            data = run_trade_regression_audit(conn, deploy_ts=self.now - 86400, top_n=10)
        by_sym = {r["key"]: r["pnl_usd"] for r in data["pnl_since_deploy"]["by_symbol"]}
        self.assertIn("ETH", by_sym)
        self.assertLess(by_sym["ETH"], 0)

    def test_gate_reject_counted_today(self) -> None:
        with market_events_connection() as conn:
            record_trade_features_on_open(
                conn,
                paper_trade_id=None,
                s40_signal_type="x",
                s40_signal_id=1,
                features={"symbol": "BTC", "direction": "LONG"},
                gate_decision="reject_expected_pnl",
                estimate={"expected_pnl_pct": -1.0, "similar_count": 50},
                now=self.now,
            )
            conn.commit()
            diag = gate_diagnostics(conn)
        self.assertEqual(diag["rejected_today"], 1)
        self.assertEqual(diag["gate_invocations_today"], 1)


if __name__ == "__main__":
    unittest.main()
