"""Tests for bidirectional live shadow audit (read-only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.bidirectional_live_audit import (
    PROMOTION_CRITERIA,
    LiveTrade,
    audit_integrity,
    compute_metrics,
    evaluate_promotion,
    load_closed_trades,
    render_report,
    run_live_audit,
    stress_test,
    _max_consecutive_losses,
    _pf,
)


def _seed_shadow_data(conn, n: int = 10) -> None:
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
    """)
    base_ts = 1_700_000_000
    for i in range(n):
        slug = f"btc-updown-5m-{1000 + i}"
        ws = base_ts + i * 300
        entry_ts = ws + 60
        side = "YES" if i % 2 == 0 else "NO"
        entry = 0.38 if side == "YES" else 0.40
        exit_p = entry * 1.08
        pnl = ((exit_p - entry) / entry) * 100
        regime = "MOMENTUM" if i % 3 else "NORMAL"
        conn.execute(
            """
            INSERT INTO bidirectional_shadow_observations (
                market_slug, timestamp, decision, confidence, regime, btc_move_30s, entry_price
            ) VALUES (?, ?, ?, 0.65, ?, 25.0, ?)
            """,
            (slug, entry_ts, side, regime, entry),
        )
        conn.execute(
            """
            INSERT INTO bidirectional_shadow_trades (
                market_slug, window_start_ts, side, status, entry_price, entry_ts,
                entry_regime, entry_confidence, max_price_seen, exit_price, exit_reason,
                pnl_pct, holding_time_seconds
            ) VALUES (?, ?, ?, 'closed', ?, ?, ?, 0.65, ?, ?, 'TRAILING_STOP', ?, 45)
            """,
            (slug, ws, side, entry, entry_ts, regime, exit_p, exit_p, pnl),
        )
    conn.commit()


class MetricsTestCase(unittest.TestCase):
    def test_pf_and_max_cl(self) -> None:
        self.assertAlmostEqual(_pf([10, -5, 10, -5]), 2.0)
        self.assertEqual(_max_consecutive_losses([5, -1, -2, 3, -1]), 2)

    def test_compute_metrics(self) -> None:
        trades = [
            LiveTrade(
                1, "m1", 1000, "YES", 0.40, 1100, "NORMAL", 0.6,
                0.44, "TRAILING_STOP", 10.0, 30, 0.44, 20.0, 240,
            ),
            LiveTrade(
                2, "m2", 1300, "NO", 0.40, 1400, "MOMENTUM", 0.7,
                0.36, "STOP_LOSS", -10.0, 20, 0.40, -15.0, 200,
            ),
        ]
        m = compute_metrics(trades)
        self.assertEqual(m["trades"], 2)
        self.assertEqual(m["wr"], 50.0)
        self.assertAlmostEqual(m["pf"], 1.0)


class IntegrityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_one_trade_per_market_passes(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 5)
            trades = load_closed_trades(conn)
            result = audit_integrity(conn, trades)
        self.assertTrue(result["one_trade_per_market"])
        self.assertEqual(result["no_avoid_zone_entries"], 0)

    def test_duplicate_market_detected(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 2)
            conn.execute(
                """
                INSERT INTO bidirectional_shadow_trades (
                    market_slug, window_start_ts, side, status, entry_price, entry_ts,
                    entry_regime, exit_price, exit_reason, pnl_pct, holding_time_seconds
                ) VALUES ('btc-updown-5m-1000', 1000, 'YES', 'closed', 0.38, 1060,
                          'NORMAL', 0.42, 'TIME_STOP', 10.5, 30)
                """
            )
            conn.commit()
            trades = load_closed_trades(conn)
            result = audit_integrity(conn, trades)
        types = [v.violation_type for v in result["violations"]]
        self.assertIn("DUPLICATE_MARKET", types)

    def test_no_avoid_zone_violation(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 1)
            conn.execute(
                """
                UPDATE bidirectional_shadow_trades
                SET side='NO', entry_price=0.30
                WHERE id=1
                """
            )
            conn.commit()
            trades = load_closed_trades(conn)
            result = audit_integrity(conn, trades)
        types = [v.violation_type for v in result["violations"]]
        self.assertIn("NO_AVOID_ZONE", types)

    def test_chop_regime_entry_violation(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 1)
            conn.execute(
                "UPDATE bidirectional_shadow_trades SET entry_regime='CHOP' WHERE id=1"
            )
            conn.commit()
            trades = load_closed_trades(conn)
            result = audit_integrity(conn, trades)
        types = [v.violation_type for v in result["violations"]]
        self.assertIn("SKIP_REGIME_ENTRY", types)


class AuditIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_live_audit_readonly(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 20)
            before_trades = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_trades"
            ).fetchone()[0]
            report = run_live_audit(conn)
            after_trades = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_trades"
            ).fetchone()[0]
            text = render_report(report)
        self.assertEqual(before_trades, after_trades)
        self.assertEqual(report["trade_count"], 20)
        self.assertIn("LIVE SHADOW AUDIT", text)
        self.assertIn("PROMOTION GATE", text)

    def test_stress_scenarios(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 5)
            trades = load_closed_trades(conn)
            stress = stress_test(conn, trades)
        self.assertIn("A_current", stress["scenarios"])
        self.assertIn("B_entry+0.01_exit-0.01", stress["scenarios"])
        self.assertLess(
            stress["scenarios"]["B_entry+0.01_exit-0.01"]["avg_pnl"],
            stress["scenarios"]["A_current"]["avg_pnl"],
        )

    def test_promotion_continue_shadow_small_sample(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 20)
            report = run_live_audit(conn)
            promo = evaluate_promotion(report)
        self.assertEqual(promo["verdict"], "CONTINUE_SHADOW")
        self.assertFalse(promo["checks"]["closed_ge_250"])


class RenderReportRegressionTestCase(unittest.TestCase):
    """Regression: render_report must not crash when rolling windows shadow width var."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_render_report_sections_8_through_11_with_rolling_windows(self) -> None:
        with connect(self.db_path) as conn:
            _seed_shadow_data(conn, 55)
            report = run_live_audit(conn)
        self.assertTrue(report["temporal"]["quarters"])
        self.assertTrue(report["temporal"]["rolling_50"])
        self.assertTrue(report["replay_vs_live"]["differences"] is not None)
        self.assertIn("A_current", report["stress"]["scenarios"])

        text = render_report(report)

        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())
        self.assertIn("8. TEMPORAL STABILITY", text)
        self.assertIn("9. REPLAY VS LIVE SHADOW", text)
        self.assertIn("10. COST / STRESS TEST", text)
        self.assertIn("11. PROMOTION GATE", text)
        self.assertIn("Rolling windows (50):", text)


if __name__ == "__main__":
    unittest.main()
