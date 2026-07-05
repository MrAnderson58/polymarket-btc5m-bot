"""Tests for futures signal intelligence research layer."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.database import connect, init_db
from bot.research.futures.market_snapshot import _return_over, build_market_snapshot
from bot.research.futures.parser import parse_signal_text
from bot.research.futures.schema import SIGNALS_TABLE, ensure_tables, insert_signal
from bot.research.futures.source_audit import audit_source_data
from bot.research.futures.source_data import RawMessage, _normalize_ts


class FuturesParserTestCase(unittest.TestCase):
    def test_parse_long_with_levels(self) -> None:
        text = (
            "BTC LONG\n"
            "Entry: 95000-95200\n"
            "SL: 94000\n"
            "TP1: 96000\n"
            "TP2: 97000\n"
            "Leverage 10x\n"
            "Confidence: 80%"
        )
        p = parse_signal_text(text)
        self.assertEqual(p.symbol, "BTC")
        self.assertEqual(p.side, "LONG")
        self.assertEqual(p.entry_min, 95000.0)
        self.assertEqual(p.entry_max, 95200.0)
        self.assertEqual(p.stop_loss, 94000.0)
        self.assertEqual(p.take_profits, [96000.0, 97000.0])
        self.assertEqual(p.leverage, 10.0)
        self.assertAlmostEqual(p.confidence, 0.8)
        self.assertGreater(p.parser_confidence, 0.5)

    def test_parse_short_minimal(self) -> None:
        p = parse_signal_text("ETH SHORT")
        self.assertEqual(p.symbol, "ETH")
        self.assertEqual(p.side, "SHORT")
        self.assertIsNone(p.entry_min)
        self.assertIsNone(p.stop_loss)

    def test_no_invented_values(self) -> None:
        p = parse_signal_text("Random market commentary without trade")
        self.assertIsNone(p.side)
        self.assertIsNone(p.symbol)
        self.assertIsNone(p.stop_loss)


class FuturesNoLookAheadTestCase(unittest.TestCase):
    def test_return_uses_past_only(self) -> None:
        candles = [
            [1000_000, "1", "1", "1", "100", "0", 0, 0, 0, 0, 0, 0],
            [1060_000, "1", "1", "1", "101", "0", 0, 0, 0, 0, 0, 0],
            [1120_000, "1", "1", "1", "102", "0", 0, 0, 0, 0, 0, 0],
        ]
        ts = 1120
        ret = _return_over(candles, ts, 60)
        self.assertIsNotNone(ret)
        self.assertAlmostEqual(ret, 0.990099, places=3)

    @patch("bot.research.futures.market_snapshot._fetch_klines")
    def test_snapshot_ts_matches_signal(self, mock_klines: MagicMock) -> None:
        ts = 1_700_000_000
        mock_klines.return_value = [
            [(ts - 60) * 1000, "1", "1", "1", "100", "0", 0, 0, 0, 0, 0, 0],
            [ts * 1000, "1", "1", "1", "101", "0", 0, 0, 0, 0, 0, 0],
        ]
        features, coverage = build_market_snapshot("BTC", ts)
        self.assertEqual(features["snapshot_ts"], ts)
        self.assertEqual(features["price"], 101.0)


class FuturesSchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_telegram(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS telegram_messages (
                id INTEGER PRIMARY KEY,
                message_id TEXT,
                source TEXT,
                text TEXT,
                timestamp INTEGER
            );
        """)
        conn.execute(
            """
            INSERT INTO telegram_messages (message_id, source, text, timestamp)
            VALUES ('1', 'test_channel', 'BTC LONG entry 100 sl 90 tp 110', 1700000000)
            """,
        )
        conn.commit()

    def test_audit_with_telegram_table(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_telegram(conn)
            audit = audit_source_data(conn)
        self.assertEqual(audit["messages"]["count"], 1)
        self.assertEqual(audit["messages"]["long_count"], 1)

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_DATABASE_URL": "postgresql:///trading_ai",
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_REQUIRE_POSTGRES": "true",
    })
    @patch("bot.research.futures.source_reader.PostgresSourceReader")
    @patch("bot.research.futures.source_reader._postgres_connect")
    def test_audit_uses_injected_sqlite_not_env_postgres(
        self, mock_pg_connect, mock_pg_reader,
    ) -> None:
        with connect(self.db_path) as conn:
            self._seed_telegram(conn)
            audit = audit_source_data(conn)
        self.assertEqual(audit["messages"]["count"], 1)
        mock_pg_reader.assert_not_called()
        mock_pg_connect.assert_not_called()

    def test_unique_signal_constraint(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            row = {
                "source": "ch", "message_id": "1", "timestamp": 100,
                "raw_text": "x", "parser_version": "deterministic_v1",
            }
            id1 = insert_signal(conn, row)
            id2 = insert_signal(conn, row)
            self.assertEqual(id1, id2)
            n = conn.execute(f"SELECT COUNT(*) FROM {SIGNALS_TABLE}").fetchone()[0]
            self.assertEqual(n, 1)


class FuturesTimestampTestCase(unittest.TestCase):
    def test_ms_to_s(self) -> None:
        self.assertEqual(_normalize_ts(1700000000000), 1700000000)

    def test_s_unchanged(self) -> None:
        self.assertEqual(_normalize_ts(1700000000), 1700000000)


class FuturesObserveAgentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @patch("bot.research.futures.observe_agent.build_market_snapshot")
    @patch("bot.research.futures.observe_agent.score_sources")
    @patch("bot.research.futures.observe_agent.walk_forward_abc")
    def test_observe_insufficient_parse(
        self, mock_wf: MagicMock, mock_score: MagicMock, mock_snap: MagicMock,
    ) -> None:
        from bot.research.futures.observe_agent import REC_INSUFFICIENT, observe_new_signal

        mock_wf.return_value = {"status": "insufficient_data"}
        mock_score.return_value = []
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            result = observe_new_signal(conn, RawMessage(
                source="ch", message_id="99", timestamp=1700000000, text="hello world",
            ))
        self.assertEqual(result["recommendation"], REC_INSUFFICIENT)


if __name__ == "__main__":
    unittest.main()
