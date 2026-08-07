"""Tests for Hermes Daily Research Pipeline V1 (100+)."""

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
from bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes import (
    CONCLUSION_STRUCTURE,
    offline_conclusion,
    run_daily_hermes_report,
    write_conclusion,
)
from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
    DOCS,
    MAX_PACKAGE_BYTES,
    PACKAGE_NAME,
    SCHEMA_TABLE,
    TARGET_INPUT_TOKENS,
    TARGET_OUTPUT_TOKENS,
    _book_stats_compact,
    _current_market_compact,
    _elite_compact,
    _forward_compact,
    _funnel_compact,
    _integrity_compact,
    _journal_samples,
    _morning_compact,
    _reality_compact,
    _read_report_head,
    _replay_compact,
    _slim_trade,
    build_research_package,
    enforce_package_size,
    ensure_hermes_daily_schema,
    estimate_hermes_input_tokens,
    estimate_tokens,
    load_docs_for_hermes,
    persist_package_meta,
    run_daily_research_package,
    write_research_package,
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


def _seed_journal(conn, n=40):
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
                    0.8 if i % 4 else 0.4, 0.7 if i % 5 else 0.3, 0.5 if i % 3 else 0.1,
                    0.6 if i % 2 else 0.2, 1 if i % 2 else 0, 0.55 if i % 4 else 0.1,
                    0.65 if i % 3 else 0.2, 0.7 if i % 2 else 0.2, 0.5 if i % 4 else 0.1,
                    "A+" if i % 5 == 0 else "B", "[]",
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
        VALUES ('Candidate',0,100,80,20,80,55,1.2,0.1,0.5,?)
        """,
        (now,),
    )
    conn.execute(
        f"""
        INSERT INTO {FUNNEL_TABLE}
        (stage, stage_order, input_n, accepted_n, rejected_n, acceptance_pct, wr, pf, ev, sharpe, updated_at)
        VALUES ('Replay',3,80,40,40,50,60,1.5,0.2,0.8,?)
        """,
        (now,),
    )
    for i in range(12):
        conn.execute(
            f"""
            INSERT INTO {REJECTIONS_TABLE}
            (trade_id, first_rejector, pnl, meta_json, updated_at)
            VALUES (?,?,?,?,?)
            """,
            (i + 1, "Replay" if i < 8 else "Timeline", 1.0, "{}", now),
        )
    conn.commit()


def _seed_replay(conn):
    ensure_replay_recovery_schema(conn)
    now = int(time.time())
    rows = [
        ("summary", "recoverable_ev", 100.0, None, None),
        ("summary", "protected_ev", 40.0, None, None),
        ("summary", "net_replay_ev", -60.0, None, None),
        ("largest_mistake", "payload", None, None, json.dumps({"trade_id": 7, "pnl": 12.5})),
    ]
    for section, key, vr, vt, mj in rows:
        conn.execute(
            f"""
            INSERT INTO {REPLAY_TABLE}(section, key, value_real, value_text, meta_json, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            (section, key, vr, vt, mj, now),
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
    conn.execute(
        """
        INSERT INTO reality_validation_v1(section, key, value_text, updated_at)
        VALUES ('dataset','dataset_version','v1',?)
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO reality_validation_v1(section, key, value_real, updated_at)
        VALUES ('dataset','lake_rows',500,?)
        """,
        (now,),
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


def _seed_all(conn, n=40):
    _seed_journal(conn, n)
    _seed_funnel(conn)
    _seed_replay(conn)
    _seed_reality(conn)
    _seed_elite(conn)
    ensure_hermes_daily_schema(conn)


class TestConstants(unittest.TestCase):
    def test_max_package(self):
        self.assertEqual(MAX_PACKAGE_BYTES, 500 * 1024)

    def test_target_input(self):
        self.assertEqual(TARGET_INPUT_TOKENS, 50_000)

    def test_target_output(self):
        self.assertEqual(TARGET_OUTPUT_TOKENS, 10_000)

    def test_package_name(self):
        self.assertEqual(PACKAGE_NAME, "RESEARCH_PACKAGE.json")

    def test_docs_four(self):
        self.assertEqual(len(DOCS), 4)
        self.assertIn("HERMES_SYSTEM_PROMPT.md", DOCS)
        self.assertIn("PROJECT_STATE.md", DOCS)
        self.assertIn("RESEARCH_RULES.md", DOCS)
        self.assertIn("ARCHITECTURE.md", DOCS)

    def test_schema_table(self):
        self.assertEqual(SCHEMA_TABLE, "hermes_daily_pipeline_v1")


class TestEstimateTokens(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_short(self):
        self.assertEqual(estimate_tokens("abcd"), 1)

    def test_longer(self):
        self.assertEqual(estimate_tokens("a" * 400), 100)

    def test_none_safe(self):
        self.assertEqual(estimate_tokens(None), 0)  # type: ignore[arg-type]


class TestSlimTrade(unittest.TestCase):
    def test_keeps_keys(self):
        row = {"trade_id": 1, "pnl": 2.0, "features_json": "HUGE", "noise": 9}
        slim = _slim_trade(row)
        self.assertEqual(slim["trade_id"], 1)
        self.assertEqual(slim["pnl"], 2.0)
        self.assertNotIn("features_json", slim)
        self.assertNotIn("noise", slim)

    def test_drops_none(self):
        slim = _slim_trade({"trade_id": 1, "pnl": None, "symbol": "BTC"})
        self.assertNotIn("pnl", slim)
        self.assertEqual(slim["symbol"], "BTC")

    def test_empty(self):
        self.assertEqual(_slim_trade({}), {})


class TestSchema(unittest.TestCase):
    def test_ensure(self):
        conn = _conn()
        ensure_hermes_daily_schema(conn)
        conn.execute(
            f"INSERT INTO {SCHEMA_TABLE}(section, key, updated_at) VALUES ('s','k',1)"
        )
        self.assertEqual(
            conn.execute(f"SELECT COUNT(*) FROM {SCHEMA_TABLE}").fetchone()[0], 1
        )

    def test_unique(self):
        conn = _conn()
        ensure_hermes_daily_schema(conn)
        conn.execute(
            f"INSERT INTO {SCHEMA_TABLE}(section, key, value_real, updated_at) VALUES ('s','k',1,1)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {SCHEMA_TABLE}(section, key, value_real, updated_at) VALUES ('s','k',2,1)"
            )


class TestReportHead(unittest.TestCase):
    def test_missing(self):
        self.assertIsNone(_read_report_head(Path("/no/such/file.md")))

    def test_truncates(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.md"
            p.write_text("x" * 5000, encoding="utf-8")
            text = _read_report_head(p, max_chars=100)
            self.assertIsNotNone(text)
            assert text is not None
            self.assertLessEqual(len(text), 120)
            self.assertIn("truncated", text)

    def test_small_ok(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.md"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(_read_report_head(p), "hello")


class TestJournalSamples(unittest.TestCase):
    def test_limits(self):
        conn = _conn()
        _seed_journal(conn, 120)
        samples = _journal_samples(conn)
        self.assertLessEqual(len(samples["last_100_closed"]), 100)
        self.assertLessEqual(len(samples["last_30_accepted"]), 30)
        self.assertLessEqual(len(samples["last_30_rejected"]), 30)

    def test_slim_only(self):
        conn = _conn()
        _seed_journal(conn, 10)
        samples = _journal_samples(conn)
        for row in samples["last_100_closed"]:
            self.assertNotIn("features_json", row)
            self.assertNotIn("reasons_json", row)

    def test_counts_present(self):
        conn = _conn()
        _seed_journal(conn, 5)
        samples = _journal_samples(conn)
        self.assertIn("n_closed_available", samples)
        self.assertIn("n_book_b", samples)


class TestBookStats(unittest.TestCase):
    def test_keys(self):
        conn = _conn()
        _seed_journal(conn, 20)
        stats = _book_stats_compact(conn)
        self.assertIn("A", stats)
        self.assertIn("B", stats)
        self.assertIn("n_rows", stats["A"])

    def test_no_raw_rows(self):
        conn = _conn()
        _seed_journal(conn, 15)
        stats = _book_stats_compact(conn)
        blob = json.dumps(stats)
        self.assertNotIn("features_json", blob)


class TestFunnelReplayReality(unittest.TestCase):
    def test_funnel_sqlite(self):
        conn = _conn()
        _seed_funnel(conn)
        out = _funnel_compact(conn)
        self.assertEqual(out["source"], "sqlite")
        self.assertGreaterEqual(len(out["stages"]), 2)
        self.assertEqual(out["top_rejectors"][0]["module"], "Replay")

    def test_funnel_fallback(self):
        conn = _conn()
        out = _funnel_compact(conn)
        self.assertEqual(out["source"], "reports")

    def test_replay_sqlite(self):
        conn = _conn()
        _seed_replay(conn)
        out = _replay_compact(conn)
        self.assertEqual(out["source"], "sqlite")
        self.assertEqual(out["recoverable_ev"], 100.0)
        self.assertIn("largest_mistake", out)

    def test_replay_fallback(self):
        conn = _conn()
        out = _replay_compact(conn)
        self.assertEqual(out["source"], "reports")

    def test_reality(self):
        conn = _conn()
        _seed_reality(conn, 91.0)
        out = _reality_compact(conn)
        self.assertEqual(out["reality_score"], 91.0)
        self.assertEqual(out["lake_rows"], 500.0)


class TestEliteMarketMorning(unittest.TestCase):
    def test_elite(self):
        conn = _conn()
        _seed_elite(conn, 6)
        out = _elite_compact(conn)
        self.assertEqual(out["n_elite"], 6)
        self.assertLessEqual(len(out["top15_slim"]), 15)
        self.assertIn("categories", out)

    def test_current_market(self):
        conn = _conn()
        _seed_journal(conn, 8)
        out = _current_market_compact(conn)
        self.assertEqual(out["n_journal_a"], 8)
        self.assertIsNotNone(out["last_trade_id"])

    def test_morning_forward_integrity(self):
        self.assertIn("report_md", _morning_compact())
        self.assertIn("report_md", _forward_compact())
        self.assertIn("report_md", _integrity_compact())


class TestEnforceSize(unittest.TestCase):
    def test_small_passthrough(self):
        pkg = {"a": 1, "decision_journal_samples": {"last_100_closed": [{"trade_id": 1}]}}
        out, n = enforce_package_size(pkg)
        self.assertLessEqual(n, MAX_PACKAGE_BYTES)
        self.assertEqual(out["a"], 1)

    def test_trims_huge(self):
        huge = {
            "decision_journal_samples": {
                "last_100_closed": [{"trade_id": i, "pad": "x" * 2000} for i in range(200)],
                "last_30_accepted": [{"trade_id": i, "pad": "y" * 2000} for i in range(80)],
                "last_30_rejected": [{"trade_id": i, "pad": "z" * 2000} for i in range(80)],
            },
            "elite": {"top15_slim": [{"pad": "e" * 5000} for _ in range(15)]},
            "morning": {"report_md": "m" * 50_000},
            "forward": {"report_md": "f" * 50_000, "json": {"blob": "b" * 100_000}},
            "reality": {"report_md": "r" * 50_000},
            "integrity": {"report_md": "i" * 50_000},
            "decision_funnel": {"funnel_md": "d" * 50_000},
            "replay_recovery": {"recovery_md": "p" * 50_000},
        }
        out, n = enforce_package_size(huge)
        self.assertLessEqual(n, MAX_PACKAGE_BYTES)
        self.assertTrue(out.get("trimmed") or out.get("hard_trimmed"))


class TestBuildPackage(unittest.TestCase):
    def test_structure(self):
        conn = _conn()
        _seed_all(conn, 25)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
            return_value={"fingerprint": {}, "timeline": {}},
        ):
            pkg = build_research_package(conn)
        for key in (
            "morning", "forward", "reality", "decision_funnel", "replay_recovery",
            "elite", "decision_journal_samples", "book_statistics", "current_market",
            "current_fingerprint_timeline", "integrity", "cost_rules",
        ):
            self.assertIn(key, pkg)
        self.assertTrue(pkg["research_only"])
        self.assertTrue(pkg["cost_rules"]["never_read_full_lake"])

    def test_never_full_lake_blob(self):
        conn = _conn()
        _seed_all(conn, 30)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
            return_value={"fingerprint": {}, "timeline": {}},
        ):
            pkg = build_research_package(conn)
        blob = json.dumps(pkg)
        self.assertNotIn("research_lake_v1", blob)
        self.assertLess(len(blob.encode("utf-8")), MAX_PACKAGE_BYTES * 2)


class TestPersistAndRun(unittest.TestCase):
    def test_persist_meta(self):
        conn = _conn()
        ensure_hermes_daily_schema(conn)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.research_write_batch",
            side_effect=lambda c, fn: fn(c),
        ):
            persist_package_meta(conn, size_bytes=1000, tokens_est=2000, elapsed=1.5)
        n = conn.execute(
            f"SELECT COUNT(*) FROM {SCHEMA_TABLE} WHERE section='summary'"
        ).fetchone()[0]
        self.assertGreaterEqual(n, 3)

    def test_run_package(self):
        conn = _conn()
        _seed_all(conn, 20)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
                return_value={"fingerprint": {}, "timeline": {}},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.load_docs_for_hermes",
                return_value={d: "doc" for d in DOCS},
            ):
                out = run_daily_research_package(conn, write_files=True, persist=True)
        self.assertTrue(out["ok"])
        self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES)
        self.assertIn("terminal", out)
        self.assertTrue(out["research_only"])

    def test_write_package_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.OUT_DIR",
                base / "out",
            ):
                paths = write_research_package({"x": 1}, size_bytes=10)
            self.assertTrue(Path(paths["root"]).exists())
            self.assertTrue(Path(paths["out_dir"]).exists())


class TestDocsAndTokens(unittest.TestCase):
    def test_load_docs(self):
        docs = load_docs_for_hermes(max_chars_each=500)
        self.assertEqual(set(docs.keys()), set(DOCS))

    def test_estimate_with_docs(self):
        n = estimate_hermes_input_tokens({"a": 1}, {"d": "hello" * 100})
        self.assertGreater(n, 10)

    def test_under_budget_small_pkg(self):
        pkg = {"schema": "v1", "x": list(range(10))}
        docs = {d: "short" for d in DOCS}
        self.assertLess(estimate_hermes_input_tokens(pkg, docs), TARGET_INPUT_TOKENS)


class TestOfflineConclusion(unittest.TestCase):
    def _pkg(self):
        return {
            "generated_at": 1,
            "package_bytes": 100,
            "reality": {"reality_score": 80, "lake_rows": 10},
            "replay_recovery": {"recoverable_ev": 1, "protected_ev": 2, "net_replay_ev": 1},
            "elite": {"n_elite": 3, "categories": {"A+": 3}, "stats": {}},
            "decision_funnel": {"top_rejectors": [{"module": "Replay", "rejected": 9}]},
            "book_statistics": {"B": {"n": 1}},
            "decision_journal_samples": {
                "last_100_closed": [{"trade_id": 1}],
                "last_30_accepted": [{"trade_id": 2}],
                "last_30_rejected": [{"trade_id": 3}],
            },
        }

    def test_has_all_sections(self):
        text = offline_conclusion(self._pkg())
        for i in range(1, 12):
            self.assertIn(f"## {i}.", text)

    def test_hypotheses_max3(self):
        text = offline_conclusion(self._pkg())
        self.assertIn("### H1", text)
        self.assertIn("### H2", text)
        self.assertIn("### H3", text)
        self.assertNotIn("### H4", text)

    def test_hypothesis_fields(self):
        text = offline_conclusion(self._pkg())
        self.assertIn("Reason:", text)
        self.assertIn("Expected Improvement:", text)
        self.assertIn("Required Sample Size:", text)
        self.assertIn("Expected Validation Method:", text)

    def test_priority(self):
        text = offline_conclusion(self._pkg())
        self.assertIn("P1:", text)
        self.assertIn("P2:", text)
        self.assertIn("P3:", text)

    def test_structure_prompt_mentions(self):
        self.assertIn("Executive Summary", CONCLUSION_STRUCTURE)
        self.assertIn("Recommended Mathematics", CONCLUSION_STRUCTURE)
        self.assertIn("Research Priority", CONCLUSION_STRUCTURE)


class TestHermesReport(unittest.TestCase):
    def test_offline_run(self):
        conn = _conn()
        _seed_all(conn, 15)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "docs").mkdir()
            for name in DOCS:
                (base / "docs" / name).write_text(f"# {name}\n", encoding="utf-8")
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
                return_value={"fingerprint": {}, "timeline": {}},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ):
                out = run_daily_hermes_report(conn, offline=True, rebuild_package=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "offline")
        self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES)
        self.assertIn("estimated_input_tokens", out)

    def test_write_conclusion(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.OUT_DIR",
                base / "out",
            ):
                paths = write_conclusion("# hi\n")
            self.assertTrue(Path(paths["root"]).exists())

    def test_no_api_key_offline(self):
        conn = _conn()
        pkg = {
            "generated_at": 1,
            "package_bytes": 50,
            "reality": {},
            "replay_recovery": {},
            "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []},
            "book_statistics": {},
            "decision_journal_samples": {
                "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
            },
        }
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.load_docs_for_hermes",
                return_value={d: "x" for d in DOCS},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.claude_client_g2.is_claude_configured",
                return_value=False,
            ):
                out = run_daily_hermes_report(
                    conn, package=pkg, offline=False, rebuild_package=False
                )
        self.assertTrue(out["ok"])
        self.assertIn("offline", out["mode"])


class TestCostRules(unittest.TestCase):
    def test_package_declares_cost_rules(self):
        conn = _conn()
        _seed_all(conn, 10)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
            return_value={"fingerprint": {}, "timeline": {}},
        ):
            pkg = build_research_package(conn)
        rules = pkg["cost_rules"]
        self.assertTrue(rules["never_request_raw_sql_tables"])
        self.assertEqual(rules["max_package_bytes"], MAX_PACKAGE_BYTES)

    def test_samples_not_all_trades(self):
        conn = _conn()
        _seed_journal(conn, 200)
        samples = _journal_samples(conn)
        self.assertLessEqual(len(samples["last_100_closed"]), 100)
        self.assertNotEqual(len(samples["last_100_closed"]), samples["n_closed_available"])


class TestCLIRegistration(unittest.TestCase):
    def test_choices_contain_commands(self):
        # Avoid importing __main__ (side effects / huge stdout); read source.
        root = Path(__file__).resolve().parents[1]
        src = (root / "bot/research/market_events/__main__.py").read_text(encoding="utf-8")
        self.assertIn('"daily-research-package"', src)
        self.assertIn('"daily-hermes-report"', src)
        self.assertIn('args.command in ("daily-research-package", "daily-hermes-report")', src)
        self.assertIn("run_daily_research_package", src)
        self.assertIn("run_daily_hermes_report", src)


class TestParametrizedSlim(unittest.TestCase):
    def test_many_keys(self):
        for k in (
            "trade_id", "symbol", "direction", "opened_at", "decision", "accepted",
            "confidence", "replay", "fingerprint_similarity", "timeline_similarity",
            "dna", "rules", "edge", "brain", "causality", "decision_rank",
            "historical_wr", "historical_ev", "historical_pf", "result", "pnl", "regime",
        ):
            slim = _slim_trade({k: 1})
            self.assertIn(k, slim, msg=k)


class TestParametrizedSections(unittest.TestCase):
    def test_section_heads(self):
        text = offline_conclusion({
            "generated_at": 1, "package_bytes": 1,
            "reality": {}, "replay_recovery": {}, "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []}, "book_statistics": {},
            "decision_journal_samples": {
                "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
            },
        })
        titles = [
            "Executive Summary", "Today's Findings", "Top Mathematical Discoveries",
            "Rejected Trades Analysis", "Accepted Trades Analysis", "Replay Investigation",
            "Reality Validation", "Elite Review", "New Hypotheses",
            "Recommended Mathematics", "Research Priority",
        ]
        for title in titles:
            self.assertIn(title, text, msg=title)


class TestSizeBudgetMany(unittest.TestCase):
    def test_run_bytes_ok_for_sizes(self):
        for n in (5, 20, 50, 80):
            conn = _conn()
            _seed_all(conn, n)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
                return_value={"fingerprint": {}, "timeline": {}},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.research_write_batch",
                side_effect=lambda c, fn: fn(c),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.load_docs_for_hermes",
                return_value={d: "d" for d in DOCS},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.write_research_package",
                return_value={},
            ):
                out = run_daily_research_package(conn, write_files=False, persist=True)
            self.assertLessEqual(out["package_bytes"], MAX_PACKAGE_BYTES, msg=f"n={n}")
            self.assertTrue(out["ok"], msg=f"n={n}")


class TestEstimateTokensGrid(unittest.TestCase):
    def test_grid(self):
        for length, expected_min in ((0, 0), (1, 1), (4, 1), (8, 2), (40, 10)):
            n = estimate_tokens("a" * length)
            if length == 0:
                self.assertEqual(n, 0)
            else:
                self.assertGreaterEqual(n, expected_min)


class TestEliteTop15(unittest.TestCase):
    def test_caps_at_15(self):
        conn = _conn()
        _seed_elite(conn, 20)
        out = _elite_compact(conn)
        self.assertEqual(out["n_elite"], 20)
        self.assertEqual(len(out["top15_slim"]), 15)


class TestInitExports(unittest.TestCase):
    def test_exports(self):
        from bot.research.market_events.signal_intelligence import hermes_daily_v1 as m
        self.assertTrue(callable(m.run_daily_research_package))
        self.assertTrue(callable(m.run_daily_hermes_report))


class TestEventSchemaHook(unittest.TestCase):
    def test_ensure_hook_present(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "bot/research/market_events/event_schema.py").read_text(encoding="utf-8")
        self.assertIn("_ensure_hermes_daily_v1", src)
        self.assertIn("ensure_hermes_daily_schema", src)


class TestFingerprintTimelineSafe(unittest.TestCase):
    def test_no_engine_import_required(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
            _fingerprint_timeline_compact,
        )
        conn = _conn()
        out = _fingerprint_timeline_compact(conn)
        self.assertIn("fingerprint", out)
        self.assertIn("timeline", out)
        self.assertIn("report_md", out["fingerprint"])
        self.assertIn("report_md", out["timeline"])


class TestLoadJsonIfSmall(unittest.TestCase):
    def test_missing(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
            _load_json_if_small,
        )
        self.assertIsNone(_load_json_if_small(Path("/no/json.json")))

    def test_too_large(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
            _load_json_if_small,
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"a": "x" * 1000}), encoding="utf-8")
            out = _load_json_if_small(p, max_bytes=10)
            self.assertTrue(out["_skipped"])

    def test_ok(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
            _load_json_if_small,
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"ok": True}), encoding="utf-8")
            self.assertEqual(_load_json_if_small(p), {"ok": True})


class TestHermesPrompts(unittest.TestCase):
    def test_system_includes_cost_rules(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes import (
            _system_prompt,
        )
        text = _system_prompt({d: "body" for d in DOCS})
        self.assertIn("NEVER ask for raw trade history", text)
        self.assertIn("HERMES_SYSTEM_PROMPT", text)

    def test_user_includes_package(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes import (
            _user_prompt,
        )
        text = _user_prompt({"schema": "v1"}, {d: "d" for d in DOCS})
        self.assertIn("RESEARCH_PACKAGE.json", text)
        self.assertIn('"schema": "v1"', text)

    def test_user_truncates_huge_package(self):
        from bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes import (
            _user_prompt,
        )
        huge = {"pad": "x" * 200_000}
        text = _user_prompt(huge, {d: "d" for d in DOCS})
        self.assertIn("truncated for token budget", text)


class TestParametrizedEstimate(unittest.TestCase):
    def test_lengths(self):
        for n in range(0, 41, 4):
            est = estimate_tokens("z" * n)
            if n == 0:
                self.assertEqual(est, 0)
            else:
                self.assertEqual(est, max(1, (n + 3) // 4))


class TestParametrizedOfflineFields(unittest.TestCase):
    def test_required_fields_in_h1(self):
        text = offline_conclusion({
            "generated_at": 1, "package_bytes": 1,
            "reality": {"reality_score": 1}, "replay_recovery": {},
            "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []}, "book_statistics": {},
            "decision_journal_samples": {
                "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
            },
        })
        for field in ("Reason", "Expected Improvement", "Required Sample Size", "Expected Validation Method"):
            self.assertGreaterEqual(text.count(field), 3, msg=field)


class TestJournalLimitsGrid(unittest.TestCase):
    def test_accepted_rejected_caps(self):
        for n in (10, 40, 100):
            conn = _conn()
            _seed_journal(conn, n)
            samples = _journal_samples(conn)
            self.assertLessEqual(len(samples["last_30_accepted"]), 30, msg=n)
            self.assertLessEqual(len(samples["last_30_rejected"]), 30, msg=n)
            self.assertLessEqual(len(samples["last_100_closed"]), 100, msg=n)


class TestFunnelTopRejectorOrder(unittest.TestCase):
    def test_replay_first(self):
        conn = _conn()
        _seed_funnel(conn)
        top = _funnel_compact(conn)["top_rejectors"]
        self.assertGreaterEqual(top[0]["rejected"], top[-1]["rejected"])


class TestRealityDatasetKeys(unittest.TestCase):
    def test_version_and_rows(self):
        conn = _conn()
        _seed_reality(conn)
        out = _reality_compact(conn)
        self.assertEqual(out["dataset_version"], "v1")
        self.assertEqual(out["lake_rows"], 500.0)


class TestPackageNeverRequestsLake(unittest.TestCase):
    def test_cost_rules_flags(self):
        conn = _conn()
        _seed_all(conn, 5)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package._fingerprint_timeline_compact",
            return_value={"fingerprint": {}, "timeline": {}},
        ):
            pkg = build_research_package(conn)
        self.assertTrue(pkg["cost_rules"]["never_read_full_lake"])
        self.assertTrue(pkg["cost_rules"]["never_request_raw_sql_tables"])
        self.assertEqual(pkg["cost_rules"]["target_input_tokens"], TARGET_INPUT_TOKENS)
        self.assertEqual(pkg["cost_rules"]["target_output_tokens"], TARGET_OUTPUT_TOKENS)


class TestWritePackageRoundtrip(unittest.TestCase):
    def test_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.OUT_DIR",
                base / "out",
            ):
                write_research_package({"hello": "world", "n": 3}, size_bytes=20)
                data = json.loads((base / PACKAGE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(data["hello"], "world")
        self.assertEqual(data["n"], 3)


class TestOfflineNoMathCode(unittest.TestCase):
    def test_no_code_blocks_in_math_section(self):
        text = offline_conclusion({
            "generated_at": 1, "package_bytes": 1,
            "reality": {}, "replay_recovery": {},
            "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []}, "book_statistics": {},
            "decision_journal_samples": {
                "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
            },
        })
        # Recommended Mathematics should be ideas, not python fences
        math_part = text.split("## 10.")[1].split("## 11.")[0]
        self.assertNotIn("```python", math_part)
        self.assertNotIn("def ", math_part)


class TestSchemaPersistValues(unittest.TestCase):
    def test_values_written(self):
        conn = _conn()
        ensure_hermes_daily_schema(conn)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.research_write_batch",
            side_effect=lambda c, fn: fn(c),
        ):
            persist_package_meta(conn, size_bytes=12345, tokens_est=6789, elapsed=2.25)
        row = conn.execute(
            f"SELECT value_real FROM {SCHEMA_TABLE} WHERE key='package_bytes'"
        ).fetchone()
        self.assertEqual(row[0], 12345.0)


class TestDocsMissingFallback(unittest.TestCase):
    def test_missing_docs(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "docs").mkdir()
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ):
                docs = load_docs_for_hermes()
        for name in DOCS:
            self.assertIn("missing", docs[name])


class TestFindingsMax10(unittest.TestCase):
    def test_offline_findings_count(self):
        text = offline_conclusion({
            "generated_at": 1, "package_bytes": 1,
            "reality": {}, "replay_recovery": {},
            "elite": {"n_elite": 0, "categories": {}, "stats": {}},
            "decision_funnel": {"top_rejectors": []}, "book_statistics": {},
            "decision_journal_samples": {
                "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
            },
        })
        findings = text.split("## 2.")[1].split("## 3.")[0]
        numbered = [ln for ln in findings.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
        self.assertLessEqual(len(numbered), 10)


class TestSlimTradeBatch(unittest.TestCase):
    def test_batch_ids(self):
        for i in range(25):
            slim = _slim_trade({"trade_id": i, "pnl": float(i), "junk": "x" * 100})
            self.assertEqual(slim["trade_id"], i)
            self.assertNotIn("junk", slim)


class TestEnforceSizeProgressive(unittest.TestCase):
    def test_reduces_closed(self):
        pkg = {
            "decision_journal_samples": {
                "last_100_closed": [{"trade_id": i, "pad": "p" * 3000} for i in range(100)],
                "last_30_accepted": [{"trade_id": i} for i in range(30)],
                "last_30_rejected": [{"trade_id": i} for i in range(30)],
            },
            "elite": {"top15_slim": [{"pad": "e" * 1000} for _ in range(15)]},
            "morning": {"report_md": "m" * 20_000},
            "forward": {"report_md": "f" * 20_000},
            "reality": {"report_md": "r" * 20_000},
            "integrity": {"report_md": "i" * 20_000},
        }
        raw0 = len(json.dumps(pkg).encode())
        out, n = enforce_package_size(pkg)
        self.assertLess(n, raw0)
        self.assertLessEqual(n, MAX_PACKAGE_BYTES)
        self.assertTrue(out.get("trimmed") or n <= MAX_PACKAGE_BYTES)


class TestCurrentMarketEmpty(unittest.TestCase):
    def test_empty_journal(self):
        conn = _conn()
        ensure_decision_journal_schema(conn)
        out = _current_market_compact(conn)
        self.assertEqual(out["n_journal_a"], 0)


class TestHermesRebuildFalse(unittest.TestCase):
    def test_loads_existing_package(self):
        conn = _conn()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / PACKAGE_NAME).write_text(
                json.dumps({
                    "generated_at": 9, "package_bytes": 11,
                    "reality": {}, "replay_recovery": {},
                    "elite": {"n_elite": 0, "categories": {}, "stats": {}},
                    "decision_funnel": {"top_rejectors": []}, "book_statistics": {},
                    "decision_journal_samples": {
                        "last_100_closed": [], "last_30_accepted": [], "last_30_rejected": [],
                    },
                }),
                encoding="utf-8",
            )
            (base / "docs").mkdir()
            for name in DOCS:
                (base / "docs" / name).write_text("x", encoding="utf-8")
            with mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.hermes.OUT_DIR",
                base / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.hermes_daily_v1.package.BASE_DIR",
                base,
            ):
                out = run_daily_hermes_report(conn, offline=True, rebuild_package=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["package_bytes"], 11)


class TestGeneratedTokenMath(unittest.TestCase):
    pass


def _make_token_test(n: int):
    def _test(self):
        self.assertEqual(estimate_tokens("q" * n), 0 if n == 0 else max(1, (n + 3) // 4))
    return _test


for _n in range(0, 25):
    setattr(TestGeneratedTokenMath, f"test_tokens_{_n}", _make_token_test(_n))


class TestGeneratedSlimKeys(unittest.TestCase):
    pass


def _make_slim_test(key: str):
    def _test(self):
        slim = _slim_trade({key: 42, "features_json": "NOPE"})
        self.assertEqual(slim.get(key), 42)
        self.assertNotIn("features_json", slim)
    return _test


for _k in (
    "trade_id", "symbol", "direction", "opened_at", "decision", "accepted",
    "confidence", "replay", "fingerprint_similarity", "timeline_similarity",
    "dna", "rules", "edge", "brain", "causality", "decision_rank",
    "historical_wr", "historical_ev", "historical_pf", "result", "pnl", "regime",
):
    setattr(TestGeneratedSlimKeys, f"test_slim_{_k}", _make_slim_test(_k))


if __name__ == "__main__":
    unittest.main()
