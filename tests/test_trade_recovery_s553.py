"""S55.3 — recovery / gate logging / portfolio / metrics regression tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55
from bot.research.market_events.signal_intelligence.portfolio_manager_s55 import (
    EXIT_STALE,
    enforce_max_open_hard_cap,
    sweep_stale_opens,
)
from bot.research.market_events.signal_intelligence.trade_regression_audit_s55 import (
    audit_open_trades,
    detect_regression,
    format_trade_regression_audit,
)


def _mem() -> object:
    tmp = tempfile.TemporaryDirectory()
    db = Path(tmp.name) / "s553.db"
    configure_unit_test_db_isolation(db)
    # keep tmp alive on conn via attribute
    with market_events_connection() as conn:
        apply_migrations(conn)
        conn.commit()
    # return factory-bound connection context is awkward; use connection each call
    class H:
        def __init__(self) -> None:
            self._tmp = tmp
            self.db = db

        def conn(self):
            return market_events_connection()

    return H()


class TestS553GateAlwaysLogs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "gate.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_max_open_rejects_are_logged(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            # Seed 2 opens already
            for i in range(2):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      mfe_pct, mae_pct, capital_usd, leverage, updated_at
                    ) VALUES ('legacy', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                    """,
                    (i, now, now),
                )
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO market_events_signal_learning_s40_signals (
                      signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
                      timestamp, snapshot_decision_confidence, snapshot_funding,
                      created_at, updated_at
                    ) VALUES ('g3', ?, 'ETH', 'LONG', 100, 95, 105, 110, ?, 7, 0.01, ?, ?)
                    """,
                    (100 + i, now, now, now),
                )
            conn.commit()

            with patch.object(s55, "S55_MAX_OPEN_TRADES", 2), patch.object(s55, "S55_ENABLED", True), patch.object(
                s55, "S55_MIN_SIMILAR", 20,
            ), patch(
                "bot.research.market_events.signal_intelligence.portfolio_manager_s55.S55_PORTFOLIO_REPLACE_ENABLED",
                False,
            ):
                opened = s42.open_paper_trades_from_s40(conn, limit=10)
                conn.commit()

            self.assertEqual(opened, 0)
            open_n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status='OPEN'",
            ).fetchone()["n"]
            self.assertEqual(open_n, 2)
            rejects = conn.execute(
                """
                SELECT COUNT(*) AS n FROM market_events_trade_features_s55
                WHERE gate_decision = ?
                """,
                (s55.GATE_MAX_OPEN,),
            ).fetchone()["n"]
            self.assertGreaterEqual(rejects, 1)

    def test_gate_always_invoked_per_candidate(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO market_events_signal_learning_s40_signals (
                      signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
                      timestamp, snapshot_decision_confidence, created_at, updated_at
                    ) VALUES ('g3', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 7, ?, ?)
                    """,
                    (i + 1, now, now, now),
                )
            conn.commit()
            with patch.object(s55, "S55_ENABLED", True), patch.object(s55, "S55_MAX_OPEN_TRADES", 25), patch.object(
                s55, "S55_MIN_SIMILAR", 20,
            ):
                opened = s42.open_paper_trades_from_s40(conn, limit=10)
                conn.commit()
            self.assertEqual(opened, 3)
            n = conn.execute("SELECT COUNT(*) AS n FROM market_events_trade_features_s55").fetchone()["n"]
            self.assertEqual(n, 3)
            decisions = {
                r[0]
                for r in conn.execute("SELECT DISTINCT gate_decision FROM market_events_trade_features_s55").fetchall()
            }
            self.assertTrue(decisions & {s55.GATE_COLD_START, s55.GATE_ALLOWED, "cold_start", "open"})


class TestS553PortfolioAndZombie(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "pm.db")
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_timeout_without_price_closes(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, status,
                  mfe_pct, mae_pct, capital_usd, leverage, updated_at
                ) VALUES ('g3', 1, 'ZZZNOFEED', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                """,
                (now - s42.TIMEOUT_SECONDS - 10, now),
            )
            conn.commit()
            with patch.object(s42, "_current_price", return_value=None):
                s42.tick_open_paper_trades_s42(conn)
                conn.commit()
            row = conn.execute("SELECT status, exit_reason FROM market_events_paper_trades_s42").fetchone()
            self.assertEqual(row["status"], "CLOSED")
            self.assertEqual(row["exit_reason"], "TIMEOUT")

    def test_hard_cap_enforced(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      mfe_pct, mae_pct, capital_usd, leverage, updated_at
                    ) VALUES ('legacy', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                    """,
                    (i, now - i * 100, now),
                )
            conn.commit()
            with patch.object(s42, "_current_price", return_value=100.0):
                closed = enforce_max_open_hard_cap(conn, max_open=2, now=now)
                conn.commit()
            self.assertEqual(closed, 3)
            open_n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status='OPEN'",
            ).fetchone()["n"]
            self.assertEqual(open_n, 2)

    def test_stale_sweep(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, status,
                  mfe_pct, mae_pct, capital_usd, leverage, updated_at
                ) VALUES ('legacy', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                """,
                (now - s42.TIMEOUT_SECONDS - 5, now),
            )
            conn.commit()
            with patch.object(s42, "_current_price", return_value=None):
                out = sweep_stale_opens(conn, now=now)
                conn.commit()
            self.assertEqual(out["stale_closed"], 1)
            row = conn.execute("SELECT exit_reason FROM market_events_paper_trades_s42").fetchone()
            self.assertEqual(row["exit_reason"], EXIT_STALE)


class TestS553MetricsAndAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "met.db")
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            for i, pnl in enumerate([10.0, -5.0, 20.0, -8.0]):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                      mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason,
                      status, capital_usd, leverage, updated_at
                    ) VALUES ('sig', ?, 'BTC', 'LONG', 1, 0.9, 1.1, 1.2, ?, ?, 60,
                      0, 0, ?, ?, ?, 'TP1', 'CLOSED', 100, 20, ?)
                    """,
                    (i, now - 100, now - 10, pnl, pnl, "WIN" if pnl > 0 else "LOSS", now),
                )
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      mfe_pct, mae_pct, capital_usd, leverage, updated_at
                    ) VALUES ('open', ?, 'ETH', 'SHORT', 1, 1.1, 0.9, 0.8, ?, 'OPEN', 0, 0, 100, 20, ?)
                    """,
                    (100 + i, now - 7200, now),
                )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_advanced_metrics_in_report(self) -> None:
        with market_events_connection() as conn:
            m = s42.compute_advanced_metrics_s42(conn)
            text = s42.format_paper_performance_s42(conn)
        self.assertGreater(m["n"], 0)
        self.assertIn("Expectancy", text)
        self.assertIn("Profit Factor", text)
        self.assertIn("Sharpe (paper)", text)
        self.assertIn("S55 Gate", text)
        self.assertIn("Allowed=", text)

    def test_open_audit_and_regression_header(self) -> None:
        with market_events_connection() as conn:
            opens = audit_open_trades(conn)
            reg = detect_regression(conn)
            text = format_trade_regression_audit(conn, top_n=10)
        self.assertEqual(opens["total_open"], 3)
        self.assertEqual(opens["short"], 3)
        self.assertIn("previous_sha", reg)
        self.assertIn("Regression detected", text)
        self.assertIn("Open book audit", text)


class TestS553OpenCapNeverExceeded(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "cap.db")
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_open_never_exceeds_max(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            for i in range(10):
                conn.execute(
                    """
                    INSERT INTO market_events_signal_learning_s40_signals (
                      signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
                      timestamp, snapshot_decision_confidence, created_at, updated_at
                    ) VALUES ('g3', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 7, ?, ?)
                    """,
                    (i + 1, now, now, now),
                )
            conn.commit()
            with patch.object(s55, "S55_ENABLED", True), patch.object(s55, "S55_MAX_OPEN_TRADES", 3), patch.object(
                s55, "S55_MIN_SIMILAR", 20,
            ), patch(
                "bot.research.market_events.signal_intelligence.portfolio_manager_s55.S55_PORTFOLIO_REPLACE_ENABLED",
                False,
            ):
                s42.open_paper_trades_from_s40(conn, limit=20)
                conn.commit()
            open_n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status='OPEN'",
            ).fetchone()["n"]
            self.assertLessEqual(open_n, 3)


if __name__ == "__main__":
    unittest.main()
