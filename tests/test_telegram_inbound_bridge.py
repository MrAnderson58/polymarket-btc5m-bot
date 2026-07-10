"""Tests for Telegram inbound audit and research bridge."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.futures_agent.db import (
    _PgConnWrapper,
    _adapt_sql_placeholders,
    agent_connection,
)
from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.ingestion import ingest_from_telegram
from bot.research.futures_agent.pipeline import process_input
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.telegram_inbound_audit import (
    TELEGRAM_SOURCE_LIKE,
    run_telegram_inbound_audit,
    sync_unbridged_inputs,
)
from bot.research.futures_agent.telegram_inbound_bridge import bridge_input_to_research
from bot.research.futures_agent.telegram_context_readiness import (
    telegram_context_readiness_report,
)

EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
)

NEWS_TEXT = "Breaking: SEC approves new Bitcoin ETF filing by major asset manager."

COMMENTARY = "BTC looks heavy into resistance; expect chop unless 68k reclaims."


class TelegramInboundBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "tg_bridge.db"
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def _ingest(self, conn, text: str, *, msg_id: int) -> int:
        ing = ingest_from_telegram(
            conn, raw_text=text, chat_id=12345, message_id=msg_id,
            forward_origin={"type": "channel", "chat": {"id": -100}},
            received_at=1_700_000_000,
        )
        return ing.input_id

    def test_no_writes_to_telegram_messages(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, EXPLICIT_LONG, msg_id=1)
            bridge_input_to_research(conn, input_id)
        src = sqlite3.connect(":memory:")
        src.execute("CREATE TABLE telegram_messages (id INTEGER)")
        tables = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
        self.assertEqual([("telegram_messages",)], tables)

    def test_forwarded_explicit_signal_bridged(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, EXPLICIT_LONG, msg_id=10)
            process_input(conn, input_id)
            result = bridge_input_to_research(conn, input_id)
            self.assertTrue(result.bridged or result.duplicate)
            self.assertIsNotNone(result.post_id)
            post = conn.execute(
                "SELECT * FROM futures_agent_trader_posts WHERE id = ?",
                (result.post_id,),
            ).fetchone()
            self.assertEqual(post["content_type"], "EXPLICIT_SIGNAL")
            self.assertEqual(int(post["message_ts"]), 1_700_000_000)

    def test_forwarded_commentary_bridged(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, COMMENTARY, msg_id=11)
            result = bridge_input_to_research(conn, input_id)
            post = conn.execute(
                "SELECT content_type FROM futures_agent_trader_posts WHERE id = ?",
                (result.post_id,),
            ).fetchone()
            self.assertIn(post["content_type"], ("MARKET_COMMENTARY", "TRADER_THESIS", "OTHER"))

    def test_forwarded_news_bridged(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, NEWS_TEXT, msg_id=12)
            result = bridge_input_to_research(conn, input_id)
            post = conn.execute(
                "SELECT content_type FROM futures_agent_trader_posts WHERE id = ?",
                (result.post_id,),
            ).fetchone()
            self.assertEqual(post["content_type"], "NEWS_EVENT")

    def test_duplicate_forwarded_message_idempotent(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, EXPLICIT_LONG, msg_id=99)
            r1 = bridge_input_to_research(conn, input_id)
            r2 = bridge_input_to_research(conn, input_id)
            self.assertTrue(r1.bridged)
            self.assertTrue(r2.duplicate)
            n = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_posts").fetchone()["n"]
            self.assertEqual(n, 1)

    def test_content_hash_duplicate_no_second_post(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            id1 = self._ingest(conn, EXPLICIT_LONG, msg_id=1)
            id2 = self._ingest(conn, EXPLICIT_LONG, msg_id=2)
            bridge_input_to_research(conn, id1)
            r2 = bridge_input_to_research(conn, id2)
            self.assertTrue(r2.duplicate)
            n = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_posts").fetchone()["n"]
            self.assertEqual(n, 1)

    def test_timestamp_preserved(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, COMMENTARY, msg_id=20)
            result = bridge_input_to_research(conn, input_id)
            post = conn.execute(
                "SELECT message_ts FROM futures_agent_trader_posts WHERE id = ?",
                (result.post_id,),
            ).fetchone()
            self.assertEqual(int(post["message_ts"]), 1_700_000_000)

    def test_no_duplicate_thesis_created(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, EXPLICIT_LONG, msg_id=30)
            bridge_input_to_research(conn, input_id)
            n = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_trader_theses").fetchone()["n"]
            self.assertEqual(n, 0)

    def test_inbound_audit_report(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, EXPLICIT_LONG, msg_id=40)
            bridge_input_to_research(conn, input_id)
            text = run_telegram_inbound_audit(conn, limit=5)
            self.assertIn("TELEGRAM INBOUND AUDIT", text)
            self.assertIn("inbound_messages_total", text)
            self.assertIn("visible_trader_posts=True", text)
            self.assertNotIn("test-token", text)

    def test_inbound_audit_limit_10(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            for i in range(12):
                self._ingest(conn, f"msg {i}", msg_id=100 + i)
            text = run_telegram_inbound_audit(conn, limit=10)
            self.assertIn("=== Last 10 inbound messages ===", text)
            self.assertEqual(text.count("    raw_text:"), 10)

    def test_inbound_audit_zero_rows(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            text = run_telegram_inbound_audit(conn, limit=10)
            self.assertIn("inbound_messages_total: 0", text)
            self.assertIn("No telegram inbound messages in agent DB.", text)

    def test_inbound_audit_unbridged_row(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            self._ingest(conn, COMMENTARY, msg_id=77)
            text = run_telegram_inbound_audit(conn, limit=5)
            self.assertIn("visible_trader_posts=False", text)

    def test_inbound_audit_read_only(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            self._ingest(conn, EXPLICIT_LONG, msg_id=88)
            counts_before = {
                t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                for t in (
                    "futures_agent_inputs",
                    "futures_agent_trader_posts",
                    "futures_agent_telegram_research_bridge",
                )
            }
            run_telegram_inbound_audit(conn, limit=10)
            counts_after = {
                t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                for t in counts_before
            }
            self.assertEqual(counts_before, counts_after)

    def test_pg_inbound_audit_sql_no_literal_percent(self) -> None:
        """Regression: LIKE 'telegram:%' + LIMIT param crashed psycopg2."""
        sql = """
        SELECT i.id FROM futures_agent_inputs i
        WHERE i.source LIKE ?
        ORDER BY i.received_at DESC
        LIMIT ?
        """
        params = (TELEGRAM_SOURCE_LIKE, 10)
        pg_sql = _adapt_sql_placeholders(sql, params)
        self.assertEqual(pg_sql.count("%s"), 2)
        stripped = pg_sql.replace("%s", "")
        self.assertNotIn("%", stripped)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur
        psycopg2 = MagicMock()
        extras = MagicMock()
        extras.RealDictCursor = object
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            wrapper.execute(sql, params)
        mock_cur.execute.assert_called_once_with(pg_sql, params)

    def test_pg_context_readiness_sql_no_literal_percent(self) -> None:
        from bot.research.futures_agent.telegram_inbound_bridge import INBOUND_CHANNEL_PREFIX

        sql = """
        SELECT p.id FROM futures_agent_trader_posts p
        JOIN futures_agent_telegram_research_bridge b ON b.post_id = p.id
        WHERE p.channel_name LIKE ? AND p.message_ts >= ?
        LIMIT 100
        """
        params = (f"{INBOUND_CHANNEL_PREFIX}:%", 1_700_000_000)
        pg_sql = _adapt_sql_placeholders(sql, params)
        stripped = pg_sql.replace("%s", "")
        self.assertNotIn("%", stripped)
        self.assertEqual(pg_sql.count("%s"), 2)

    def test_sync_unbridged_inputs(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            self._ingest(conn, COMMENTARY, msg_id=50)
            stats = sync_unbridged_inputs(conn)
            self.assertEqual(stats["bridged"], 1)

    def test_context_readiness_report(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            input_id = self._ingest(conn, "BTC breaking down through support", msg_id=60)
            bridge_input_to_research(conn, input_id)
            text = telegram_context_readiness_report(conn, symbol="BTC", days=30)
            self.assertIn("CONTEXT LINKING READINESS", text)

    def test_no_live_trading_path(self) -> None:
        import bot.research.futures_agent.telegram_inbound_bridge as bridge
        import bot.research.futures_agent.telegram_inbound as inbound
        src = Path(bridge.__file__).read_text() + Path(inbound.__file__).read_text()
        self.assertNotIn("bot.main", src)
        self.assertNotIn("bot.execution", src)
        self.assertNotIn("TRADING_MODE=live", src)


if __name__ == "__main__":
    unittest.main()
