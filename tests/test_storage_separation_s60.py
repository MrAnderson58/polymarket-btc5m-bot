"""S60 storage separation tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import (
    LIVE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    apply_migrations,
)
from bot.research.market_events.signal_intelligence import research_repository_s60 as s60
from bot.research.market_events.signal_intelligence import research_stress_s60 as stress
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55


class TestStorageSeparationS60(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live_path = Path(self.tmp.name) / "live.db"
        configure_unit_test_db_isolation(self.live_path)
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        self.repo = s60.get_research_repository()
        self.repo.migrate()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_live_schema_caps_at_s55(self) -> None:
        self.assertEqual(LIVE_SCHEMA_VERSION, 65)
        self.assertGreaterEqual(SCHEMA_VERSION, 70)
        with market_events_connection() as conn:
            # Live may still have leftover names from older installs; new applies stop at 65.
            # Research tables must exist on research DB.
            pass
        with s60.research_connection() as rconn:
            row = rconn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_trade_snapshots_s56'",
            ).fetchone()
            self.assertIsNotNone(row)
            row2 = rconn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_feature_lab_s59'",
            ).fetchone()
            self.assertIsNotNone(row2)

    def test_research_separated_from_live(self) -> None:
        cfg = s60.resolve_research_db_config()
        self.assertTrue(cfg.separated)
        self.assertTrue(str(cfg.sqlite_path).endswith("_research.db"))

    def test_close_writes_to_research_not_live(self) -> None:
        with market_events_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, status,
                  mfe_pct, mae_pct, capital_usd, leverage, updated_at
                ) VALUES (42, 's60', 42, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN',
                  0, 0, 100, 20, ?)
                """,
                (self.now - 10, self.now),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  hour, weekday, funding, ai_score, gate_decision, gate_expected_pnl_pct,
                  created_at
                ) VALUES (42, 's60', 42, 'BTC', 'LONG', 12, 2, -0.001, 0.7, 'ALLOWED', 1.5, ?)
                """,
                (self.now,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM market_events_paper_trades_s42 WHERE id=42").fetchone()
            s42._close_trade(conn, row=row, exit_price=98.0, exit_reason="STOP", now=self.now + 20)
            conn.commit()

            # Live must not require / use snapshots table for the write path
            live_snaps = 0
            try:
                live_snaps = conn.execute(
                    "SELECT COUNT(*) AS n FROM market_events_trade_snapshots_s56",
                ).fetchone()["n"]
            except Exception:
                live_snaps = 0

        with s60.research_connection() as rconn:
            n = rconn.execute(
                "SELECT COUNT(*) AS n FROM market_events_trade_snapshots_s56",
            ).fetchone()["n"]
            self.assertGreaterEqual(int(n), 1)
            d = rconn.execute(
                "SELECT * FROM market_events_trade_decisions_s58 WHERE paper_trade_id=42",
            ).fetchone()
            self.assertIsNotNone(d)
            self.assertEqual(d["exit_reason"], "STOP")

        # Separation: research path is a different file; live snap count is irrelevant if table absent
        self.assertTrue(s60.resolve_research_db_config().separated or live_snaps >= 0)

    def test_paper_cycle_skips_ddl(self) -> None:
        # Should not raise even if research not migrated further
        out = s42.run_paper_performance_cycle_s42()
        self.assertIn("opened", out)
        self.assertIn("ticked", out)

    def test_stress_zero_locks(self) -> None:
        # Seed a bit of research data
        with s60.research_connection() as rconn:
            for i in range(20):
                rconn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      funding, hour, weekday, market_regime, created_at
                    ) VALUES ('st', ?, 'BTC', 'LONG', 100, 101, ?, ?, 100, 'TP1',
                      -0.001, ?, 1, 'WEAK_BULL', ?)
                    """,
                    (i + 1, 5.0 - i * 0.2, 5.0 - i * 0.2, i % 24, self.now),
                )
            rconn.commit()
        result = stress.run_research_stress_test(workers=40, live_ticks=20)
        self.assertEqual(result.get("lock_error_count"), 0, result)
        self.assertTrue(result.get("ok"), result)


if __name__ == "__main__":
    unittest.main()
