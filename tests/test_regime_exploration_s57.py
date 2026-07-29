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
    """Insert closed S55 features that make RANGE/LONG look unprofitable."""
    now = int(time.time())
    for i in range(n):
        pnl = -50.0 if i % 3 != 0 else 20.0  # ~33% win, negative EV
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55
                (s40_signal_type, s40_signal_id, symbol, direction,
                 gate_decision, market_regime, pnl_usd, result, created_at)
            VALUES (?, ?, 'BTCUSDT', 'LONG',
                    'ALLOWED', 'RANGE', ?, 'CLOSED', ?)
            """,
            (f"learning", i + 1, pnl, now - 3600 * i),
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

    def test_open_paper_trade_with_exploration(self) -> None:
        """End-to-end: synthetic candidate → S42 INSERT + S55 row."""
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
             patch.object(s55, "S55_ENABLED", False):
            opened = open_paper_trades_from_s40(conn)

        self.assertGreaterEqual(opened, 1, "At least one paper trade should open via exploration")

        # Verify S42 row exists
        s42_count = conn.execute(
            "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE s40_signal_id = 9002"
        ).fetchone()[0]
        self.assertEqual(s42_count, 1, "S42 paper trade row must exist")

        # Verify S55 features row with ALLOWED-like decision
        s55_row = conn.execute(
            """SELECT gate_decision FROM market_events_trade_features_s55
               WHERE s40_signal_id = 9002 AND gate_decision NOT IN ('REGIME_BLOCK', 'INSUFFICIENT_HISTORY')
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        self.assertIsNotNone(s55_row, "S55 features row must exist with non-blocked decision")

    def test_deadlock_scenario_before_fix(self) -> None:
        """Simulate the exact production scenario: all RANGE/LONG, negative EV."""
        conn = _mem_conn()
        _seed_negative_regime_stats(conn, n=45)

        import bot.research.market_events.signal_intelligence.market_regime_s57 as s57

        # Without exploration: permanent block
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

        # With default 10% exploration: some pass
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
        # With 10% rate over 1000 trials, expect ~100 ± ~30
        self.assertGreater(passed, 30, "Exploration should let some through")
        self.assertLess(passed, 250, "Exploration should not let too many through")


if __name__ == "__main__":
    unittest.main()
