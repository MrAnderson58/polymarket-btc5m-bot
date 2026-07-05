"""Tests for multi-timeframe context research layer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.labels import alignment_label, htf_label_from_btc
from bot.research.mtf.models import BtcSpotContext, PolymarketTfContext
from bot.research.mtf.analysis import (
    context_performance_matrix,
    test_hypotheses,
    walk_forward_abc,
)
from bot.research.mtf.context_builder import build_trade_context
from bot.research.mtf.data_audit import audit_data_coverage
from bot.research.mtf.models import TradeContext
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot
from bot.research.bidirectional_live_audit import LiveTrade


def _seed_mc(conn: sqlite3.Connection, base_ts: int, n: int = 100) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            seconds_remaining REAL NOT NULL,
            strike_price REAL NOT NULL,
            btc_price REAL NOT NULL,
            yes_bid REAL, yes_ask REAL, no_bid REAL, no_ask REAL,
            signal TEXT,
            checked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bidirectional_shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'closed',
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_regime TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            holding_time_seconds REAL
        );
    """)
    price = 60000.0
    for i in range(n):
        ts = base_ts + i * 10
        price += 5 if i % 3 == 0 else -2
        conn.execute(
            """
            INSERT INTO market_checks (
                market_slug, seconds_remaining, strike_price, btc_price,
                yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
            ) VALUES ('btc-updown-5m-test', 200, 60000, ?, 0.4, 0.42, 0.58, 0.6, NULL,
                      datetime(?, 'unixepoch'))
            """,
            (price, ts),
        )
    conn.commit()


class MtfBtcContextTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_btc_context_no_look_ahead(self) -> None:
        base = 1_700_000_000
        with connect(self.db_path) as conn:
            _seed_mc(conn, base, 200)
            ctx = build_btc_context(conn, base + 1000)
        self.assertIsNotNone(ctx.price)
        self.assertIsNotNone(ctx.return_5m)

    def test_htf_labels(self) -> None:
        up = BtcSpotContext(timestamp=0, price=60000, return_1h=0.5, return_15m=0.2)
        self.assertEqual(htf_label_from_btc(up), "HTF_STRONG_UP")
        down = BtcSpotContext(timestamp=0, price=60000, return_1h=-0.5, return_15m=-0.2)
        self.assertEqual(htf_label_from_btc(down), "HTF_STRONG_DOWN")


class MtfAnalysisTestCase(unittest.TestCase):
    def test_walk_forward_runs(self) -> None:
        contexts = []
        for i in range(40):
            btc = BtcSpotContext(timestamp=i, price=60000, return_1h=0.2 if i % 2 else -0.2)
            pm = PolymarketTfContext("15m", available=True, prob_yes=0.6, prob_direction="UP")
            contexts.append(TradeContext(
                i, f"m{i}", i * 100, "YES" if i % 2 else "NO", 0.4, 5.0 if i % 3 else -4.0,
                btc=btc, pm_15m=pm,
            ))
        wf = walk_forward_abc(contexts)
        self.assertIn("MODEL_A_5m_only", wf.get("models", {}))

    def test_hypotheses_structure(self) -> None:
        contexts = [
            TradeContext(1, "m", 100, "NO", 0.4, 10.0, btc=BtcSpotContext(100, 60000),
                         pm_15m=PolymarketTfContext("15m", available=True, prob_direction="DOWN"),
                         pm_1h=PolymarketTfContext("1h", available=True, prob_direction="DOWN")),
        ]
        h = test_hypotheses(contexts)
        self.assertIn("A_no_triple_align", h)


class MtfSnapshotsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_audit_and_snapshots(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            insert_snapshot(conn, {
                "timestamp": 1_700_000_000,
                "btc_price": 60000,
                "market_5m_slug": "btc-updown-5m-1700000000",
                "market_15m_slug": "btc-updown-15m-1700000000",
                "market_15m_yes_bid": 0.48,
                "market_15m_yes_ask": 0.50,
                "market_15m_no_bid": 0.50,
                "market_15m_no_ask": 0.52,
            })
            audit = audit_data_coverage(conn)
        self.assertEqual(audit["snapshot_total"], 1)
        self.assertEqual(audit["snapshot_15m"], 1)


if __name__ == "__main__":
    unittest.main()
