"""Tests for read-only bidirectional research modules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.bidirectional_live_audit import LiveTrade, audit_integrity
from bot.research.bidirectional_quote_alignment import run_alignment_study
from bot.research.bidirectional_v12_research import (
    run_v12_research,
    _continuation_label,
)


def _seed(conn, n: int = 5) -> None:
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
        CREATE TABLE IF NOT EXISTS v4_shadow_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            timestamp INTEGER NOT NULL,
            seconds_from_start INTEGER,
            seconds_left INTEGER,
            btc_price REAL,
            strike REAL,
            delta REAL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            trend_score REAL,
            trend_side TEXT,
            spread REAL
        );
        CREATE TABLE IF NOT EXISTS market_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            seconds_remaining REAL NOT NULL,
            strike_price REAL NOT NULL,
            btc_price REAL NOT NULL,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            signal TEXT,
            checked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    base = 1_700_000_000
    for i in range(n):
        slug = f"btc-updown-5m-{1000 + i}"
        ws = base + i * 300
        entry_ts = ws + 60
        side = "YES" if i % 2 == 0 else "NO"
        entry = 0.38 if side == "YES" else 0.40
        exit_p = entry * 1.08
        pnl = ((exit_p - entry) / entry) * 100
        conn.execute(
            """
            INSERT INTO bidirectional_shadow_trades (
                market_slug, window_start_ts, side, status, entry_price, entry_ts,
                entry_regime, exit_price, exit_reason, pnl_pct, holding_time_seconds
            ) VALUES (?, ?, ?, 'closed', ?, ?, 'NORMAL', ?, 'TRAILING_STOP', ?, 45)
            """,
            (slug, ws, side, entry, entry_ts, exit_p, pnl),
        )
        conn.execute(
            """
            INSERT INTO bidirectional_shadow_observations (
                market_slug, timestamp, decision, entry_price, regime, btc_move_30s
            ) VALUES (?, ?, ?, ?, 'NORMAL', 20.0)
            """,
            (slug, entry_ts, side, entry),
        )
        conn.execute(
            """
            INSERT INTO v4_shadow_observations (
                market_slug, window_start_ts, timestamp,
                seconds_from_start, seconds_left, btc_price,
                yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (?, ?, ?, 60, 240, 60100, ?, ?, ?, ?)
            """,
            (
                slug, ws, entry_ts,
                entry - 0.01, entry,
                exit_p - 0.02 if side == "NO" else 0.58,
                entry + 0.02 if side == "NO" else 0.60,
            ),
        )
        conn.execute(
            """
            INSERT INTO market_checks (
                market_slug, seconds_remaining, strike_price, btc_price,
                yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
            ) VALUES (?, 240, 60000, 60100, ?, ?, 0.58, 0.60, NULL, datetime(?, 'unixepoch'))
            """,
            (slug, entry - 0.01, entry, entry_ts),
        )
    conn.commit()


class QuoteAlignmentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_alignment_study_runs(self) -> None:
        with connect(self.db_path) as conn:
            _seed(conn, 3)
            study = run_alignment_study(conn, corrected=True)
        self.assertEqual(study["trade_count"], 3)
        self.assertIn("legacy_v4_entry_flags", study)

    def test_quote_suspects_not_integrity_violations(self) -> None:
        with connect(self.db_path) as conn:
            _seed(conn, 1)
            conn.execute(
                """
                UPDATE v4_shadow_observations SET yes_ask=0.50
                WHERE market_slug='btc-updown-5m-1000'
                """
            )
            conn.commit()
            from bot.research.bidirectional_live_audit import load_closed_trades
            trades = load_closed_trades(conn)
            result = audit_integrity(conn, trades)
        types = [v.violation_type for v in result["violations"]]
        self.assertNotIn("ENTRY_NOT_ASK", types)
        suspect_types = [v.violation_type for v in result["quote_suspects"]]
        self.assertIn("ENTRY_NOT_ASK", suspect_types)


class V12ResearchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_v12_research_runs(self) -> None:
        with connect(self.db_path) as conn:
            _seed(conn, 8)
            research = run_v12_research(conn, corrected=True)
        self.assertEqual(research["trade_count"], 8)
        self.assertIn("no_continuation", research)
        self.assertIn("q4_degradation", research)
        self.assertTrue(research["v12_hypotheses"])

    def test_continuation_label(self) -> None:
        t = LiveTrade(
            1, "m", 1000, "NO", 0.40, 1100, "NORMAL", 0.6,
            0.36, "STOP", -10, 20, 0.40, 2.0, 200,
        )
        self.assertEqual(_continuation_label(t), "no_weak")
        t.btc_move_30s = -20.0
        self.assertEqual(_continuation_label(t), "no_momentum")
        t.btc_move_30s = 20.0
        self.assertEqual(_continuation_label(t), "no_continuation_against_move")


if __name__ == "__main__":
    unittest.main()
