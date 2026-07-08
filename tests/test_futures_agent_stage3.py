"""Tests for Futures Agent Stage 3 research pipeline (Phase B)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.db import agent_connection
from bot.research.futures_agent.research_ingest import ingest_research_posts, run_thesis_extract
from bot.research.futures_agent.research_scoring import (
    SourceScoreInputs,
    bayesian_shrinkage_rate,
    compose_source_score,
    wilson_lower_bound,
)
from bot.research.futures_agent.research_taxonomy import (
    ResearchContentType,
    classify_research_content,
)
from bot.research.futures_agent.research_utils import content_hash, extract_symbols
from bot.research.futures_agent.schema import STAGE3_VERSION, apply_migrations
from bot.research.futures_agent.thesis_extract import extract_theses_from_post


WHALE_BORROW = (
    "A whale wallet 0xabc123 borrowed 5,000 ETH from Aave to sell on Binance."
)
THIRD_PARTY_LONG = (
    "Smart money trader opened a 20x leveraged ETH long position worth $12M."
)
AUTHOR_SIGNAL = (
    "ORDI LONG\nEntry: 45.2-45.8\nSL: 43.5\nTP1: 48.0\nTP2: 50.5"
)
ORDI_THESIS = (
    "ORDI is weaker than BTC. After support loss I expect continuation lower. "
    "Interested after retest."
)
NEWS_ETF = "Breaking: SEC approves spot Bitcoin ETF filing from major asset manager."


def _create_source_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE telegram_messages (
            id INTEGER PRIMARY KEY,
            channel_name TEXT NOT NULL,
            message_text TEXT NOT NULL,
            message_date INTEGER NOT NULL,
            telegram_message_id TEXT NOT NULL
        )
        """,
    )
    conn.executemany(
        """
        INSERT INTO telegram_messages
        (channel_name, message_text, message_date, telegram_message_id)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


class FuturesAgentStage3TestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.agent_db = Path(self._tmpdir.name) / "agent.db"
        self.source_db = Path(self._tmpdir.name) / "source.db"
        self.agent_url = f"sqlite:///{self.agent_db}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _agent_conn(self):
        return agent_connection(self.agent_url)

    def test_stage3_migration_creates_tables(self) -> None:
        with self._agent_conn() as conn:
            applied = apply_migrations(conn)
            self.assertTrue(any("v3" in item for item in applied))
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
        for name in (
            "futures_agent_trader_posts",
            "futures_agent_trader_theses",
            "futures_agent_trader_levels",
            "futures_agent_thesis_outcomes",
            "futures_agent_source_scores",
        ):
            self.assertIn(name, tables)

    def test_whale_borrow_not_explicit_signal(self) -> None:
        cls = classify_research_content(WHALE_BORROW)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT),
        )
        self.assertNotEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_third_party_long_not_explicit_signal(self) -> None:
        cls = classify_research_content(THIRD_PARTY_LONG)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT),
        )

    def test_author_signal_is_explicit(self) -> None:
        cls = classify_research_content(AUTHOR_SIGNAL)
        self.assertEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_ordi_thesis_without_levels(self) -> None:
        cls = classify_research_content(ORDI_THESIS)
        self.assertEqual(cls.content_type, ResearchContentType.TRADER_THESIS)
        theses = extract_theses_from_post(
            ORDI_THESIS,
            ResearchContentType.TRADER_THESIS.value,
            symbols=["ORDI"],
        )
        self.assertEqual(len(theses), 1)
        thesis = theses[0]
        self.assertFalse(thesis.unresolved)
        self.assertEqual(thesis.symbol, "ORDI")
        self.assertEqual(thesis.direction, "SHORT")
        self.assertTrue(thesis.condition_text)
        self.assertEqual(len(thesis.levels), 0)

    def test_news_event_neutral_no_levels_required(self) -> None:
        cls = classify_research_content(NEWS_ETF)
        self.assertEqual(cls.content_type, ResearchContentType.NEWS_EVENT)
        theses = extract_theses_from_post(
            NEWS_ETF,
            ResearchContentType.NEWS_EVENT.value,
            symbols=["BTC"],
        )
        self.assertEqual(len(theses), 1)
        self.assertEqual(theses[0].direction, "NEUTRAL")

    def test_content_hash_dedup_normalization(self) -> None:
        a = content_hash("BTC  LONG\nEntry 1.0")
        b = content_hash("btc long entry 1.0")
        self.assertEqual(a, b)

    def test_symbol_extraction(self) -> None:
        syms = extract_symbols("$ORDI looks weak vs BTCUSDT")
        self.assertIn("ORDI", syms)
        self.assertIn("BTC", syms)

    def test_ingest_idempotent(self) -> None:
        _create_source_db(
            self.source_db,
            [
                ("lookonchain", WHALE_BORROW, 1_700_000_000, "m1"),
                ("signalyp", AUTHOR_SIGNAL, 1_700_000_100, "m2"),
                ("signalyp", AUTHOR_SIGNAL, 1_700_000_200, "m3"),
            ],
        )

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_configured_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                stats1 = ingest_research_posts(conn, limit=10)
                stats2 = ingest_research_posts(conn, limit=10)
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM futures_agent_trader_posts",
                ).fetchone()["n"]

        self.assertEqual(stats1.inserted, 2)
        self.assertEqual(stats2.inserted, 0)
        self.assertGreater(stats2.skipped_duplicate + stats2.skipped_hash_duplicate, 0)
        self.assertEqual(count, 2)

    def test_thesis_extract_persists_levels(self) -> None:
        _create_source_db(
            self.source_db,
            [("signalyp", AUTHOR_SIGNAL, 1_700_000_000, "sig1")],
        )

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_configured_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                ingest_research_posts(conn, limit=10)
                stats = run_thesis_extract(conn)
                levels = conn.execute(
                    "SELECT level_type, price FROM futures_agent_trader_levels ORDER BY level_type",
                ).fetchall()

        self.assertEqual(stats["theses_inserted"], 1)
        self.assertGreaterEqual(stats["levels_inserted"], 2)
        level_types = {r["level_type"] for r in levels}
        self.assertIn("STOP", level_types)
        self.assertTrue({"ENTRY_LOW", "ENTRY_HIGH"} & level_types)

    def test_max_per_source_cap(self) -> None:
        rows = [
            ("lookonchain", f"Whale bought BTC msg {i}", 1_700_000_000 + i, f"w{i}")
            for i in range(5)
        ]
        rows.append(("signalyp", ORDI_THESIS, 1_700_000_500, "s1"))
        _create_source_db(self.source_db, rows)

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_configured_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                stats = ingest_research_posts(conn, max_per_source=2)

        self.assertEqual(stats.inserted, 3)
        self.assertEqual(stats.skipped_source_cap, 3)
        self.assertEqual(stats.per_channel.get("lookonchain"), 2)
        self.assertEqual(stats.per_channel.get("signalyp"), 1)

    def test_wilson_and_bayesian_helpers(self) -> None:
        wlb = wilson_lower_bound(7, 10)
        self.assertIsNotNone(wlb)
        self.assertGreater(wlb, 0.39)
        shrunk = bayesian_shrinkage_rate(7, 10)
        self.assertIsNotNone(shrunk)
        composed = compose_source_score(
            SourceScoreInputs(wins=7, total=10, avg_mfe=2.0, avg_mae=1.0),
        )
        self.assertIsNotNone(composed["wilson_lower_bound"])
        self.assertEqual(composed["expectancy_proxy"], 1.0)

    def test_migration_version_constant(self) -> None:
        self.assertEqual(STAGE3_VERSION, 3)


if __name__ == "__main__":
    unittest.main()
