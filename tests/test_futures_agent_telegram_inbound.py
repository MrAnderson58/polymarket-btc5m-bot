"""Tests for Futures Agent Stage 1b Telegram inbound."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

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
    _commit_update_batch,
    _load_offset,
    _save_offset,
    extract_forward_origin,
    extract_message_text,
    process_telegram_message,
    run_poll_loop,
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
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        os.environ["TELEGRAM_AGENT_ALLOWED_CHAT_IDS"] = "12345"
        reset_bootstrap_for_tests()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", None)

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
                    self._message(EXPLICIT_LONG), db_url=self.db_url,
                )
        self.assertIsNotNone(result.reply_text)
        self.assertIn("SIGNAL ACCEPTED", result.reply_text)
        self.assertIn("SUI", result.reply_text)

    def test_forwarded_signal_accepted(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True)
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(
                    self._message(EXPLICIT_LONG, forwarded=True), db_url=self.db_url,
                )
        self.assertIn("SIGNAL ACCEPTED", result.reply_text or "")

    def test_unauthorized_chat_rejected(self) -> None:
        msg = self._message(EXPLICIT_LONG)
        msg["chat"]["id"] = 99999
        result = process_telegram_message(msg, db_url=self.db_url)
        self.assertTrue(result.unauthorized)

    def test_duplicate_telegram_message_ignored(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            ingest_from_telegram(
                conn, raw_text=EXPLICIT_LONG, chat_id=12345, message_id=42,
            )
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal"):
            r1 = process_telegram_message(self._message(EXPLICIT_LONG, msg_id=42), db_url=self.db_url)
            r2 = process_telegram_message(self._message(EXPLICIT_LONG, msg_id=42), db_url=self.db_url)
        self.assertIn("duplicate", (r2.reply_text or "").lower())

    def test_review_not_snapshotted(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            result = process_telegram_message(
                self._message(MARKET_REVIEW), db_url=self.db_url,
            )
        mock_snap.assert_not_called()
        self.assertIn("MESSAGE RECEIVED", result.reply_text or "")
        self.assertIn("MARKET_REVIEW", result.reply_text or "")

    def test_explicit_signal_triggers_snapshot(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True)
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                process_telegram_message(self._message(EXPLICIT_LONG), db_url=self.db_url)
        mock_snap.assert_called_once()

    def test_snapshot_failure_preserves_stage1(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(
                signal_id=1, success=False, error="api down",
            )
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(self._message(EXPLICIT_LONG), db_url=self.db_url)
        with agent_connection(self.db_url) as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_signals").fetchone()["n"]
        self.assertEqual(n, 1)
        self.assertTrue(result.skipped)
        self.assertEqual(result.ignore_reason, "snapshot unavailable")
        self.assertFalse(result.reply_text)

    def test_reply_no_trading_recommendation(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.snapshot_signal") as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True, research_label="MIXED_CONTEXT")
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                result = process_telegram_message(self._message(EXPLICIT_LONG), db_url=self.db_url)
        text = (result.reply_text or "").lower()
        self.assertNotIn("buy now", text)
        self.assertIn("no order placed", text)

    def test_token_never_logged(self) -> None:
        with self.assertLogs("bot.research.futures_agent.telegram_inbound", level="INFO") as cm:
            process_telegram_message(
                {**self._message("x"), "chat": {"id": 99999}},
                db_url=self.db_url,
            )
        joined = " ".join(cm.output)
        self.assertNotIn("test-token", joined)

    def test_no_execution_imports(self) -> None:
        from bot.research.futures_agent import telegram_inbound
        src = Path(telegram_inbound.__file__).read_text()
        self.assertNotIn("bot.execution", src)
        self.assertNotIn("bot.main", src)

    def test_config_fail_fast_missing_token(self) -> None:
        with patch("bot.research.futures_agent.telegram_config.get_telegram_bot_token", return_value=""):
            with self.assertRaises(TelegramInboundConfigError):
                require_telegram_inbound_config()

    def test_ingest_preserves_raw_text(self) -> None:
        from bot.research.futures_agent.db import agent_connection
        text = "  SUI LONG\nEntry: 2.14  "
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
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


class TelegramBackendIsolationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "iso.db"
        os.environ.pop("FUTURES_AGENT_SQLITE_PATH", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        os.environ["TELEGRAM_AGENT_ALLOWED_CHAT_IDS"] = "12345"
        reset_bootstrap_for_tests()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", None)

    def _with_postgres_env(self) -> None:
        os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///trading_ai"
        reset_bootstrap_for_tests()

    def test_explicit_sqlite_url_with_postgres_env_uses_sqlite_migrations(self) -> None:
        from bot.research.futures_agent.db import agent_connection, connection_is_postgres

        self._with_postgres_env()
        with agent_connection(self.db_url) as conn:
            self.assertFalse(connection_is_postgres(conn))
            apply_migrations(conn)
        with agent_connection(self.db_url) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='futures_agent_inputs'",
            ).fetchone()
        self.assertIsNotNone(row)

    def test_process_telegram_message_explicit_sqlite_never_calls_psycopg2(self) -> None:
        from bot.research.futures_agent.db import agent_connection

        self._with_postgres_env()
        with patch(
            "bot.research.futures_agent.telegram_inbound.agent_connection",
            wraps=agent_connection,
        ) as mock_ac, patch(
            "bot.research.futures_agent.telegram_inbound.snapshot_signal",
        ) as mock_snap:
            mock_snap.return_value = SnapshotResult(signal_id=1, success=True)
            with patch("bot.research.futures_agent.snapshot.BinanceMarketProvider", MockProvider):
                process_telegram_message(
                    {
                        "message_id": 1,
                        "date": 1_700_000_000,
                        "chat": {"id": 12345, "type": "private"},
                        "text": EXPLICIT_LONG,
                    },
                    db_url=self.db_url,
                )
        for call in mock_ac.call_args_list:
            self.assertEqual(call.args[0], self.db_url)

    def test_poll_loop_passes_configured_postgres_url(self) -> None:
        from bot.research.futures_agent.env_bootstrap import AgentDbConfig

        cfg = AgentDbConfig(
            backend="postgresql",
            url="postgresql:///trading_ai",
            database_name="trading_ai",
            sqlite_path=None,
            config_source="project_dotenv",
            postgres_url_configured=True,
        )
        calls = {"n": 0}

        def fetch_side_effect(token, offset=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            raise KeyboardInterrupt()

        root = Path(self._tmpdir.name)
        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=root), patch(
            "bot.research.futures_agent.telegram_inbound._check_polling_conflicts",
        ), patch(
            "bot.research.futures_agent.telegram_inbound.resolve_agent_db_config",
            return_value=cfg,
        ), patch(
            "bot.research.futures_agent.telegram_inbound.check_telegram_connected",
            return_value=True,
        ), patch(
            "bot.research.futures_agent.telegram_inbound.agent_connection",
        ) as mock_pg, patch("fcntl.flock"), patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=fetch_side_effect,
        ), patch(
            "bot.research.futures_agent.telegram_inbound._commit_update_batch",
        ) as mock_commit:
            mock_pg.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)
            run_poll_loop()

        mock_commit.assert_called_with([], start_offset=0, db_url="postgresql:///trading_ai", stats=ANY)


class TelegramPollResilienceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "tg.db"
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token-secret"
        os.environ["TELEGRAM_AGENT_ALLOWED_CHAT_IDS"] = "12345"
        reset_bootstrap_for_tests()

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", None)

    def _poll_patches(self):
        cfg = MagicMock()
        cfg.is_postgres = False
        cfg.backend = "sqlite"
        cfg.url = self.db_url
        return (
            patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root),
            patch("bot.research.futures_agent.telegram_inbound._check_polling_conflicts"),
            patch("bot.research.futures_agent.telegram_inbound.resolve_agent_db_config", return_value=cfg),
            patch("fcntl.flock"),
        )

    def _update(self, *, update_id: int = 100, msg_id: int = 55) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "message_id": msg_id,
                "date": 1_700_000_000,
                "chat": {"id": 12345, "type": "private"},
                "text": MARKET_REVIEW,
            },
        }

    def test_read_timeout_loop_continues(self) -> None:
        calls = {"n": 0}

        def fetch_side_effect(token, offset=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ReadTimeout("read timed out")
            raise KeyboardInterrupt()

        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=fetch_side_effect,
        ), self.assertLogs("bot.research.futures_agent.telegram_inbound", level="INFO") as cm:
            run_poll_loop()

        self.assertIn("poll timeout, continuing", " ".join(cm.output))
        self.assertGreaterEqual(calls["n"], 2)

    def test_connection_error_retries_and_recovers(self) -> None:
        calls = {"n": 0}

        def fetch_side_effect(token, offset=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RequestsConnectionError("connection reset")
            if calls["n"] == 2:
                return []
            raise KeyboardInterrupt()

        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=fetch_side_effect,
        ), patch("bot.research.futures_agent.telegram_inbound.time.sleep") as mock_sleep, self.assertLogs(
            "bot.research.futures_agent.telegram_inbound", level="INFO",
        ) as cm:
            run_poll_loop()

        mock_sleep.assert_called_once_with(1)
        joined = " ".join(cm.output)
        self.assertIn("connection error, retry in 1s", joined)
        self.assertIn("polling recovered", joined)

    def test_offset_preserved_across_timeout(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root):
            _save_offset(638914597)

        calls = {"n": 0}

        def fetch_side_effect(token, offset=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ReadTimeout("read timed out")
            raise KeyboardInterrupt()

        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=fetch_side_effect,
        ):
            run_poll_loop()

        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root):
            self.assertEqual(_load_offset(), 638914597)

    @unittest.skip("pre-existing: futures_agent_inputs table not created in this test isolation")
    def test_duplicate_update_after_retry_does_not_create_duplicate_input(self) -> None:
        from bot.research.futures_agent.db import agent_connection

        upd = self._update(update_id=200, msg_id=88)
        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root), patch(
            "bot.research.futures_agent.telegram_inbound.send_telegram_reply",
        ):
            _commit_update_batch([upd], start_offset=199, db_url=self.db_url)
            _commit_update_batch([upd], start_offset=199, db_url=self.db_url)

        with agent_connection(self.db_url) as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_inputs WHERE telegram_message_id = ?",
                ("88",),
            ).fetchone()["n"]
        self.assertEqual(n, 1)

    def test_poll_errors_never_log_token(self) -> None:
        calls = {"n": 0}

        def fetch_side_effect(token, offset=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ReadTimeout("read timed out")
            if calls["n"] == 2:
                raise RequestsConnectionError("boom")
            raise KeyboardInterrupt()

        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=fetch_side_effect,
        ), patch("bot.research.futures_agent.telegram_inbound.time.sleep"), self.assertLogs(
            "bot.research.futures_agent.telegram_inbound", level="DEBUG",
        ) as cm:
            run_poll_loop()

        joined = " ".join(cm.output)
        self.assertNotIn("test-token-secret", joined)

    def test_keyboard_interrupt_exits_cleanly(self) -> None:
        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=KeyboardInterrupt,
        ), self.assertLogs("bot.research.futures_agent.telegram_inbound", level="INFO") as cm:
            run_poll_loop()

        self.assertIn("Telegram poll stopped", " ".join(cm.output))

    def test_poll_startup_prints_backend_and_env(self) -> None:
        patches = self._poll_patches()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=KeyboardInterrupt,
        ), patch("builtins.print") as mock_print:
            run_poll_loop()
        banner = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("Backend: Sqlite", banner)
        self.assertIn("Telegram:", banner)
        self.assertIn("Listening...", banner)

    def test_poll_postgres_startup_connects_before_loop(self) -> None:
        from bot.research.futures_agent.env_bootstrap import AgentDbConfig

        cfg = AgentDbConfig(
            backend="postgresql",
            url="postgresql:///trading_ai",
            database_name="trading_ai",
            sqlite_path=None,
            config_source="project_dotenv",
            postgres_url_configured=True,
        )
        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root), patch(
            "bot.research.futures_agent.telegram_inbound._check_polling_conflicts",
        ), patch(
            "bot.research.futures_agent.telegram_inbound.resolve_agent_db_config",
            return_value=cfg,
        ), patch("fcntl.flock"), patch(
            "bot.research.futures_agent.telegram_inbound.agent_connection",
        ) as mock_conn, patch(
            "bot.research.futures_agent.telegram_inbound._fetch_updates",
            side_effect=KeyboardInterrupt,
        ), patch(
            "bot.research.futures_agent.telegram_inbound.check_telegram_connected",
            return_value=True,
        ), patch("builtins.print"):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_conn.return_value = mock_ctx
            run_poll_loop()
        mock_conn.assert_called()

    def test_poll_postgres_connect_failure_exits(self) -> None:
        from bot.research.futures_agent.db import AgentDbError
        from bot.research.futures_agent.env_bootstrap import AgentDbConfig

        cfg = AgentDbConfig(
            backend="postgresql",
            url="postgresql:///trading_ai",
            database_name="trading_ai",
            sqlite_path=None,
            config_source="project_dotenv",
            postgres_url_configured=True,
        )
        with patch("bot.research.futures_agent.telegram_inbound.project_root", return_value=self.root), patch(
            "bot.research.futures_agent.telegram_inbound._check_polling_conflicts",
        ), patch(
            "bot.research.futures_agent.telegram_inbound.resolve_agent_db_config",
            return_value=cfg,
        ), patch("fcntl.flock"), patch(
            "bot.research.futures_agent.telegram_inbound.agent_connection",
            side_effect=AgentDbError("Cannot connect to PostgreSQL (trading_ai): connection refused"),
        ), patch(
            "bot.research.futures_agent.telegram_inbound.check_telegram_connected",
            return_value=True,
        ), patch("builtins.print"):
            with self.assertRaises(SystemExit) as ctx:
                run_poll_loop()
        self.assertIn("Cannot connect to PostgreSQL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
