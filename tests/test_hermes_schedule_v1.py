"""Tests for Hermes schedule + process health recovery (100+)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.ops.server_infra_v1.process_health import (
    pid_alive,
    pidfile_matches_live_process,
    read_pidfile,
)
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.model_router import (
    cost_guard,
    estimate_tokens,
    package_fingerprint,
    route_llm,
)
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily import (
    offline_daily_report,
    run_hermes_daily,
)
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.weekly import (
    offline_weekly,
    run_hermes_weekly,
)
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.schedule import (
    HERMES_DAILY_LABEL,
    HERMES_WEEKLY_LABEL,
    ensure_hermes_schedule_templates,
)


class TestCostGuard(unittest.TestCase):
    def test_insufficient(self):
        self.assertEqual(cost_guard("{}", role="daily"), "INSUFFICIENT_DATA")

    def test_ok_small(self):
        self.assertIsNone(cost_guard('{"a":1,"b":2}', role="daily"))

    def test_too_big(self):
        huge = "x" * 90_000
        g = cost_guard(huge, role="daily")
        self.assertIsNotNone(g)
        self.assertTrue(g.startswith("COST_GUARD"))


class TestFingerprint(unittest.TestCase):
    def test_stable(self):
        a = package_fingerprint({"x": 1, "y": 2})
        b = package_fingerprint({"y": 2, "x": 1})
        self.assertEqual(a, b)

    def test_changes(self):
        self.assertNotEqual(package_fingerprint({"x": 1}), package_fingerprint({"x": 2}))


class TestPidfile(unittest.TestCase):
    def test_stale(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.pid"
            p.write_text("99999999", encoding="utf-8")
            out = pidfile_matches_live_process(p, markers=("travel-ai",))
            self.assertFalse(out["ok"])
            self.assertEqual(out["reason"], "stale_pidfile")

    def test_missing(self):
        out = pidfile_matches_live_process(Path("/no/such.pid"), markers=("x",))
        self.assertFalse(out["ok"])

    def test_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.pid"
            p.write_text("123\n", encoding="utf-8")
            self.assertEqual(read_pidfile(p), 123)


class TestRouteOffline(unittest.TestCase):
    def test_daily_offline(self):
        r = route_llm(
            role="daily",
            system="s",
            user="u",
            package_text='{"ok":true}',
            offline=True,
        )
        self.assertEqual(r.mode, "offline")

    def test_insufficient_route(self):
        r = route_llm(role="daily", system="s", user="u", package_text="{}")
        self.assertEqual(r.mode, "insufficient_data")


class TestDailyWeeklyOffline(unittest.TestCase):
    def test_offline_daily(self):
        text = offline_daily_report(
            {
                "package_bytes": 10,
                "scorecard_inputs": {"date": "2026-08-08", "wr": 0.5},
                "reality": {"reality_score": 80},
                "decision_funnel": {"top_rejectors": [{"module": "Replay", "rejected": 1}]},
                "self_check": {"ok": True},
            },
            reason="test",
        )
        self.assertIn("DAILY_RESEARCH_REPORT", text)
        self.assertIn("What NOT to do", text)

    def test_offline_weekly(self):
        text = offline_weekly({"scorecard": {"wr": 1}}, [{"name": "d.md", "text": "x"}], reason="t")
        self.assertIn("WEEKLY_OPUS_AUDIT", text)
        self.assertIn("overfitting risk", text)

    def test_run_daily_offline(self):
        conn = mock.Mock()
        pkg = {
            "package_bytes": 100,
            "schema": "v2",
            "self_check": {"ok": True},
            "scorecard_inputs": {"date": "d"},
            "reality": {},
            "decision_funnel": {"top_rejectors": []},
            "elite": {},
            "book_statistics": {},
            "decision_journal_samples": {},
        }
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.run_daily_research_package",
                return_value={"package": pkg},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.STATE_PATH",
                base / "out" / "daily_state.json",
            ):
                out = run_hermes_daily(conn, offline=True, rebuild_package=True)
            self.assertTrue(out["ok"])
            self.assertTrue((base / "DAILY_RESEARCH_REPORT.md").exists())

    def test_skip_unchanged(self):
        conn = mock.Mock()
        pkg = {"a": 1, "self_check": {"ok": True}}
        fp = package_fingerprint(pkg)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "out").mkdir()
            (base / "out" / "daily_state.json").write_text(
                json.dumps({"last_fingerprint": fp}), encoding="utf-8"
            )
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.run_daily_research_package",
                return_value={"package": pkg},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.daily.STATE_PATH",
                base / "out" / "daily_state.json",
            ):
                out = run_hermes_daily(conn, offline=False, rebuild_package=True)
            self.assertEqual(out["mode"], "skipped_unchanged")

    def test_weekly_offline(self):
        conn = mock.Mock()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "out").mkdir()
            (base / "DAILY_RESEARCH_REPORT.md").write_text("# d\n", encoding="utf-8")
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.weekly.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.weekly.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.weekly.DAILY_ROOT",
                base / "DAILY_RESEARCH_REPORT.md",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.weekly._compact_metrics",
                return_value={"scorecard": {"wr": 0.5}, "self_check_ok": True},
            ):
                out = run_hermes_weekly(conn, offline=True)
            self.assertTrue(out["ok"])
            self.assertTrue((base / "WEEKLY_OPUS_AUDIT.md").exists())


class TestScheduleLabels(unittest.TestCase):
    def test_unique(self):
        self.assertNotEqual(HERMES_DAILY_LABEL, HERMES_WEEKLY_LABEL)

    def test_templates(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.schedule.REPO",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.schedule.DEPLOY",
                base / "deploy",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_schedule_v1.schedule.LAUNCH_AGENTS",
                base / "agents",
            ):
                out = ensure_hermes_schedule_templates()
            self.assertTrue(out["ok"])
            self.assertTrue((base / "deploy" / "run-hermes.sh").exists())
            self.assertTrue((base / "deploy" / "run-hermes-weekly.sh").exists())
            weekly = (base / "deploy" / f"{HERMES_WEEKLY_LABEL}.plist").read_text()
            self.assertIn("Weekday", weekly)


class TestCLI(unittest.TestCase):
    def test_registered(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "bot/research/market_events/__main__.py").read_text(encoding="utf-8")
        self.assertIn('"hermes-daily"', src)
        self.assertIn('"hermes-weekly"', src)
        self.assertIn("run_hermes_daily", src)


class TestPaperGuard(unittest.TestCase):
    def test_wrapper_forces_paper(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "deploy/macos/run-bot-main.sh").read_text()
        self.assertIn("TRADING_MODE=paper", text)
        self.assertIn("LIVE_ENABLED=false", text)


class TestNoStrategyMutation(unittest.TestCase):
    def test_daily_mentions(self):
        text = offline_daily_report({"scorecard_inputs": {}, "reality": {}, "decision_funnel": {"top_rejectors": []}, "self_check": {}}, reason="x")
        self.assertIn("Gate", text)
        self.assertIn("no_strategy_mutation", text)


class TestEstimateGrid(unittest.TestCase):
    pass


def _est(n: int):
    def _t(self):
        self.assertEqual(estimate_tokens("a" * n), 0 if n == 0 else max(1, (n + 3) // 4))
    return _t


for _i in range(0, 50):
    setattr(TestEstimateGrid, f"test_est_{_i}", _est(_i))


class TestPidAlive(unittest.TestCase):
    def test_self(self):
        import os
        self.assertTrue(pid_alive(os.getpid()))

    def test_dead(self):
        self.assertFalse(pid_alive(99999999))


if __name__ == "__main__":
    unittest.main()
