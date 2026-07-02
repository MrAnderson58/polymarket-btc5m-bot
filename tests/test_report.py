"""Smoke tests for unified analytics report."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
)
from bot.report.builder import build_report
from bot.report.memory import set_reports_root
from bot.report.render import render_json, render_markdown, write_report_files


class ReportSmokeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.reports_dir = Path(self._tmpdir.name) / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        set_reports_root(self.reports_dir)
        init_db(self.db_path)

    def tearDown(self) -> None:
        set_reports_root(None)
        self._tmpdir.cleanup()

    def _seed_closed_trade(self, conn) -> None:
        now = int(time.time())
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-report",
            window_start_ts=now - 120,
            end_ts=now + 180,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.38,
            entry_ts=now - 60,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=0.42,
            exit_reason="TRAILING_STOP",
            pnl_percent=10.5,
            pnl_usdc=1.0,
            holding_time_seconds=45.0,
        )
        checked_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            """
            INSERT INTO market_checks (
                market_slug, seconds_remaining, strike_price, btc_price,
                yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "btc-updown-5m-report",
                100.0,
                100_000.0,
                100_000.0,
                0.5,
                0.51,
                0.42,
                0.43,
                None,
                checked_at,
            ),
        )

    def test_build_report_and_write_files(self) -> None:
        from bot.optimizer.cache import save_optimizer_cache
        from bot.strategy_review.cache import save_strategy_review_cache

        cache_dir = Path(self._tmpdir.name) / "optimizer_cache"
        review_dir = Path(self._tmpdir.name) / "strategy_review_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)

        default_opt = {
            "meta": {"generated_at": "test"},
            "parameter_optimizer": {
                "current": {"entry": 0.40, "stop_pct": -10, "trailing_activation": 0.03},
                "optimal": {"entry": 0.39, "stop_pct": -15, "trailing_activation": 0.03},
                "expected_improvement_pct": 5.0,
                "entry_significance": [],
            },
            "walk_forward": {"rows": [], "trend": "insufficient_data"},
            "heatmaps": {},
            "sensitivity_analysis": {"parameters": []},
        }
        with mock.patch("bot.optimizer.cache.CACHE_DIR", cache_dir):
            with mock.patch("bot.optimizer.cache.RESULTS_PATH", cache_dir / "optimizer_results.json"):
                with mock.patch("bot.strategy_review.cache.CACHE_DIR", review_dir):
                    with mock.patch(
                        "bot.strategy_review.cache.RESULTS_PATH",
                        review_dir / "strategy_review_results.json",
                    ):
                        save_optimizer_cache(default_opt)
                        save_strategy_review_cache(
                            {
                                "version": "1.0",
                                "final_verdict": {"decision": "KEEP CURRENT SETTINGS"},
                            }
                        )
                        with connect(self.db_path) as conn:
                            self._seed_closed_trade(conn)
                            conn.commit()
                            report = build_report(conn, read_only=True)

        self.assertIn("configuration", report)
        self.assertIn("recommendations", report)
        self.assertIn("final_score", report)
        self.assertGreaterEqual(report["overall_performance"]["trades"], 1)

        md = render_markdown(report)
        self.assertIn("AI Trading Analytics Report v4", md)
        self.assertIn("EXECUTION AUDIT", md)
        self.assertIn("AI RESEARCH NOTEBOOK", md)
        self.assertIn("AI INTELLIGENCE", md)
        self.assertIn("TRADING BRAIN", md)
        self.assertIn("AI SCIENTIST", md)
        self.assertIn("STRATEGY REVIEW", md)
        self.assertIn("PARAMETER STABILITY SCORE", md)
        self.assertIn("SAFE TO CHANGE", md)
        self.assertIn("AI DECISION ENGINE", md)
        self.assertIn("FINAL ACTION PLAN", md)

        payload = json.loads(render_json(report))
        self.assertEqual(payload["meta"]["report_version"], "4.0")
        self.assertIn("parameter_optimizer", report)
        self.assertIn("ai_decision", report)
        self.assertIn("execution_audit", report)
        self.assertIn("research_notebook", report)
        self.assertIn("ai_agent", report)
        self.assertIn("intelligence", report["ai_agent"])
        self.assertIn("trading_brain", report)
        self.assertIn("scientist", report)
        self.assertIn("strategy_review", report)
        self.assertIn("safe_to_change", report)
        self.assertIn("final_action_plan", report)

        out_dir = Path(self._tmpdir.name) / "reports"
        md_path, json_path = write_report_files(report, reports_dir=out_dir)
        self.assertTrue(md_path.exists())
        self.assertTrue(json_path.exists())

    def test_report_without_optimizer_cache(self) -> None:
        from bot.strategy_review.cache import save_strategy_review_cache

        review_dir = Path(self._tmpdir.name) / "strategy_review_cache"
        review_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = Path(self._tmpdir.name) / "optimizer_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch("bot.optimizer.cache.CACHE_DIR", cache_dir):
            with mock.patch("bot.optimizer.cache.RESULTS_PATH", cache_dir / "missing.json"):
                with mock.patch("bot.strategy_review.cache.CACHE_DIR", review_dir):
                    with mock.patch(
                        "bot.strategy_review.cache.RESULTS_PATH",
                        review_dir / "strategy_review_results.json",
                    ):
                        save_strategy_review_cache(
                            {
                                "version": "1.0",
                                "final_verdict": {"decision": "KEEP CURRENT SETTINGS"},
                            }
                        )
                        with connect(self.db_path) as conn:
                            self._seed_closed_trade(conn)
                            conn.commit()
                            report = build_report(conn, read_only=True)

        self.assertFalse(report.get("optimizer_cache_available", True))
        self.assertIn("optimizer_cache_note", report)
        md = render_markdown(report)
        self.assertIn("Optimizer cache unavailable", md)
        self.assertIn("python -m bot.daily", md)


if __name__ == "__main__":
    unittest.main()
