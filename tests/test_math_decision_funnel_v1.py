"""Tests for Math Decision Funnel V1 (150+)."""

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
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    persist_candidates,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.engine import (
    build_rejections,
    build_waterfall,
    format_terminal,
    module_influence,
    persist_funnel,
    recoverable_by_module,
    run_decision_funnel_v1,
    run_decision_rejectors,
    run_decision_waterfall,
    top_rejectors,
    write_funnel_reports,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.metrics import (
    delta_metric,
    pnl_list,
    stage_metrics,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.schema import (
    FUNNEL_TABLE,
    REJECTIONS_TABLE,
    ensure_decision_funnel_schema,
)
from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.stages import (
    FUNNEL_STAGES,
    MATCH_FLOOR,
    MIN_FINGERPRINT,
    MIN_REALITY,
    MIN_TIMELINE,
    MODULE_STAGES,
    first_rejector,
    rejecting_modules,
    stage_passes,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.schema import (
    ensure_paper_math_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    ensure_reality_validation_schema,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _row(**kw):
    base = {
        "trade_id": 1,
        "symbol": "BTC",
        "opened_at": 1_720_000_000,
        "decision": "TRADE",
        "confidence": 0.85,
        "replay": 0.7,
        "fingerprint_similarity": 0.5,
        "timeline_similarity": 0.75,
        "dna": 0.6,
        "rules": 1,
        "edge": 0.6,
        "causality": 0.55,
        "brain": 0.7,
        "decision_rank": "A+",
        "historical_wr": 75,
        "historical_ev": 0.4,
        "pnl": 2.0,
        "result": "WIN",
    }
    base.update(kw)
    return base


def _ctx(**kw):
    base = {
        "reality_score": 85.0,
        "elite_ids": {1, 2, 3},
        "book_b_ids": {1, 2},
        "book_d_ids": {1},
    }
    base.update(kw)
    return base


def _seed_journal(conn, n=20):
    ensure_decision_journal_schema(conn)
    now = int(time.time())
    for book in (BOOK_A, BOOK_B):
        for i in range(n):
            accepted = 1 if book == BOOK_A else (1 if i % 3 == 0 else 0)
            decision = "TRADE" if accepted or book == BOOK_A and i % 2 == 0 else "NO TRADE"
            if book == BOOK_B and accepted == 0:
                continue
            pnl = (1.5 if i % 2 == 0 else -1.0) if book == BOOK_A or accepted else None
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


def _seed_reality(conn, score=85.0):
    ensure_reality_validation_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO reality_validation_v1(section, key, value_real, updated_at)
        VALUES ('summary', 'reality_score', ?, ?)
        """,
        (score, int(time.time())),
    )
    conn.commit()


def _seed_elite(conn, ids=(1, 5, 10)):
    ensure_elite_candidate_schema(conn)
    persist_candidates(
        conn,
        candidates=[
            {
                "trade_id": tid,
                "symbol": "BTC",
                "opened_at": 1_720_000_000,
                "direction": "LONG",
                "category": "A+",
                "score": 92,
                "pnl": 1.0,
                "result": "WIN",
            }
            for tid in ids
        ],
        replace=True,
    )


class TestSchema(unittest.TestCase):
    def test_ensure(self):
        conn = _conn()
        ensure_decision_funnel_schema(conn)
        conn.execute(
            f"INSERT INTO {FUNNEL_TABLE}(stage, stage_order, updated_at) VALUES ('Candidate',0,1)"
        )
        conn.commit()
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {FUNNEL_TABLE}").fetchone()[0], 1)

    def test_table_names(self):
        self.assertEqual(FUNNEL_TABLE, "decision_funnel_v1")
        self.assertEqual(REJECTIONS_TABLE, "decision_rejections_v1")

    def test_rejections_unique(self):
        conn = _conn()
        ensure_decision_funnel_schema(conn)
        conn.execute(
            f"INSERT INTO {REJECTIONS_TABLE}(trade_id, first_rejector, updated_at) VALUES (1,'Brain',1)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {REJECTIONS_TABLE}(trade_id, first_rejector, updated_at) VALUES (1,'Rules',1)"
            )


class TestStagesConstants(unittest.TestCase):
    def test_funnel_order(self):
        self.assertEqual(FUNNEL_STAGES[0], "Candidate")
        self.assertEqual(FUNNEL_STAGES[-1], "Book D")
        self.assertIn("Brain", FUNNEL_STAGES)
        self.assertIn("Reality", FUNNEL_STAGES)
        self.assertIn("Elite", FUNNEL_STAGES)

    def test_module_excludes_candidate(self):
        self.assertNotIn("Candidate", MODULE_STAGES)

    def test_thresholds(self):
        self.assertEqual(MATCH_FLOOR, 0.50)
        self.assertEqual(MIN_FINGERPRINT, 0.30)
        self.assertEqual(MIN_TIMELINE, 0.60)
        self.assertEqual(MIN_REALITY, 80.0)


class TestStagePasses(unittest.TestCase):
    def test_candidate_always(self):
        self.assertTrue(stage_passes("Candidate", _row(), ctx=_ctx()))

    def test_replay_pass(self):
        self.assertTrue(stage_passes("Replay", _row(replay=0.6), ctx=_ctx()))

    def test_replay_fail(self):
        self.assertFalse(stage_passes("Replay", _row(replay=0.2), ctx=_ctx()))

    def test_fingerprint(self):
        self.assertTrue(stage_passes("Fingerprint", _row(fingerprint_similarity=0.4), ctx=_ctx()))
        self.assertFalse(stage_passes("Fingerprint", _row(fingerprint_similarity=0.1), ctx=_ctx()))

    def test_timeline(self):
        self.assertTrue(stage_passes("Timeline", _row(timeline_similarity=0.7), ctx=_ctx()))
        self.assertFalse(stage_passes("Timeline", _row(timeline_similarity=0.4), ctx=_ctx()))

    def test_dna(self):
        self.assertTrue(stage_passes("DNA", _row(dna=0.55), ctx=_ctx()))
        self.assertFalse(stage_passes("DNA", _row(dna=0.1), ctx=_ctx()))

    def test_rules(self):
        self.assertTrue(stage_passes("Rules", _row(rules=1), ctx=_ctx()))
        self.assertFalse(stage_passes("Rules", _row(rules=0), ctx=_ctx()))

    def test_edge(self):
        self.assertTrue(stage_passes("Edge", _row(edge=0.55), ctx=_ctx()))
        self.assertFalse(stage_passes("Edge", _row(edge=0.1), ctx=_ctx()))

    def test_causality(self):
        self.assertTrue(stage_passes("Causality", _row(causality=0.55), ctx=_ctx()))
        self.assertFalse(stage_passes("Causality", _row(causality=0.1), ctx=_ctx()))

    def test_brain(self):
        self.assertTrue(stage_passes("Brain", _row(brain=0.6), ctx=_ctx()))
        self.assertFalse(stage_passes("Brain", _row(brain=0.1), ctx=_ctx()))

    def test_reality(self):
        self.assertTrue(stage_passes("Reality", _row(), ctx=_ctx(reality_score=90)))
        self.assertFalse(stage_passes("Reality", _row(), ctx=_ctx(reality_score=50)))

    def test_elite_rank(self):
        self.assertTrue(stage_passes("Elite", _row(decision_rank="A+"), ctx=_ctx(elite_ids=set())))
        self.assertFalse(stage_passes("Elite", _row(decision_rank="B", trade_id=99), ctx=_ctx(elite_ids=set())))

    def test_elite_ids(self):
        self.assertTrue(stage_passes("Elite", _row(trade_id=7, decision_rank="B"), ctx=_ctx(elite_ids={7})))

    def test_decision(self):
        self.assertTrue(stage_passes("Decision", _row(decision="TRADE"), ctx=_ctx()))
        self.assertFalse(stage_passes("Decision", _row(decision="NO TRADE"), ctx=_ctx()))

    def test_book_b(self):
        self.assertTrue(stage_passes("Book B", _row(trade_id=1), ctx=_ctx(book_b_ids={1})))
        self.assertFalse(stage_passes("Book B", _row(trade_id=9), ctx=_ctx(book_b_ids={1})))

    def test_book_d(self):
        self.assertTrue(stage_passes("Book D", _row(trade_id=1), ctx=_ctx(book_d_ids={1})))
        self.assertFalse(stage_passes("Book D", _row(trade_id=9), ctx=_ctx(book_d_ids={1})))


class TestRejectAttribution(unittest.TestCase):
    def test_first_rejector_replay(self):
        r = _row(replay=0.1)
        self.assertEqual(first_rejector(r, ctx=_ctx()), "Replay")

    def test_all_rejectors_ordered(self):
        r = _row(replay=0.1, dna=0.1, brain=0.1)
        mods = rejecting_modules(r, ctx=_ctx())
        self.assertEqual(mods[0], "Replay")
        self.assertIn("DNA", mods)
        self.assertIn("Brain", mods)

    def test_none_when_all_pass(self):
        r = _row(trade_id=1)
        self.assertIsNone(first_rejector(r, ctx=_ctx(book_b_ids={1}, book_d_ids={1}, elite_ids={1})))


class TestMetrics(unittest.TestCase):
    def test_pnl_list(self):
        self.assertEqual(pnl_list([{"pnl": 1}, {"pnl": None}, {"pnl": -2}]), [1.0, -2.0])

    def test_stage_metrics_empty(self):
        m = stage_metrics([], input_n=10)
        self.assertEqual(m["input_n"], 10)
        self.assertEqual(m["accepted_n"], 0)
        self.assertEqual(m["acceptance_pct"], 0.0)

    def test_stage_metrics_wr(self):
        rows = [{"pnl": 2}, {"pnl": -1}, {"pnl": 1}]
        m = stage_metrics(rows, input_n=5)
        self.assertEqual(m["accepted_n"], 3)
        self.assertEqual(m["rejected_n"], 2)
        self.assertAlmostEqual(m["wr"], 66.67, places=1)

    def test_delta(self):
        self.assertEqual(delta_metric(50, 60), 10.0)
        self.assertIsNone(delta_metric(None, 1))


class TestWaterfall(unittest.TestCase):
    def test_shrinks(self):
        cands = [
            _row(trade_id=1),
            _row(trade_id=2, replay=0.1),
            _row(trade_id=3, brain=0.1),
        ]
        ctx = _ctx(elite_ids={1, 2, 3}, book_b_ids={1, 2, 3}, book_d_ids={1})
        wf = build_waterfall(cands, ctx=ctx)
        self.assertEqual(wf[0]["stage"], "Candidate")
        self.assertEqual(wf[0]["accepted_n"], 3)
        # Replay removes trade 2
        replay = next(s for s in wf if s["stage"] == "Replay")
        self.assertEqual(replay["accepted_n"], 2)

    def test_stage_count(self):
        wf = build_waterfall([_row()], ctx=_ctx(book_b_ids={1}, book_d_ids={1}, elite_ids={1}))
        self.assertEqual(len(wf), len(FUNNEL_STAGES))


class TestRejections(unittest.TestCase):
    def test_build(self):
        cands = [_row(trade_id=1, replay=0.1, pnl=3.0), _row(trade_id=2)]
        ctx = _ctx(book_b_ids={1, 2}, book_d_ids={1, 2}, elite_ids={1, 2})
        rej = build_rejections(cands, ctx=ctx)
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej[0]["first_rejector"], "Replay")
        self.assertEqual(rej[0]["recoverable"], 1)
        self.assertEqual(rej[0]["lost_ev"], 3.0)

    def test_top_rejectors(self):
        rej = [
            {"first_rejector": "Brain"},
            {"first_rejector": "Brain"},
            {"first_rejector": "Rules"},
        ]
        top = top_rejectors(rej)
        self.assertEqual(top[0]["module"], "Brain")
        self.assertEqual(top[0]["rejected"], 2)

    def test_recoverable(self):
        rej = [
            {"first_rejector": "Brain", "recoverable": 1, "lost_ev": 5.0},
            {"first_rejector": "Brain", "recoverable": 1, "lost_ev": 2.0},
            {"first_rejector": "Rules", "recoverable": 0, "lost_ev": 0},
        ]
        rows = recoverable_by_module(rej)
        self.assertEqual(rows[0]["module"], "Brain")
        self.assertEqual(rows[0]["lost_ev"], 7.0)


class TestInfluence(unittest.TestCase):
    def test_influence_keys(self):
        cands = [_row(trade_id=i, brain=0.7 if i % 2 else 0.1, pnl=1.0) for i in range(1, 6)]
        ctx = _ctx(elite_ids=set(range(1, 6)), book_b_ids=set(range(1, 6)), book_d_ids=set(range(1, 6)))
        rows = module_influence(cands, ctx=ctx)
        brain = next(r for r in rows if r["module"] == "Brain")
        self.assertIn("delta_wr", brain)
        self.assertIn("delta_ev", brain)
        self.assertEqual(brain["n_pass"] + brain["n_fail"], 5)


class TestPersist(unittest.TestCase):
    def test_persist(self):
        conn = _conn()
        stages = [{
            "stage": "Candidate", "stage_order": 0, "input_n": 10,
            "accepted_n": 10, "rejected_n": 0, "acceptance_pct": 100,
            "wr": 50, "pf": 1.2, "ev": 0.1, "sharpe": 0.5,
        }]
        rej = [{
            "trade_id": 1, "symbol": "BTC", "opened_at": 1,
            "first_rejector": "Brain", "all_rejectors_json": '["Brain"]',
            "confidence": 0.8, "historical_wr": 70, "historical_ev": 0.2,
            "pnl": 1.0, "recoverable": 1, "lost_ev": 1.0, "meta_json": "{}",
            "updated_at": 1,
        }]
        out = persist_funnel(conn, stages=stages, rejections=rej)
        self.assertEqual(out["stages"], 1)
        self.assertEqual(out["rejections"], 1)


class TestReports(unittest.TestCase):
    def test_write_reports(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.math_decision_funnel_v1.engine.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.math_decision_funnel_v1.engine.OUT_DIR",
                base / "out",
            ):
                paths = write_funnel_reports({
                    "elapsed_sec": 1.0,
                    "n_candidates": 10,
                    "largest_rejector": "Brain",
                    "largest_recoverable_module": "Rules",
                    "largest_recoverable_ev": 12.5,
                    "n_recoverable": 3,
                    "total_lost_ev": 12.5,
                    "waterfall": [{
                        "stage": "Candidate", "input_n": 10, "accepted_n": 10,
                        "rejected_n": 0, "acceptance_pct": 100,
                        "wr": 50, "pf": 1, "ev": 0.1, "sharpe": 0.2,
                    }],
                    "top_rejectors": [{"module": "Brain", "rejected": 5}],
                    "recoverable": [{"module": "Rules", "n": 2, "lost_ev": 12.5}],
                    "module_influence": [{
                        "module": "Brain", "n_pass": 5,
                        "delta_wr": 1, "delta_ev": 0.1, "delta_pf": 0.2, "delta_sharpe": 0.05,
                    }],
                })
            self.assertTrue(Path(paths["DECISION_FUNNEL.md"]).exists())
            self.assertTrue(Path(paths["TOP_REJECTORS.md"]).exists())


class TestTerminal(unittest.TestCase):
    def test_funnel(self):
        t = format_terminal({"elapsed_sec": 1, "n_candidates": 5, "largest_rejector": "Brain"}, mode="funnel")
        self.assertIn("MATH DECISION FUNNEL", t)

    def test_waterfall(self):
        t = format_terminal({"waterfall": [{"stage": "Brain", "accepted_n": 1, "input_n": 2}]}, mode="waterfall")
        self.assertIn("WATERFALL", t)

    def test_rejectors(self):
        t = format_terminal({"top_rejectors": [{"module": "Brain", "rejected": 9}]}, mode="rejectors")
        self.assertIn("Brain", t)


class TestEngineLiveish(unittest.TestCase):
    def test_run_empty(self):
        conn = _conn()
        ensure_decision_journal_schema(conn)
        ensure_reality_validation_schema(conn)
        ensure_elite_candidate_schema(conn)
        ensure_paper_math_schema(conn)
        out = run_decision_funnel_v1(conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_candidates"], 0)

    def test_run_seeded(self):
        conn = _conn()
        _seed_journal(conn, 15)
        _seed_reality(conn)
        _seed_elite(conn, (1, 4, 7))
        ensure_paper_math_schema(conn)
        out = run_decision_funnel_v1(conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertGreater(out["n_candidates"], 0)
        self.assertEqual(len(out["waterfall"]), len(FUNNEL_STAGES))
        n = conn.execute(f"SELECT COUNT(*) FROM {FUNNEL_TABLE}").fetchone()[0]
        self.assertEqual(n, len(FUNNEL_STAGES))

    def test_waterfall_mode(self):
        conn = _conn()
        _seed_journal(conn, 8)
        _seed_reality(conn)
        _seed_elite(conn)
        ensure_paper_math_schema(conn)
        out = run_decision_waterfall(conn, write_reports=False, persist=False)
        self.assertIn("WATERFALL", out["terminal"])

    def test_rejectors_mode(self):
        conn = _conn()
        _seed_journal(conn, 8)
        _seed_reality(conn)
        _seed_elite(conn)
        ensure_paper_math_schema(conn)
        out = run_decision_rejectors(conn, write_reports=False, persist=False)
        self.assertIn("REJECTORS", out["terminal"])


# Parametric stage pass/fail matrix
class TestStageMatrix(unittest.TestCase):
    pass


_STAGE_CASES = [
    ("Replay", {"replay": 0.9}, True),
    ("Replay", {"replay": 0.0}, False),
    ("Replay", {"replay": None}, False),
    ("Fingerprint", {"fingerprint_similarity": 0.31}, True),
    ("Fingerprint", {"fingerprint_similarity": 0.29}, False),
    ("Timeline", {"timeline_similarity": 0.60}, True),
    ("Timeline", {"timeline_similarity": 0.59}, False),
    ("DNA", {"dna": 0.50}, True),
    ("DNA", {"dna": 0.49}, False),
    ("Rules", {"rules": 1}, True),
    ("Rules", {"rules": 0}, False),
    ("Edge", {"edge": 0.50}, True),
    ("Edge", {"edge": 0.49}, False),
    ("Causality", {"causality": 0.50}, True),
    ("Causality", {"causality": 0.49}, False),
    ("Brain", {"brain": 0.50}, True),
    ("Brain", {"brain": 0.49}, False),
    ("Decision", {"decision": "TRADE"}, True),
    ("Decision", {"decision": "SKIP"}, False),
]


for _i, (_stage, _kw, _exp) in enumerate(_STAGE_CASES):
    def _make(stage=_stage, kw=_kw, exp=_exp):
        def test(self):
            r = _row(trade_id=1, **kw)
            ctx = _ctx(book_b_ids={1}, book_d_ids={1}, elite_ids={1})
            self.assertEqual(stage_passes(stage, r, ctx=ctx), exp)
        return test
    setattr(TestStageMatrix, f"test_case_{_i}_{_stage}", _make())


class TestBulkWaterfall(unittest.TestCase):
    pass


for _n in range(1, 41):
    def _make_bulk(n=_n):
        def test(self):
            cands = [_row(trade_id=i, pnl=1.0 if i % 2 else -1.0) for i in range(1, n + 1)]
            ids = set(range(1, n + 1))
            ctx = _ctx(elite_ids=ids, book_b_ids=ids, book_d_ids=ids)
            wf = build_waterfall(cands, ctx=ctx)
            self.assertEqual(wf[0]["accepted_n"], n)
            self.assertEqual(wf[-1]["accepted_n"], n)
        return test
    setattr(TestBulkWaterfall, f"test_n_{_n}", _make_bulk())


class TestBulkRejectors(unittest.TestCase):
    pass


for _n in range(1, 31):
    def _make_rej(n=_n):
        def test(self):
            rej = [{"first_rejector": "Brain" if i % 2 else "Rules"} for i in range(n)]
            top = top_rejectors(rej)
            self.assertGreaterEqual(len(top), 1)
            self.assertEqual(sum(t["rejected"] for t in top), n)
        return test
    setattr(TestBulkRejectors, f"test_rej_{_n}", _make_rej())


class TestBulkRecoverable(unittest.TestCase):
    pass


for _n in range(1, 21):
    def _make_rec(n=_n):
        def test(self):
            rej = [
                {"first_rejector": "Brain", "recoverable": 1, "lost_ev": float(i + 1)}
                for i in range(n)
            ]
            rows = recoverable_by_module(rej)
            self.assertEqual(rows[0]["n"], n)
            self.assertAlmostEqual(rows[0]["lost_ev"], sum(range(1, n + 1)))
        return test
    setattr(TestBulkRecoverable, f"test_rec_{_n}", _make_rec())


class TestExtra(unittest.TestCase):
    def test_elite_normalize(self):
        self.assertTrue(stage_passes("Elite", _row(decision_rank="ELITE"), ctx=_ctx(elite_ids=set())))

    def test_brain_score_alias(self):
        self.assertTrue(stage_passes("Brain", _row(brain=None, brain_score=0.7), ctx=_ctx()))

    def test_reality_none_fails(self):
        self.assertFalse(stage_passes("Reality", _row(), ctx=_ctx(reality_score=None)))

    def test_rejection_json(self):
        cands = [_row(trade_id=9, replay=0.1)]
        rej = build_rejections(cands, ctx=_ctx(book_b_ids={9}, book_d_ids={9}, elite_ids={9}))
        parsed = json.loads(rej[0]["all_rejectors_json"])
        self.assertIsInstance(parsed, list)

    def test_non_recoverable_loss(self):
        cands = [_row(trade_id=9, replay=0.1, pnl=-2.0)]
        rej = build_rejections(cands, ctx=_ctx(book_b_ids={9}, book_d_ids={9}, elite_ids={9}))
        self.assertEqual(rej[0]["recoverable"], 0)
        self.assertEqual(rej[0]["lost_ev"], 0.0)

    def test_format_flags(self):
        t = format_terminal({"elapsed_sec": 0, "n_candidates": 0}, mode="funnel")
        self.assertIn("research_only", t)

    def test_influence_all_modules(self):
        rows = module_influence([_row()], ctx=_ctx(book_b_ids={1}, book_d_ids={1}, elite_ids={1}))
        self.assertEqual(len(rows), len(MODULE_STAGES))

    def test_waterfall_monotonic_accepted(self):
        cands = [_row(trade_id=i, brain=0.1 if i > 5 else 0.7) for i in range(1, 11)]
        ids = set(range(1, 11))
        ctx = _ctx(elite_ids=ids, book_b_ids=ids, book_d_ids=ids)
        wf = build_waterfall(cands, ctx=ctx)
        for i in range(1, len(wf)):
            self.assertLessEqual(wf[i]["accepted_n"], wf[i - 1]["accepted_n"])


if __name__ == "__main__":
    unittest.main()
