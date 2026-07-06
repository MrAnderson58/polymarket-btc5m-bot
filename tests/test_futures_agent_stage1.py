"""Tests for Futures Intelligence Agent Stage 1."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.audit import run_architecture_audit
from bot.research.futures_agent.config import (
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING_SNAPSHOT,
    STATUS_RECEIVED,
    STATUS_REJECTED,
)
from bot.research.futures_agent.db import agent_connection
from bot.research.futures_agent.ingestion import ingest_forwarded_signal
from bot.research.futures_agent.pipeline import process_input, process_pending
from bot.research.futures_agent.schema import apply_migrations


EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
    "TP2: 2.45\n"
)

MARKET_REVIEW = (
    "ATOM Technical Analysis / Review\n"
    "Outlook neutral. BTC correlates with market."
)

PROMO = "Join t.me/vip for 20% discount on LINK trading course"


class FuturesAgentStage1TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "agent.db"
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return agent_connection(self.db_url)

    def test_migrations_idempotent(self) -> None:
        with self._conn() as conn:
            a = apply_migrations(conn, postgres=False)
            b = apply_migrations(conn, postgres=False)
        self.assertTrue(a)
        self.assertEqual(b, [])

    def test_raw_text_unchanged_after_processing(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="t1")
            before = conn.execute(
                "SELECT raw_text FROM futures_agent_inputs WHERE id = ?",
                (ing.input_id,),
            ).fetchone()["raw_text"]
            process_input(conn, ing.input_id)
            after = conn.execute(
                "SELECT raw_text FROM futures_agent_inputs WHERE id = ?",
                (ing.input_id,),
            ).fetchone()["raw_text"]
        self.assertEqual(before, after)
        self.assertEqual(before, EXPLICIT_LONG.strip())

    def test_explicit_signal_ingestion_and_parse(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="sig1")
            proc = process_input(conn, ing.input_id)
            sig = conn.execute(
                "SELECT * FROM futures_agent_signals WHERE input_id = ?",
                (ing.input_id,),
            ).fetchone()
            tps = conn.execute(
                "SELECT target_price FROM futures_agent_targets WHERE signal_id = ? ORDER BY target_index",
                (proc.signal_id,),
            ).fetchall()
        self.assertTrue(proc.passes_gate)
        self.assertEqual(proc.processing_status, STATUS_PENDING_SNAPSHOT)
        self.assertEqual(sig["symbol"], "SUI")
        self.assertEqual(sig["direction"], "LONG")
        self.assertAlmostEqual(sig["entry_low"], 2.14)
        self.assertAlmostEqual(sig["stop_loss"], 2.05)
        self.assertEqual(len(tps), 2)

    def test_market_review_rejected(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_forwarded_signal(conn, raw_text=MARKET_REVIEW, telegram_message_id="rev1")
            proc = process_input(conn, ing.input_id)
        self.assertFalse(proc.passes_gate)
        self.assertEqual(proc.processing_status, STATUS_REJECTED)
        self.assertEqual(proc.taxonomy, "MARKET_REVIEW")

    def test_promo_rejected(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_forwarded_signal(conn, raw_text=PROMO, telegram_message_id="promo1")
            proc = process_input(conn, ing.input_id)
        self.assertEqual(proc.processing_status, STATUS_REJECTED)

    def test_parser_does_not_invent_fields(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            text = "Random commentary about markets"
            ing = ingest_forwarded_signal(conn, raw_text=text, telegram_message_id="x1")
            proc = process_input(conn, ing.input_id)
            sig = conn.execute(
                "SELECT symbol, direction, stop_loss FROM futures_agent_signals WHERE input_id = ?",
                (ing.input_id,),
            ).fetchone()
        self.assertIsNone(sig["symbol"])
        self.assertIsNone(sig["direction"])
        self.assertIsNone(sig["stop_loss"])

    def test_duplicate_prevention(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            a = ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="dup")
            b = ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="dup")
        self.assertFalse(a.duplicate)
        self.assertTrue(b.duplicate)
        self.assertEqual(a.input_id, b.input_id)

    def test_duplicate_analysis_prevention(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="once")
            process_input(conn, ing.input_id)
            again = process_input(conn, ing.input_id)
        self.assertEqual(again.gate_reason, "already_processed")

    def test_process_pending(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn, postgres=False)
            ingest_forwarded_signal(conn, raw_text=EXPLICIT_LONG, telegram_message_id="p1")
            ingest_forwarded_signal(conn, raw_text=MARKET_REVIEW, telegram_message_id="p2")
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_inputs WHERE processing_status = ?",
                (STATUS_RECEIVED,),
            ).fetchone()["n"]
            self.assertEqual(pending, 2)
            results = process_pending(conn, limit=10)
        self.assertEqual(len(results), 2)

    def test_no_execution_imports_in_agent(self) -> None:
        audit = run_architecture_audit()
        self.assertTrue(audit["execution_isolation"]["clean"])

    @patch.dict("os.environ", {"FUTURES_AGENT_SQLITE_PATH": ""}, clear=False)
    def test_architecture_audit_runs(self) -> None:
        audit = run_architecture_audit()
        self.assertIn("repository", audit)
        self.assertTrue(audit["reusable_modules"]["parser_v2"])


if __name__ == "__main__":
    unittest.main()
