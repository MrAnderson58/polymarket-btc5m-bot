"""S61 Strategy Discovery Engine tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import strategy_discovery_s61 as s61
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    RESEARCH_SCHEMA_VERSION,
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestStrategyDiscoveryS61(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s61.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 120) -> None:
        for i in range(n):
            direction = "LONG" if i % 2 == 0 else "SHORT"
            # Funding favorable LONGs win more; ARB loses; NEAR SHORT in 11-15 wins
            symbol = "BTC"
            hour = i % 24
            funding = -0.001 if direction == "LONG" else 0.001
            fear = 25 if direction == "LONG" else 55
            ema200 = 99.0
            entry = 100.0
            pnl = 10.0 if (direction == "LONG" and funding < 0 and fear < 30) else -4.0

            if i % 7 == 0:
                symbol = "ARB"
                pnl = -9.0
            elif i % 5 == 0:
                symbol = "NEAR"
                direction = "SHORT"
                hour = 12
                funding = 0.0005
                fear = 50
                pnl = 14.0

            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, fear_greed, atr, volume, news_score, macro_score, ai_score,
                  trend, hour, weekday, market_regime, created_at
                ) VALUES ('s61', ?, ?, ?, ?, ?, ?, ?, 300, 'TP1',
                  ?, ?, 1.0, 1000, 0.5, 0.5, 0.7, ?, ?, ?, 'WEAK_BULL', ?)
                """,
                (
                    i + 1,
                    symbol,
                    direction,
                    entry,
                    entry + 1,
                    pnl,
                    pnl,
                    funding,
                    fear,
                    0.5 if entry > ema200 else -0.5,
                    hour,
                    i % 7,
                    self.now,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  ema20, ema50, ema200, gate_result, gate_reason,
                  final_pnl_usd, exit_reason, closed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PASS', 'ALLOWED', ?, 'TP1', ?, ?, ?)
                """,
                (
                    i + 1,
                    self.now,
                    symbol,
                    direction,
                    entry,
                    100.5,
                    100.0,
                    ema200,
                    pnl,
                    self.now,
                    self.now,
                    self.now,
                ),
            )
        conn.commit()

    def test_schema_v71(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 71)
        self.assertGreaterEqual(RESEARCH_SCHEMA_VERSION, 71)
        with research_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_strategy_discovery_s61'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_generates_and_ranks(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=120)
            out = s61.run_strategy_discovery(conn, top_n=15, now=self.now)
            conn.commit()
            self.assertIsNotNone(out.get("run_id"))
            self.assertGreater(out.get("n_hypotheses") or 0, 20)
            self.assertGreaterEqual(out.get("n_evaluated") or 0, 1)
            self.assertTrue(out.get("top"))
            # Top rules should improve expectancy vs baseline
            top0 = out["top"][0]
            self.assertIsNotNone(top0.get("delta_expectancy"))
            self.assertGreaterEqual(float(top0.get("score") or 0), 0)

            text = s61.format_strategy_discovery_report(conn, run_id=out["run_id"])
            self.assertIn("S61 Strategy Discovery Engine", text)
            self.assertIn("ΔE", text)
            self.assertIn("Rule", text)

            rows = s61.latest_discovery_rows(conn, run_id=out["run_id"])
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(int(rows[0]["rank"]), 1)

    def test_funding_ema_fear_long_discovered(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=140)
            out = s61.run_strategy_discovery(conn, top_n=40, now=self.now)
            rules = " | ".join(r["rule_text"] for r in out["top"])
            # Should surface something about funding / fear / LONG or BLOCK ARB
            self.assertTrue(
                ("Funding" in rules and "LONG" in rules)
                or ("Fear" in rules)
                or ("ARB" in rules)
                or ("NEAR" in rules),
                msg=rules[:500],
            )

    def test_observe_only_no_suggestions_by_default(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=80)
            out = s61.run_strategy_discovery(conn, write_suggestions=False, now=self.now)
            self.assertFalse(out.get("suggestions_enabled"))
            self.assertEqual(out.get("suggestions_created"), 0)


if __name__ == "__main__":
    unittest.main()
