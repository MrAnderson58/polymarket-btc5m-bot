"""S62 Alpha Discovery Engine tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import alpha_discovery_s62 as s62
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    RESEARCH_SCHEMA_VERSION,
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestAlphaDiscoveryS62(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s62.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 200) -> None:
        """Seed a stable LONG+Funding<0 edge across time windows."""
        for i in range(n):
            # Spread timestamps across ~100 days so 30/60/90 windows are populated
            ts = self.now - (i % 100) * 86400 - (i % 17) * 3600
            direction = "LONG" if i % 3 != 0 else "SHORT"
            funding = -0.001 if direction == "LONG" else 0.0005
            fear = 22 if direction == "LONG" else 55
            hour = 15 if direction == "LONG" else (3 if i % 11 == 0 else 12)
            symbol = "BTC"
            pnl = 12.0 if (direction == "LONG" and funding < 0) else -5.0
            if i % 9 == 0:
                symbol = "ARB"
                direction = "SHORT"
                hour = 2
                pnl = -11.0
            elif i % 8 == 0:
                symbol = "NEAR"
                direction = "LONG"
                fear = 20
                pnl = 9.0 if funding < 0 else -4.0

            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, fear_greed, atr, volume, news_score, macro_score, ai_score,
                  trend, hour, weekday, market_regime, created_at, timestamp
                ) VALUES (?, 's62', ?, ?, ?, 100, 101, ?, ?, ?, 'TP1',
                  ?, ?, 1.0, 1000, 0.5, 0.5, 0.7, ?, ?, ?, 'RANGE', ?, ?)
                """,
                (
                    i + 1,
                    i + 1,
                    symbol,
                    direction,
                    pnl,
                    pnl,
                    400 + (i % 50) * 10,
                    funding,
                    fear,
                    0.4 if direction == "LONG" else -0.3,
                    hour,
                    i % 7,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  ema20, ema50, ema200, rsi, gate_result, gate_reason,
                  final_pnl_usd, exit_reason, closed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, 100.5, 100.0, 99.0, ?, 'PASS', 'ALLOWED',
                  ?, 'TP1', ?, ?, ?)
                """,
                (
                    i + 1,
                    ts - 500,
                    symbol,
                    direction,
                    28.0 if fear < 25 else 55.0,
                    pnl,
                    ts,
                    ts,
                    ts,
                ),
            )
        conn.commit()

    def test_schema_v72(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 72)
        self.assertGreaterEqual(RESEARCH_SCHEMA_VERSION, 72)
        with research_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_alpha_discovery_s62'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_metrics_include_dd_and_hold(self) -> None:
        rows = [
            {"pnl_usd": 10, "mae_pct": -1.5, "duration_sec": 300},
            {"pnl_usd": -5, "mae_pct": -2.0, "duration_sec": 600},
        ]
        m = s62.pattern_metrics(rows)
        self.assertEqual(m["n"], 2)
        self.assertIsNotNone(m["avg_dd"])
        self.assertIsNotNone(m["avg_hold_sec"])

    def test_run_discovers_with_gates(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=220)
            out = s62.run_alpha_discovery(
                conn, top_n=15, now=self.now, min_n=20, write_suggestions=False,
            )
            conn.commit()
            self.assertIsNotNone(out.get("run_id"))
            self.assertGreater(out.get("n_patterns") or 0, 20)
            self.assertEqual(out.get("suggestions_created"), 0)
            text = s62.format_alpha_discovery_report(conn, run_id=out["run_id"])
            self.assertIn("S62 Alpha Discovery Engine", text)
            self.assertIn("Human approval", text)
            # If anything passed gates, must have confidence letter
            for item in out.get("top") or []:
                self.assertIn(item.get("confidence"), ("A", "B", "C"))
                self.assertIn("stability", item)
                self.assertIn("walk_forward", item)

    def test_observe_only(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=120)
            out = s62.run_alpha_discovery(conn, min_n=15, write_suggestions=False, now=self.now)
            self.assertFalse(out.get("suggestions_enabled"))


if __name__ == "__main__":
    unittest.main()
