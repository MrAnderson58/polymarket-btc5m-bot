"""Tests for Futures Agent Stage 1b Telegram inbound."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.futures_agent.config import INPUT_TYPE_TELEGRAM, STATUS_COMPLETE
from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.ingestion import ingest_from_telegram
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.snapshot import SnapshotResult
from bot.research.futures_agent.telegram_config import (
    TelegramInboundConfigError,
    get_allowed_chat_ids,
    require_telegram_inbound_config,
)
from bot.research.futures_agent.telegram_inbound import (
    extract_forward_origin,
    extract_message_text,
    process_telegram_message,
)
from bot.research.futures_agent.telegram_replies import (
    format_telegram_accepted,
    format_telegram_rejected,
)

EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
    "TP2: 2.45\n"
)

MARKET_REVIEW = "ATOM Technical Analysis / Review\nOutlook neutral."


def _synthetic_candles(base_ts: int, start: float, n: int = 300) -> list:
    candles = []
    price = start
    for i in range(n):
        ts = base_ts - (n - i) * 60
        price *= 1.0002
        candles.append([
            ts * 1000, str(price * 0.999), str(price * 1.001), str(price * 0.999),
            str(price), "1000", (ts + 60) * 1000, "0", "10", "0", "0", "0",
        ])
    return candles


class MockProvider:
    def symbol_available(self, pair, end_ts):
        return True

    def fetch_spot_klines(self, pair, interval, end_ts, limit=500):
        return _synthetic_candles(end_ts, 0.74 if "SUI" in pair else 65000.0)

    def fetch_futures_klines(self, pair, interval, end_ts, limit=500):
        return self.fetch_spot_klines(pair, interval, end_ts, limit=limit)

    def fetch_funding_rate(self, pair, end_ts):
        return 0.0001, "available"


class TelegramInboundTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "tg.db"
        os.environ["FUTURES_AGENT_SQLITE_PATH"] = str(self.db_path)
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        os.environ["TELEGRAM_AGENT_ALLOWED_CHAT_IDS"] = "12345"
        reset_bootstrap_for_tests()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", None)
        os.environ.pop("FUTURES_AGENT_SQLITE_PATH", None)

    def _message(self, text: str, *, msg_id: int = 1, forwarded: bool = False) -> dict:
        msg = {
            "message_id": msg_id,
            "date": 1_700_000_000,
            "chat": {"id": 12345, "type": "private"},
            "text": text,
        }
        if forwarded:
            msg["forward_origin"] = {"type": "channel", "chat": {"id": -100}, "message_id": 99}
        return msg

    def test_plain_text_signal_accepted(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(
                signal_id=1, success=True, research_label="MIXED_CONTEXT",
            )
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(
                    self._message(EXPLICIT_LONG), postgres=False,
                )
        self.assertIsNotNone(result.reply_text)
        self.assertIn("SIGNAL ACCEPTED", result.reply_text)
        self.assertIn("SUI", result.reply_text)

    def test_forwarded_signal_accepted(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True)
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(
                    self._message(EXPLICIT_LONG, forwarded=True), postgres=False,
                )
        self.assertIn("SIGNAL ACCEPTED", result.reply_text or "")

    def test_unauthorized_chat_rejected(self) -> None:
        msg = self._message(EXPLICIT_LONG)
        msg["chat"]["id"] = 99999
        result = process_telegram_message(msg, postgres=False)
        self.assertTrue(result.unauthorized)

    def test_duplicate_telegram_message_ignored(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        with agent_connection(f"sqlite:///{self.db_path}") as conn:
            apply_migrations(conn, postgres=False)
            ingest_from_telegram(
                conn, raw_text=EXPLICIT_LONG, chat_id=12345, message_id=42,
            )
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal"):
            r1 = process_telegram_message(self._message(EXPLICIT_LONG, msg_id=42), postgres=False)
            r2 = process_telegram_message(self._message(EXPLICIT_LONG, msg_id=42), postgres=False)
        self.assertIn("duplicate", (r2.reply_text or "").lower())

    def test_review_not_snapshotted(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            result = process_telegram_message(
                self._message(MARKET_REVIEW), postgres=False,
            )
        mock_snap.assert_not_called()
        self.assertIn("MESSAGE RECEIVED", result.reply_text or "")
        self.assertIn("MARKET_REVIEW", result.reply_text or "")

    def test_explicit_signal_triggers_snapshot(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True)
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                process_telegram_message(self._message(EXPLICIT_LONG), postgres=False)
        mock_snap.assert_called_once()

    def test_snapshot_failure_preserves_stage1(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(
                signal_id=1, success=False, error="api down",
            )
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(self._message(EXPLICIT_LONG), postgres=False)
        with agent_connection(f"sqlite:///{self.db_path}") as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_signals").fetchone()["n"]
        self.assertEqual(n, 1)
        self.assertIn("SIGNAL ACCEPTED", result.reply_text or "")
        self.assertIn("failed", (result.reply_text or "").lower())

    def test_reply_no_trading_recommendation(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True, research_label="MIXED_CONTEXT")
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(self._message(EXPLICIT_LONG), postgres=False)
        text = (result.reply_text or "").lower()
        self.assertNotIn("buy now", text)
        self.assertIn("no order placed", text)

    def test_token_never_logged(self) -> None:
        with self.assertLogs("bot.research.futures_agent.telegram_inbound", level="INFO") as cm:
            process_telegram_message(
                {**self._message("x"), "chat": {"id": 99999}},
                postgres=False,
            )
        joined = " ".join(cm.output)
        self.assertNotIn("test-token", joined)

    def test_no_execution_imports(self) -> None:
        from bot.research.futures_agent import telegram_inbound
        src = Path(telegram_inbound.__file__).read_text()
        self.assertNotIn("bot.execution", src)
        self.assertNotIn("bot.main", src)

    def test_config_fail_fast_missing_token(self) -> None:
        os.environ.pop("TELEGRAM_BOT_TOKEN")
        with self.assertRaises(TelegramInboundConfigError):
            require_telegram_inbound_config()

    def test_ingest_preserves_raw_text(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        text = "  SUI LONG\nEntry: 2.14  "
        with agent_connection(f"sqlite:///{self.db_path}") as conn:
            apply_migrations(conn, postgres=False)
            ing = ingest_from_telegram(
                conn, raw_text=text, chat_id=1, message_id=7,
            )
            row = conn.execute(
                "SELECT raw_text, input_type FROM futures_agent_inputs WHERE id = ?",
                (ing.input_id,),
            ).fetchone()
        self.assertEqual(row["raw_text"], text)
        self.assertEqual(row["input_type"], INPUT_TYPE_TELEGRAM)

    def test_extract_forward_origin(self) -> None:
        msg = self._message("hi", forwarded=True)
        origin = extract_forward_origin(msg)
        self.assertIsNotNone(origin)

    def test_rejected_format(self) -> None:
        from bot.research.futures_agent.pipeline import ProcessResult
        txt = format_telegram_rejected(ProcessResult(
            input_id=1, signal_id=None, parse_status="FAILED",
            processing_status="rejected", passes_gate=False,
            taxonomy="MARKET_REVIEW", gate_reason="not a signal",
        ))
        self.assertIn("MARKET_REVIEW", txt)


if __name__ == "__main__":
    unittest.main()
