"""Tests for operational tooling — no secrets, no execution imports."""

from __future__ import annotations

import ast
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import init_db, insert_v4_shadow_observation
from bot.ops.collector_health import analyze_collector_health
from bot.ops.healthcheck import run_healthcheck
from bot.ops.process_utils import (
    ProcessInfo,
    assess_telegram_lock,
    find_main_bot_processes,
    project_python,
    remove_stale_telegram_lock,
    stop_processes,
)
from bot.ops.redact import is_secret_env_name, redact_database_url, redact_text
from bot.ops.snapshot import generate_snapshot_text


class OpsRedactTestCase(unittest.TestCase):
    def test_secret_env_detection(self) -> None:
        self.assertTrue(is_secret_env_name("TELEGRAM_BOT_TOKEN"))
        self.assertTrue(is_secret_env_name("POLY_PRIVATE_KEY"))
        self.assertFalse(is_secret_env_name("POLL_INTERVAL_SEC"))

    def test_redact_database_url(self) -> None:
        url = "postgresql://user:secretpass@localhost:5432/trading_ai"
        redacted = redact_database_url(url)
        self.assertNotIn("secretpass", redacted)
        self.assertIn("trading_ai", redacted)

    def test_redact_text(self) -> None:
        raw = "TELEGRAM_BOT_TOKEN=1234567890:ABCDEF"
        out = redact_text(raw)
        self.assertNotIn("1234567890", out)


class OpsHealthcheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_healthcheck_no_secret_values(self) -> None:
        env = {
            "DATABASE_PATH": str(self.db_path),
            "TELEGRAM_BOT_TOKEN": "123456789:SUPER_SECRET_TOKEN_VALUE",
            "POLY_PRIVATE_KEY": "0xdeadbeef",
            "FUTURES_AGENT_DATABASE_URL": "postgresql://u:pass@localhost/trading_ai",
            "ENABLE_V4_SHADOW": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "bot.ops.healthcheck.find_main_bot_processes", return_value=[]
            ), mock.patch(
                "bot.ops.healthcheck.find_telegram_poll_processes", return_value=[]
            ), mock.patch(
                "bot.ops.healthcheck._webhook_status_safe", return_value="inactive"
            ), mock.patch(
                "bot.ops.healthcheck._check_postgres", return_value=(True, "trading_ai")
            ), mock.patch(
                "bot.ops.healthcheck.resolve_agent_db_config"
            ) as mock_cfg:
                from bot.research.futures_agent.env_bootstrap import AgentDbConfig

                mock_cfg.return_value = AgentDbConfig(
                    backend="postgresql",
                    url="postgresql://u:pass@localhost/trading_ai",
                    database_name="trading_ai",
                    sqlite_path=None,
                    config_source="test",
                    postgres_url_configured=True,
                )
                report = run_healthcheck()
        blob = "\n".join(report.lines)
        self.assertNotIn("SUPER_SECRET_TOKEN_VALUE", blob)
        self.assertNotIn("0xdeadbeef", blob)
        self.assertNotIn("pass@", blob)
        self.assertIn("TELEGRAM_BOT_TOKEN: configured=True", blob)

    def test_density_warning_thresholds(self) -> None:
        window_start = 1_900_000_000
        slug = f"btc-updown-5m-{window_start}"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for i in range(30):
                insert_v4_shadow_observation(
                    conn,
                    market_slug=slug,
                    window_start_ts=window_start,
                    timestamp=window_start + i * 10,
                    seconds_from_start=min(299, i * 10),
                    seconds_left=max(0, 300 - i * 10),
                    btc_price=100_000.0,
                    strike=100_000.0,
                    delta=0.0,
                    yes_bid=0.55,
                    yes_ask=0.56,
                    no_bid=0.38,
                    no_ask=0.39,
                    trend_score=None,
                    trend_side=None,
                    spread=0.01,
                )
            conn.commit()
            health = analyze_collector_health(conn, completed_limit=5)
        self.assertTrue(any("median gap" in w for w in health.warnings))
        self.assertTrue(any("obs/market" in w for w in health.warnings))


class OpsProcessUtilsTestCase(unittest.TestCase):
    def test_duplicate_detection(self) -> None:
        procs = [
            ProcessInfo(1, "python -m bot.main"),
            ProcessInfo(2, "python -m bot.main"),
        ]
        with mock.patch(
            "bot.ops.healthcheck.find_main_bot_processes", return_value=procs
        ), mock.patch(
            "bot.ops.healthcheck.find_telegram_poll_processes", return_value=[]
        ), mock.patch(
            "bot.ops.healthcheck._webhook_status_safe", return_value="inactive"
        ), mock.patch(
            "bot.ops.healthcheck.bootstrap_config"
        ), mock.patch(
            "bot.ops.healthcheck.connect"
        ), mock.patch(
            "bot.ops.healthcheck.resolve_agent_db_config"
        ) as mock_cfg:
            from bot.research.futures_agent.env_bootstrap import AgentDbConfig

            mock_cfg.return_value = AgentDbConfig(
                backend="sqlite",
                url="sqlite:///tmp/x.db",
                database_name=None,
                sqlite_path="/tmp/x.db",
                config_source="test",
                postgres_url_configured=False,
            )
            report = run_healthcheck()
        self.assertEqual(report.status, "CRITICAL")
        self.assertTrue(any("duplicate" in i for i in report.issues))

    def test_project_python_uses_venv(self) -> None:
        py = project_python()
        self.assertIn(".venv", str(py))

    def test_graceful_stop_not_running(self) -> None:
        ok, lines = stop_processes([], label="bot.main")
        self.assertTrue(ok)
        self.assertTrue(any("not running" in line for line in lines))

    def test_stale_lock_without_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "futures_agent_telegram_poll.lock"
            with mock.patch("bot.ops.process_utils.telegram_lock_path", return_value=lock):
                self.assertEqual(assess_telegram_lock(), "no_lock_file")
                ok, msg = remove_stale_telegram_lock()
                self.assertTrue(ok)


class OpsSnapshotTestCase(unittest.TestCase):
    def test_snapshot_redaction(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "999:SECRET_SNAPSHOT_TOKEN"},
            clear=False,
        ), mock.patch("bot.ops.snapshot.bootstrap_config"), mock.patch(
            "bot.ops.snapshot.read_git_status"
        ), mock.patch(
            "bot.ops.snapshot.find_main_bot_processes", return_value=[]
        ), mock.patch(
            "bot.ops.snapshot.find_telegram_poll_processes", return_value=[]
        ), mock.patch(
            "bot.ops.snapshot.assess_telegram_lock", return_value="no_lock_file"
        ), mock.patch(
            "bot.ops.snapshot.connect"
        ), mock.patch(
            "bot.ops.snapshot.resolve_agent_db_config"
        ) as mock_cfg, mock.patch(
            "bot.ops.snapshot.run_healthcheck"
        ) as mock_health, mock.patch(
            "bot.ops.snapshot._futures_agent_counts", return_value=[]
        ):
            from bot.ops.git_info import GitStatus
            from bot.research.futures_agent.env_bootstrap import AgentDbConfig

            mock_cfg.return_value = AgentDbConfig(
                backend="postgresql",
                url="postgresql:///trading_ai",
                database_name="trading_ai",
                sqlite_path=None,
                config_source="test",
                postgres_url_configured=True,
            )
            mock_health.return_value = mock.Mock(status="DEGRADED", issues=[])
            with mock.patch(
                "bot.ops.snapshot.read_git_status",
                return_value=GitStatus("main", "abc", False, []),
            ):
                text = generate_snapshot_text()
        self.assertNotIn("SECRET_SNAPSHOT_TOKEN", text)


class OpsImportSafetyTestCase(unittest.TestCase):
    def test_healthcheck_no_execution_import(self) -> None:
        source = Path(__file__).resolve().parents[1] / "bot" / "ops" / "healthcheck.py"
        tree = ast.parse(source.read_text())
        imports = {
            node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("bot.execution", imports)

    def test_ops_modules_importable(self) -> None:
        importlib.import_module("bot.ops.healthcheck")
        importlib.import_module("bot.ops.snapshot")
        importlib.import_module("bot.ops.prod_control")


if __name__ == "__main__":
    unittest.main()
