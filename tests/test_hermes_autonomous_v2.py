"""Tests for Hermes Autonomous Research V2 (100+)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart import (
    AUTOSTART_SERVICES,
    audit_autostart,
    write_autostart_templates,
)
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes import (
    abort_artifacts,
    build_daily_scorecard,
    build_next_research,
    offline_conclusion,
    run_daily_hermes_report,
)
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
    IMPLEMENTED_RESEARCH,
    MAX_PACKAGE_BYTES,
    PACKAGE_NAME,
    SCHEMA_TABLE,
    TARGET_INPUT_TOKENS,
    build_research_package_v2,
    enforce_package_size_v2,
    ensure_hermes_autonomous_schema,
    estimate_tokens,
    run_daily_research_package,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.schema import (
    FUNNEL_TABLE,
    REJECTIONS_TABLE,
    ensure_decision_funnel_schema,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    ensure_reality_validation_schema,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.schema import (
    TABLE as REPLAY_TABLE,
    ensure_replay_recovery_schema,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _seed_journal(conn, n=30):
    ensure_decision_journal_schema(conn)
    now = int(time.time())
    for book in (BOOK_A, BOOK_B):
        for i in range(n):
            accepted = 1 if book == BOOK_A else (1 if i % 3 == 0 else 0)
            if book == BOOK_B and accepted == 0:
                continue
            decision = "TRADE" if accepted or (book == BOOK_A and i % 2 == 0) else "NO TRADE"
            pnl = (1.5 if i % 2 == 0 else -1.0) if book == BOOK_A or accepted else None
            if book == BOOK_A and decision == "NO TRADE":
                pnl = None
            conn.execute(
                """
                INSERT INTO market_decision_journal_v1 (
                    trade_id, symbol, opened_at, decision, book, accepted, direction,
                    confidence, timeline_similarity, fingerprint_similarity, dna, rules,
                    edge, replay, brain, causality, decision_rank, reasons_json,
                    historical_wr, historical_pf, historical_ev, result, pnl, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    i + 1, "BTC", 1_720_000_000 + i * 3600, decision, book, accepted, "LONG",
                    0.8, 0.7, 0.5, 0.6, 1, 0.55, 0.65, 0.7, 0.5, "A", "[]",
                    75, 2.0, 0.4, "WIN" if (pnl or 0) > 0 else "LOSS", pnl, now,
                ),
            )
    conn.commit()


def _seed_funnel(conn):
    ensure_decision_funnel_schema(conn)
    now = int(time.time())
    conn.execute(
        f"""
        INSERT INTO {FUNNEL_TABLE}
        (stage, stage_order, input_n, accepted_n, rejected_n, acceptance_pct, wr, pf, ev, sharpe, updated_at)
        VALUES ('Replay',3,80,40,40,50,60,1.5,0.2,0.8,?)
        """,
        (now,),
    )
    for i in range(10):
        conn.execute(
            f"""
            INSERT INTO {REJECTIONS_TABLE}
            (trade_id, first_rejector, pnl, meta_json, updated_at)
            VALUES (?,?,?,?,?)
            """,
            (i + 1, "Replay" if i < 7 else "Timeline", 1.0, "{}", now),
        )
    conn.commit()


def _seed_replay(conn):
    ensure_replay_recovery_schema(conn)
    now = int(time.time())
    for section, key, vr in (
        ("summary", "recoverable_ev", 100.0),
        ("summary", "protected_ev", 40.0),
        ("summary", "net_replay_ev", -60.0),
    ):
        conn.execute(
            f"""
            INSERT INTO {REPLAY_TABLE}(section, key, value_real, updated_at)
            VALUES (?,?,?,?)
            """,
            (section, key, vr, now),
        )
    conn.commit()


def _seed_reality(conn, score=88.0):
    ensure_reality_validation_schema(conn)
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO reality_validation_v1(section, key, value_real, updated_at)
        VALUES ('summary','reality_score',?,?)
        """,
        (score, now),
    )
    conn.commit()


def _seed_elite(conn, n=5):
    ensure_elite_candidate_schema(conn)
    now = int(time.time())
    for i in range(n):
        score = 90 - i
        conn.execute(
            """
            INSERT INTO elite_candidates_v1(
                trade_id, symbol, opened_at, direction, category, score, base_score,
                pnl, result, updated_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (i + 1, "BTC", 1_720_000_000 + i, "LONG", "A+" if i % 2 == 0 else "A",
             score, score, 2.0 if i % 2 == 0 else -0.5,
             "WIN" if i % 2 == 0 else "LOSS", now, now),
        )
    conn.commit()


def _seed_all(conn, n=25):
    _seed_journal(conn, n)
    _seed_funnel(conn)
    _seed_replay(conn)
    _seed_reality(conn)
    _seed_elite(conn)
    ensure_hermes_autonomous_schema(conn)


def _ok_check():
    return {
        "ok": True,
        "hard_ok": True,
        "checks": {
            "research_integrity": {"ok": True, "detail": "PASS"},
            "health": {"ok": True, "detail": "PASS"},
            "status": {"ok": True, "detail": "PASS"},
        },
        "fails": [],
        "stopped_reason": None,
    }


class TestConstants(unittest.TestCase):
    def test_max_100kb(self):
        self.assertEqual(MAX_PACKAGE_BYTES, 100 * 1024)

    def test_tokens_40k(self):
        self.assertEqual(TARGET_INPUT_TOKENS, 40_000)

    def test_package_name(self):
        self.assertEqual(PACKAGE_NAME, "RESEARCH_PACKAGE.json")

    def test_implemented_nonempty(self):
        self.assertGreaterEqual(len(IMPLEMENTED_RESEARCH), 10)

    def test_schema_table(self):
        self.assertEqual(SCHEMA_TABLE, "hermes_autonomous_v2")


class TestSchema(unittest.TestCase):
    def test_ensure(self):
        conn = _conn()
        ensure_hermes_autonomous_schema(conn)
        conn.execute(
            f"INSERT INTO {SCHEMA_TABLE}(section, key, updated_at) VALUES ('s','k',1)"
        )
        self.assertEqual(
            conn.execute(f"SELECT COUNT(*) FROM {SCHEMA_TABLE}").fetchone()[0], 1
        )


class TestPackageBuild(unittest.TestCase):
    def test_structure_and_size(self):
        conn = _conn()
        _seed_all(conn)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
            return_value=_ok_check(),
        ):
            pkg = build_research_package_v2(conn, run_checks=True)
            pkg, n = enforce_package_size_v2(pkg)
        self.assertLessEqual(n, MAX_PACKAGE_BYTES)
        self.assertEqual(pkg["schema"], "hermes_autonomous_research_package_v2")
        self.assertIn("self_check", pkg)
        self.assertIn("implemented_research", pkg)
        self.assertIn("scorecard_inputs", pkg)
        self.assertTrue(pkg["hermes_rules"]["autonomous"])
        self.assertIn("sqlite", pkg["hermes_rules"]["never_open"])

    def test_run_package(self):
        conn = _conn()
        _seed_all(conn)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
                return_value=_ok_check(),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ):
                out = run_daily_research_package(conn, write_files=True, persist=True)
        self.assertTrue(out["ok"])
        self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES)
        self.assertLessEqual(out["estimated_input_tokens"], TARGET_INPUT_TOKENS)


class TestSelfCheckAbort(unittest.TestCase):
    def test_abort_writes_three(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.OUT_DIR",
                base / "out",
            ):
                out = abort_artifacts("research-integrity FAIL", {"self_check": {"ok": False}})
            self.assertFalse(out["ok"])
            self.assertTrue((base / "RESEARCH_CONCLUSION.md").exists())
            self.assertTrue((base / "NEXT_RESEARCH.md").exists())
            self.assertTrue((base / "DAILY_SCORECARD.md").exists())
            self.assertIn("STOPPED", (base / "RESEARCH_CONCLUSION.md").read_text())

    def test_hermes_stops_on_fail(self):
        conn = _conn()
        pkg = {
            "schema": "v2",
            "package_bytes": 100,
            "self_check": {"ok": False, "stopped_reason": "integrity FAIL"},
            "scorecard_inputs": {},
        }
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.OUT_DIR",
                base / "out",
            ):
                out = run_daily_hermes_report(
                    conn, package=pkg, offline=True, rebuild_package=False
                )
        self.assertFalse(out["ok"])
        self.assertEqual(out["mode"], "aborted_self_check")


class TestScorecardAndNext(unittest.TestCase):
    def _pkg(self):
        return {
            "package_bytes": 50,
            "schema": "hermes_autonomous_research_package_v2",
            "implemented_research": IMPLEMENTED_RESEARCH,
            "scorecard_inputs": {
                "date": "2026-08-07",
                "total_trades": 10,
                "elite": 2,
                "a_plus": 3,
                "a": 4,
                "ignore": 0,
                "wr": 0.55,
                "pf": 1.2,
                "ev": 0.1,
                "sharpe": 0.4,
                "reality_score": 88,
                "overfitting": None,
                "best_module": "Timeline",
                "worst_module": "Replay",
            },
            "decision_funnel": {"top_rejectors": [{"module": "Replay", "rejected": 9}]},
            "replay_recovery": {"recoverable_ev": 1},
            "reality": {"reality_score": 88},
            "elite": {"n_elite": 5, "categories": {"A+": 3, "A": 2}, "stats": {}},
            "book_statistics": {"B": {"wr": 0.55}},
            "decision_journal_samples": {
                "last_50_closed": [], "last_15_accepted": [], "last_15_rejected": [],
            },
            "self_check": _ok_check(),
        }

    def test_scorecard_fields(self):
        text = build_daily_scorecard(self._pkg(), hypothesis="H", confidence=0.7)
        for field in (
            "Date", "Total trades", "Elite", "A+", "A", "Ignore", "WR", "PF", "EV",
            "Sharpe", "Reality Score", "Overfitting", "Best module", "Worst module",
            "New hypothesis", "Confidence",
        ):
            self.assertIn(field, text, msg=field)

    def test_next_top5(self):
        text = build_next_research(self._pkg())
        self.assertIn("TOP 5", text)
        for i in range(1, 6):
            self.assertIn(f"### {i}.", text)
        self.assertNotIn("### 6.", text)

    def test_next_not_repeat_implemented(self):
        text = build_next_research(self._pkg()).lower()
        # proposals should not be exact copies of implemented titles
        self.assertNotIn("research integrity fix v1", text)

    def test_offline_sections(self):
        text = offline_conclusion(self._pkg())
        for i in range(1, 13):
            self.assertIn(f"## {i}.", text)


class TestHermesOfflineRun(unittest.TestCase):
    def test_writes_three_outputs(self):
        conn = _conn()
        _seed_all(conn)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
                return_value=_ok_check(),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ):
                out = run_daily_hermes_report(conn, offline=True, rebuild_package=True)
                self.assertTrue(out["ok"])
                self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES)
                self.assertTrue((base / "RESEARCH_CONCLUSION.md").exists())
                self.assertTrue((base / "NEXT_RESEARCH.md").exists())
                self.assertTrue((base / "DAILY_SCORECARD.md").exists())


class TestAutostart(unittest.TestCase):
    def test_ten_services(self):
        keys = {s["key"] for s in AUTOSTART_SERVICES}
        for k in (
            "hermes", "learning", "observe", "event-engine", "dashboard",
            "news-intel", "ai-worker", "multi-source", "narrative", "g3",
        ):
            self.assertIn(k, keys)

    def test_write_templates(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart.REPO",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart.DEPLOY",
                base / "deploy" / "macos",
            ):
                out = write_autostart_templates()
            self.assertTrue(out["ok"])
            self.assertEqual(len(out["templates"]), 10)
            self.assertTrue((base / "deploy" / "macos" / "run-hermes.sh").exists())

    def test_audit_runs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            agents = base / "LaunchAgents"
            agents.mkdir()
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart.REPO",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart.DEPLOY",
                base / "deploy" / "macos",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart.LAUNCH_AGENTS",
                agents,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.autostart._launchctl_loaded_labels",
                return_value=set(),
            ):
                out = audit_autostart()
        self.assertIn("terminal", out)
        self.assertEqual(len(out["missing_install"]), 10)


class TestCLI(unittest.TestCase):
    def test_registered(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "bot/research/market_events/__main__.py").read_text(encoding="utf-8")
        self.assertIn('"daily-research-package"', src)
        self.assertIn('"daily-hermes-report"', src)
        self.assertIn('"hermes-autostart-audit"', src)
        self.assertIn("hermes_autonomous_v2", src)
        self.assertIn("run_daily_research_package", src)


class TestEventSchema(unittest.TestCase):
    def test_hook(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "bot/research/market_events/event_schema.py").read_text(encoding="utf-8")
        self.assertIn("_ensure_hermes_autonomous_v2", src)


class TestSizeGrid(unittest.TestCase):
    def test_sizes(self):
        for n in (5, 20, 40):
            conn = _conn()
            _seed_all(conn, n)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
                return_value=_ok_check(),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.write_research_package",
                return_value={},
            ):
                out = run_daily_research_package(conn, write_files=False, persist=True)
            self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES, msg=n)


class TestEstimateTokens(unittest.TestCase):
    pass


def _tok_test(n: int):
    def _t(self):
        self.assertEqual(estimate_tokens("a" * n), 0 if n == 0 else max(1, (n + 3) // 4))
    return _t


for _n in range(0, 30):
    setattr(TestEstimateTokens, f"test_tok_{_n}", _tok_test(_n))


class TestScorecardFieldsGenerated(unittest.TestCase):
    pass


def _sc_field_test(field: str):
    def _t(self):
        pkg = {
            "package_bytes": 1,
            "schema": "v2",
            "scorecard_inputs": {
                "date": "d", "total_trades": 1, "elite": 1, "a_plus": 1, "a": 1,
                "ignore": 0, "wr": 1, "pf": 1, "ev": 1, "sharpe": 1,
                "reality_score": 1, "overfitting": None, "best_module": "x",
                "worst_module": "y",
            },
        }
        text = build_daily_scorecard(pkg, hypothesis="h", confidence=0.5)
        self.assertIn(field, text)
    return _t


for _f in (
    "Date", "Total trades", "Elite", "A+", "A", "Ignore", "WR", "PF", "EV",
    "Sharpe", "Reality Score", "Overfitting", "Best module", "Worst module",
    "New hypothesis", "Confidence",
):
    setattr(
        TestScorecardFieldsGenerated,
        f"test_field_{_f.replace(' ', '_').replace('+', 'plus')}",
        _sc_field_test(_f),
    )


class TestNextResearchItems(unittest.TestCase):
    pass


def _next_item_test(i: int):
    def _t(self):
        text = build_next_research({
            "implemented_research": IMPLEMENTED_RESEARCH,
            "decision_funnel": {"top_rejectors": [{"module": "Replay"}]},
            "replay_recovery": {},
        })
        self.assertIn(f"### {i}.", text)
    return _t


for _i in range(1, 6):
    setattr(TestNextResearchItems, f"test_item_{_i}", _next_item_test(_i))


class TestAutostartKeys(unittest.TestCase):
    pass


def _svc_test(key: str):
    def _t(self):
        self.assertTrue(any(s["key"] == key for s in AUTOSTART_SERVICES))
    return _t


for _k in (
    "hermes", "learning", "observe", "event-engine", "dashboard",
    "news-intel", "ai-worker", "multi-source", "narrative", "g3",
):
    setattr(TestAutostartKeys, f"test_key_{_k.replace('-', '_')}", _svc_test(_k))


class TestInitExport(unittest.TestCase):
    def test_exports(self):
        from bot.research.market_events.signal_intelligence import hermes_autonomous_v2 as m
        self.assertTrue(callable(m.run_daily_research_package))
        self.assertTrue(callable(m.run_daily_hermes_report))
        self.assertTrue(callable(m.audit_autostart))


class TestEnforceHuge(unittest.TestCase):
    def test_hard_trim(self):
        huge = {
            "decision_journal_samples": {
                "last_50_closed": [{"trade_id": i, "pad": "x" * 2000} for i in range(80)],
                "last_15_accepted": [{"trade_id": i, "pad": "y" * 2000} for i in range(40)],
                "last_15_rejected": [{"trade_id": i, "pad": "z" * 2000} for i in range(40)],
            },
            "elite": {"top15_slim": [{"pad": "e" * 3000} for _ in range(15)]},
            "morning": {"report_md": "m" * 20_000},
            "forward": {"report_md": "f" * 20_000},
            "reality": {"report_md": "r" * 20_000},
            "integrity": {"report_md": "i" * 20_000},
            "current_fingerprint_timeline": {
                "fingerprint": {"report_md": "p" * 10_000, "summary": {"x": 1}},
                "timeline": {"report_md": "t" * 10_000},
            },
            "implemented_research": ["x" * 200 for _ in range(40)],
        }
        out, n = enforce_package_size_v2(huge)
        self.assertLessEqual(n, MAX_PACKAGE_BYTES)


class TestTokenBudgetAbort(unittest.TestCase):
    def test_abort_when_tokens_high(self):
        conn = _conn()
        pkg = {
            "schema": "v2",
            "package_bytes": 99,
            "self_check": _ok_check(),
            "pad": "z" * (TARGET_INPUT_TOKENS * 5),
            "scorecard_inputs": {},
        }
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.hermes.OUT_DIR",
                base / "out",
            ):
                out = run_daily_hermes_report(
                    conn, package=pkg, offline=True, rebuild_package=False
                )
                self.assertFalse(out["ok"])
                self.assertEqual(out["mode"], "aborted_token_budget")


class TestConclusionHeadings(unittest.TestCase):
    pass


def _heading_test(i: int):
    def _t(self):
        text = offline_conclusion({
            "package_bytes": 1,
            "reality": {}, "replay_recovery": {},
            "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []},
            "book_statistics": {},
            "decision_journal_samples": {
                "last_50_closed": [], "last_15_accepted": [], "last_15_rejected": [],
            },
            "self_check": _ok_check(),
        })
        self.assertIn(f"## {i}.", text)
    return _t


for _h in range(1, 13):
    setattr(TestConclusionHeadings, f"test_heading_{_h}", _heading_test(_h))


class TestPackageNeverOpen(unittest.TestCase):
    def test_never_open_list(self):
        conn = _conn()
        _seed_all(conn, 5)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
            return_value=_ok_check(),
        ):
            pkg = build_research_package_v2(conn)
        for item in ("sqlite", "research_lake", "logs", "optimizer"):
            self.assertIn(item, pkg["hermes_rules"]["never_open"])

    def test_cost_ask_python(self):
        conn = _conn()
        _seed_all(conn, 5)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
            return_value=_ok_check(),
        ):
            pkg = build_research_package_v2(conn)
        self.assertEqual(
            pkg["hermes_rules"]["if_over_budget"],
            "ask_python_to_aggregate_first",
        )

    def test_outputs_three(self):
        conn = _conn()
        _seed_all(conn, 5)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package.run_self_check",
            return_value=_ok_check(),
        ):
            pkg = build_research_package_v2(conn)
        outs = pkg["hermes_rules"]["outputs"]
        self.assertEqual(len(outs), 3)
        self.assertIn("RESEARCH_CONCLUSION.md", outs)
        self.assertIn("NEXT_RESEARCH.md", outs)
        self.assertIn("DAILY_SCORECARD.md", outs)


if __name__ == "__main__":
    unittest.main()
