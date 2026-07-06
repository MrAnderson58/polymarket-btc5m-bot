"""Tests for Bidirectional V1.3 execution-aware research."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.strategy.bidirectional_shadow import ensure_tables
from bot.research.bidirectional_live_audit import LiveTrade
from bot.research.bidirectional_v13_execution.entry_models import (
    ENTRY_MODELS,
    model_delayed_taker,
    model_immediate_taker,
    model_passive_limit,
)
from bot.research.bidirectional_v13_execution.engine import run_v13_execution_research
from bot.research.bidirectional_v13_execution.exit_models import apply_exit_model
from bot.research.bidirectional_v13_execution.quote_path import reconstruct_quote_path
from bot.research.mtf.clean_eligibility import audit_bid_gt_ask_timeline, row_is_clean_15m
from bot.research.execution_failure_audit import audit_execution_failures, _classify_error


def _seed_v13(conn: sqlite3.Connection) -> None:
    ws = 1_700_000_000
    entry_ts = ws + 60
    slug = "btc-updown-5m-test"
    conn.execute(
        """
        INSERT INTO bidirectional_shadow_trades (
            market_slug, window_start_ts, side, status, entry_price, entry_ts,
            entry_regime, exit_price, exit_reason, pnl_pct, holding_time_seconds
        ) VALUES (?, ?, 'YES', 'closed', 0.40, ?, 'NORMAL', 0.44, 'TRAIL', 10.0, 30)
        """,
        (slug, ws, entry_ts),
    )
    conn.execute(
        """
        INSERT INTO bidirectional_shadow_observations (
            market_slug, timestamp, decision, btc_move_30s, entry_price, regime
        ) VALUES (?, ?, 'YES', 20.0, 0.40, 'NORMAL')
        """,
        (slug, entry_ts),
    )
    quotes = [
        (entry_ts - 3, 0.39, 0.40, 60000),
        (entry_ts, 0.39, 0.40, 60010),
        (entry_ts + 1, 0.39, 0.42, 60015),
        (entry_ts + 2, 0.39, 0.40, 60020),
        (entry_ts + 30, 0.43, 0.44, 60050),
    ]
    for ts, bid, ask, btc in quotes:
        sfs = ts - ws
        conn.execute(
            """
            INSERT INTO v4_shadow_observations (
                market_slug, window_start_ts, timestamp, seconds_from_start,
                seconds_left, btc_price, yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (?, ?, ?, ?, 240, ?, ?, ?, 0.56, 0.60)
            """,
            (slug, ws, ts, sfs, btc, bid, ask),
        )
    conn.commit()


class V13QuotePathTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        with connect(self.db_path) as conn:
            ensure_tables(conn)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_no_future_quote_for_signal(self) -> None:
        with connect(self.db_path) as conn:
            _seed_v13(conn)
            trade = LiveTrade(
                id=1, market_slug="btc-updown-5m-test", window_start_ts=1_700_000_000,
                side="YES", entry_price=0.40, entry_ts=1_700_000_060,
                entry_regime="NORMAL", entry_confidence=None,
                exit_price=0.44, exit_reason="TRAIL", pnl_pct=10.0,
                holding_time_seconds=30.0, max_price_seen=None, btc_move_30s=20.0,
            )
            ctx = reconstruct_quote_path(conn, trade)
        self.assertEqual(ctx.signal_ask, 0.40)
        self.assertEqual(ctx.ask_at_offset[1], 0.42)

    def test_delayed_fill_uses_future_observation(self) -> None:
        with connect(self.db_path) as conn:
            _seed_v13(conn)
            trade = LiveTrade(
                id=1, market_slug="btc-updown-5m-test", window_start_ts=1_700_000_000,
                side="YES", entry_price=0.40, entry_ts=1_700_000_060,
                entry_regime="NORMAL", entry_confidence=None,
                exit_price=0.44, exit_reason="TRAIL", pnl_pct=10.0,
                holding_time_seconds=30.0, max_price_seen=None, btc_move_30s=20.0,
            )
            ctx = reconstruct_quote_path(conn, trade)
            fill = model_delayed_taker(ctx, trade, 2)
        self.assertTrue(fill.filled)
        self.assertEqual(fill.entry_price, 0.40)

    def test_passive_fill_only_when_ask_reaches_limit(self) -> None:
        with connect(self.db_path) as conn:
            _seed_v13(conn)
            trade = LiveTrade(
                id=1, market_slug="btc-updown-5m-test", window_start_ts=1_700_000_000,
                side="YES", entry_price=0.40, entry_ts=1_700_000_060,
                entry_regime="NORMAL", entry_confidence=None,
                exit_price=0.44, exit_reason="TRAIL", pnl_pct=10.0,
                holding_time_seconds=30.0, max_price_seen=None, btc_move_30s=20.0,
            )
            ctx = reconstruct_quote_path(conn, trade)
            ok = model_passive_limit(ctx, trade, 5)
            no_fill = model_passive_limit(
                type(ctx)(
                    **{**ctx.__dict__, "signal_ask": 0.38, "observations": ctx.observations}
                ),
                trade,
                3,
            )
        self.assertTrue(ok.filled)
        self.assertFalse(no_fill.filled)

    def test_exit_uses_observed_bid_not_future_invention(self) -> None:
        with connect(self.db_path) as conn:
            _seed_v13(conn)
            trade = LiveTrade(
                id=1, market_slug="btc-updown-5m-test", window_start_ts=1_700_000_000,
                side="YES", entry_price=0.40, entry_ts=1_700_000_060,
                entry_regime="NORMAL", entry_confidence=None,
                exit_price=0.44, exit_reason="TRAIL", pnl_pct=10.0,
                holding_time_seconds=30.0, max_price_seen=None, btc_move_30s=20.0,
            )
            result = apply_exit_model(conn, trade, 0.40, "immediate_bid")
        self.assertIsNotNone(result.pnl_pct)
        self.assertAlmostEqual(result.exit_price or 0, 0.43, places=2)

    def test_engine_runs(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            _seed_v13(conn)
            report = run_v13_execution_research(conn)
        self.assertIn("verdict", report)
        self.assertEqual(len(report["entry_models"]), len(ENTRY_MODELS))


class CleanEligibilityTestCase(unittest.TestCase):
    def test_bid_gt_ask_rejected(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE multi_timeframe_snapshots (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER,
                market_15m_slug TEXT,
                market_15m_yes_bid REAL,
                market_15m_yes_ask REAL,
                market_15m_no_bid REAL,
                market_15m_no_ask REAL,
                market_15m_strike REAL,
                btc_price REAL
            );
        """)
        conn.execute(
            """
            INSERT INTO multi_timeframe_snapshots VALUES (
                1, 1700000000, 'btc-updown-15m-1700000000',
                0.55, 0.50, 0.45, 0.50, 60000, 60000
            )
            """
        )
        row = conn.execute("SELECT * FROM multi_timeframe_snapshots").fetchone()
        ok, reason = row_is_clean_15m(row)
        self.assertFalse(ok)
        self.assertEqual(reason, "BID_GT_ASK")
        audit = audit_bid_gt_ask_timeline(conn)
        self.assertEqual(audit["total_bid_gt_ask"], 1)


class ExecutionFailureAuditTestCase(unittest.TestCase):
    def test_classify_entry_failed(self) -> None:
        self.assertEqual(_classify_error("result=FAILED reason=entry_failed"), "entry_failed")

    def test_classify_clob_404(self) -> None:
        self.assertEqual(
            _classify_error("No orderbook exists for requested token id"),
            "clob_404_no_orderbook",
        )

    def test_audit_empty_db(self) -> None:
        conn = sqlite3.connect(":memory:")
        report = audit_execution_failures(conn)
        self.assertIn("severity_verdict", report)


if __name__ == "__main__":
    unittest.main()
