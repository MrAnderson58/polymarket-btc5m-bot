"""Regression test: S57 exploration rate prevents permanent REGIME_BLOCK deadlock.

Root cause: when all historical trades for a regime×direction have negative
expectancy AND PF<1, apply_regime_gate blocks 100% of candidates.  No new
trades open, so stats never refresh → permanent deadlock.

Fix: ε-greedy exploration (S57_EXPLORATION_RATE) lets a fraction through with
gate_decision=REGIME_EXPLORE.
"""

from __future__ import annotations

import sqlite3
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import apply_migrations


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


def _seed_negative_regime_stats(conn: sqlite3.Connection, *, n: int = 40) -> None:
    """Insert closed S55 features that make RANGE/LONG look unprofitable.

    Sets closed_at + pnl_pct so S55 find_similar_trades also sees negative EV
    (the live path after a19e0db).
    """
    now = int(time.time())
    for i in range(n):
        win = i % 3 == 0
        pnl_usd = 20.0 if win else -50.0
        pnl_pct = 1.0 if win else -2.5
        result = "WIN" if win else "LOSS"
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55
                (s40_signal_type, s40_signal_id, symbol, direction,
                 gate_decision, market_regime, pnl_usd, pnl_pct, result,
                 created_at, closed_at)
            VALUES (?, ?, 'BTCUSDT', 'LONG',
                    'ALLOWED', 'RANGE', ?, ?, ?, ?, ?)
            """,
            (f"learning", i + 1, pnl_usd, pnl_pct, result, now - 3600 * i, now - 3600 * i),
        )
    conn.commit()


def _insert_s40_signal(conn: sqlite3.Connection, signal_id: int = 1) -> None:
    """Insert a synthetic S40 learning signal (candidate)."""
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_signal_learning_s40_signals
            (signal_type, signal_id, symbol, direction, entry, stop, tp1,
             timestamp, created_at, updated_at,
             snapshot_decision_confidence, snapshot_funding,
             snapshot_open_interest, snapshot_volume, snapshot_atr,
             snapshot_fear_greed, snapshot_trend, snapshot_news_score)
        VALUES (?, ?, 'BTCUSDT', 'LONG', 100000.0, 99000.0, 101000.0,
                ?, ?, ?, 0.7, 0.001, 5000000, 1000000, 500.0, 50, 10.0, 0.5)
        """,
        ("learning", signal_id, now, now, now),
    )
    conn.commit()


class TestRegimeExplorationS57(unittest.TestCase):
    """Verify ε-greedy exploration breaks REGIME_BLOCK deadlock."""

    def test_all_blocked_without_exploration(self) -> None:
        """With exploration=0, negative regime blocks everything."""
        conn = _mem_conn()
        _seed_negative_regime_stats(conn)
        _insert_s40_signal(conn, signal_id=9001)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57

        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 10), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 0.0):
            ok, decision, meta = s57.apply_regime_gate(
                conn, {"market_regime": "RANGE", "direction": "LONG"},
            )
        self.assertFalse(ok)
        self.assertEqual(decision, "REGIME_BLOCK")

    def test_exploration_passes_blocked_candidate(self) -> None:
        """With exploration=1.0 (always explore), blocked candidate passes."""
        conn = _mem_conn()
        _seed_negative_regime_stats(conn)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57

        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 10), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 1.0):
            ok, decision, meta = s57.apply_regime_gate(
                conn, {"market_regime": "RANGE", "direction": "LONG"},
            )
        self.assertTrue(ok)
        self.assertEqual(decision, "REGIME_EXPLORE")
        self.assertTrue(meta.get("exploration"))

    def test_explore_then_negative_expectancy_still_opens(self) -> None:
        """Live regression after a19e0db.

        Call sequence that failed in production:
          apply_regime_gate → REGIME_EXPLORE (allow)
          find_similar_trades → expected_pnl < 0
          should_open_trade → NEGATIVE_EXPECTANCY (deny)  ← bug
          open_paper_trades_from_s40 → no S42 INSERT

        Fix: REGIME_EXPLORE must bypass NEGATIVE_EXPECTANCY and open the trade.
        """
        conn = _mem_conn()
        _seed_negative_regime_stats(conn, n=45)
        _insert_s40_signal(conn, signal_id=9100)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57
        import bot.research.market_events.signal_intelligence.trade_intelligence_s55 as s55
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            open_paper_trades_from_s40,
        )

        features = {
            "market_regime": "RANGE",
            "direction": "LONG",
            "symbol": "BTCUSDT",
            "volatility": 500.0,
            "atr": 500.0,
            "funding": 0.001,
            "trend": 10.0,
            "fear_greed": 50.0,
            "volume": 1000000.0,
        }

        # Prove the intermediate call stack: explore passes, neighbors are negative.
        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 30), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 1.0), \
             patch.object(s55, "S55_ENABLED", True), \
             patch.object(s55, "S55_MIN_SIMILAR", 20), \
             patch.object(s55, "S55_MIN_EXPECTED_PNL_PCT", 0.0):
            ok_reg, reg_reason, _ = s57.apply_regime_gate(conn, dict(features))
            self.assertTrue(ok_reg)
            self.assertEqual(reg_reason, "REGIME_EXPLORE")

            neighbors = s55.find_similar_trades(conn, features)
            self.assertGreaterEqual(len(neighbors), 20)
            est = s55.estimate_from_neighbors(neighbors)
            self.assertLess(float(est["expected_pnl_pct"]), 0.0)

            allow, decision, estimate = s55.should_open_trade(
                conn, features=dict(features), open_count=0, skip_max_open_check=True,
            )
            self.assertTrue(allow, "exploration must not die at NEGATIVE_EXPECTANCY")
            self.assertEqual(decision, "REGIME_EXPLORE")
            self.assertEqual(estimate.get("regime_gate"), "REGIME_EXPLORE")

            opened = open_paper_trades_from_s40(conn)

        self.assertGreaterEqual(opened, 1)
        s42 = conn.execute(
            "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE s40_signal_id = 9100"
        ).fetchone()[0]
        self.assertEqual(s42, 1)
        s55_row = conn.execute(
            """SELECT gate_decision, paper_trade_id FROM market_events_trade_features_s55
               WHERE s40_signal_id = 9100 ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        self.assertIsNotNone(s55_row)
        self.assertEqual(s55_row[0], "REGIME_EXPLORE")
        self.assertIsNotNone(s55_row[1], "S55 row must link to opened S42 trade")

    def test_open_paper_trade_with_exploration(self) -> None:
        """End-to-end with S55 gate ENABLED (live config)."""
        conn = _mem_conn()
        _seed_negative_regime_stats(conn)
        _insert_s40_signal(conn, signal_id=9002)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            open_paper_trades_from_s40,
        )
        import bot.research.market_events.signal_intelligence.trade_intelligence_s55 as s55

        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 10), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 1.0), \
             patch.object(s55, "S55_ENABLED", True), \
             patch.object(s55, "S55_MIN_SIMILAR", 20), \
             patch.object(s55, "S55_MIN_EXPECTED_PNL_PCT", 0.0):
            opened = open_paper_trades_from_s40(conn)

        self.assertGreaterEqual(opened, 1, "At least one paper trade should open via exploration")

        s42_count = conn.execute(
            "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE s40_signal_id = 9002"
        ).fetchone()[0]
        self.assertEqual(s42_count, 1, "S42 paper trade row must exist")

        s55_row = conn.execute(
            """SELECT gate_decision FROM market_events_trade_features_s55
               WHERE s40_signal_id = 9002 ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        self.assertIsNotNone(s55_row)
        self.assertEqual(s55_row[0], "REGIME_EXPLORE")

    def test_deadlock_scenario_before_fix(self) -> None:
        """Simulate the exact production scenario: all RANGE/LONG, negative EV."""
        conn = _mem_conn()
        _seed_negative_regime_stats(conn, n=45)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57

        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 30), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 0.0):
            blocked = 0
            for _ in range(100):
                ok, decision, _ = s57.apply_regime_gate(
                    conn, {"market_regime": "RANGE", "direction": "LONG"},
                )
                if not ok:
                    blocked += 1
        self.assertEqual(blocked, 100, "Without exploration, all 100 must be blocked")

        with patch.object(s57, "S57_ENABLED", True), \
             patch.object(s57, "S57_FILTER_ENABLED", True), \
             patch.object(s57, "S57_MIN_EVIDENCE", 30), \
             patch.object(s57, "S57_MIN_EXPECTANCY", 0.0), \
             patch.object(s57, "S57_EXPLORATION_RATE", 0.10):
            passed = 0
            for _ in range(1000):
                ok, decision, _ = s57.apply_regime_gate(
                    conn, {"market_regime": "RANGE", "direction": "LONG"},
                )
                if ok:
                    passed += 1
        self.assertGreater(passed, 30, "Exploration should let some through")
        self.assertLess(passed, 250, "Exploration should not let too many through")


if __name__ == "__main__":
    unittest.main()
