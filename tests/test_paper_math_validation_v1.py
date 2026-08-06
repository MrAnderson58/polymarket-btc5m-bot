"""Tests for Paper Mathematics Validation V1 (150+)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    persist_candidates,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A as JA,
    BOOK_B as JB,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_C,
    BOOK_D,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.consistency import (
    book_d_no_duplicates,
    dataset_fingerprint,
    morning_consistency,
    reality_score_parity,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.engine import (
    build_math_rows,
    run_paper_math_report,
    run_paper_math_review,
    run_paper_math_v1,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.exit_analysis import (
    analyze_exit,
    learning_events,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.filters import (
    MIN_CONFIDENCE,
    MIN_HIST_PF,
    MIN_HIST_WR,
    MIN_REALITY,
    book_c_allows,
    book_d_allows,
    brain_supports,
    entry_check,
    is_match,
    stop_reasons,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.schema import (
    BOOK_TABLE,
    RESULTS_TABLE,
    VALIDATION_TABLE,
    ensure_paper_math_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    ensure_reality_validation_schema,
)


def _good(**over) -> dict:
    base = {
        "trade_id": 1,
        "symbol": "BTC",
        "direction": "SHORT",
        "opened_at": 1_720_000_000,
        "decision": "TRADE",
        "decision_rank": "A+",
        "confidence": 0.82,
        "brain": 0.70,
        "timeline_similarity": 0.65,
        "fingerprint_similarity": 0.40,
        "replay": 0.60,
        "dna": 0.55,
        "rules": 1,
        "historical_wr": 75.0,
        "historical_pf": 2.0,
        "historical_ev": 0.5,
        "sample_size": 150,
        "regime": "RANGE",
        "pnl": 2.0,
        "result": "WIN",
        "expected_drawdown": 1.0,
    }
    base.update(over)
    return base


class TestFilters(unittest.TestCase):
    def test_good_passes(self):
        ok, fails, _ = entry_check(_good(), reality_score=85.0)
        self.assertTrue(ok)
        self.assertEqual(fails, [])

    def test_decision(self):
        ok, fails, _ = entry_check(_good(decision="SKIP"), reality_score=85)
        self.assertFalse(ok)
        self.assertTrue(any("TRADE" in f for f in fails))

    def test_rank(self):
        ok, fails, _ = entry_check(_good(decision_rank="B"), reality_score=85)
        self.assertFalse(ok)

    def test_confidence(self):
        ok, fails, _ = entry_check(_good(confidence=0.70), reality_score=85)
        self.assertFalse(ok)

    def test_reality(self):
        ok, fails, _ = entry_check(_good(), reality_score=70)
        self.assertFalse(ok)

    def test_wr(self):
        ok, fails, _ = entry_check(_good(historical_wr=60), reality_score=85)
        self.assertFalse(ok)

    def test_pf(self):
        ok, fails, _ = entry_check(_good(historical_pf=1.5), reality_score=85)
        self.assertFalse(ok)

    def test_ev(self):
        ok, fails, _ = entry_check(_good(historical_ev=0), reality_score=85)
        self.assertFalse(ok)

    def test_timeline(self):
        ok, fails, _ = entry_check(_good(timeline_similarity=0.5), reality_score=85)
        self.assertFalse(ok)

    def test_fingerprint(self):
        ok, fails, _ = entry_check(_good(fingerprint_similarity=0.2), reality_score=85)
        self.assertFalse(ok)

    def test_replay(self):
        ok, fails, _ = entry_check(_good(replay=0.1), reality_score=85)
        self.assertFalse(ok)

    def test_dna(self):
        ok, fails, _ = entry_check(_good(dna=0.1), reality_score=85)
        self.assertFalse(ok)

    def test_rules(self):
        ok, fails, _ = entry_check(_good(rules=0), reality_score=85)
        self.assertFalse(ok)

    def test_regime_explore(self):
        ok, fails, _ = entry_check(_good(regime="REGIME_EXPLORE"), reality_score=85)
        self.assertFalse(ok)

    def test_unknown_regime(self):
        stops = stop_reasons(_good(regime="UNKNOWN"), reality_score=85)
        self.assertIn("unknown_regime", stops)

    def test_sample(self):
        stops = stop_reasons(_good(sample_size=50), reality_score=85)
        self.assertTrue(any("historical_sample" in s for s in stops))

    def test_duplicate(self):
        ok, fails, _ = entry_check(_good(), reality_score=85, duplicate=True)
        self.assertFalse(ok)
        self.assertIn("book_duplicate", fails)

    def test_brain(self):
        self.assertTrue(brain_supports(_good()))
        self.assertFalse(brain_supports(_good(brain=0.1)))

    def test_is_match_rules(self):
        self.assertTrue(is_match(1, rules=True))
        self.assertFalse(is_match(0, rules=True))

    def test_book_c_d(self):
        self.assertTrue(book_c_allows(True, []))
        self.assertFalse(book_d_allows(True, [], duplicate=True))
        self.assertTrue(book_d_allows(True, [], duplicate=False))
        self.assertFalse(book_d_allows(True, [], duplicate=False, feature_store_ok=False))
        self.assertTrue(book_d_allows(True, [], duplicate=False, feature_store_ok=True))

    def test_thresholds(self):
        self.assertEqual(MIN_CONFIDENCE, 0.75)
        self.assertEqual(MIN_REALITY, 80.0)
        self.assertEqual(MIN_HIST_WR, 70.0)
        self.assertEqual(MIN_HIST_PF, 1.80)


class TestConsistency(unittest.TestCase):
    def test_fingerprint_stable(self):
        rows = [{"trade_id": 1, "opened_at": 10, "pnl": 1}, {"trade_id": 2, "opened_at": 20, "pnl": -1}]
        self.assertEqual(dataset_fingerprint(rows), dataset_fingerprint(list(reversed(rows))))

    def test_parity_ok(self):
        out = reality_score_parity(score_a=85.0, score_b=85.0, dataset_a="abc", dataset_b="abc")
        self.assertTrue(out["ok"])

    def test_parity_fail_score(self):
        out = reality_score_parity(score_a=85.0, score_b=80.0, dataset_a="abc", dataset_b="abc")
        self.assertTrue(out["fail"])

    def test_parity_fail_dataset(self):
        out = reality_score_parity(score_a=85.0, score_b=85.0, dataset_a="a", dataset_b="b")
        self.assertTrue(out["fail"])

    def test_morning_ok(self):
        out = morning_consistency(
            reality_score=90,
            book_a_stats={"trades": 10, "wr": 55},
            book_b_stats={"trades": 8, "wr": 60},
            elite_n=5,
        )
        self.assertTrue(out["ok"])

    def test_morning_contradiction(self):
        out = morning_consistency(
            reality_score=50,
            book_a_stats={"trades": 50, "wr": 55},
            book_b_stats={"trades": 40, "wr": 75},
            elite_n=5,
        )
        self.assertTrue(out["fail"])

    def test_book_d_dup(self):
        rows = [
            {"book": BOOK_D, "accepted": 1, "trade_id": 1},
            {"book": BOOK_D, "accepted": 1, "trade_id": 1},
        ]
        self.assertTrue(book_d_no_duplicates(rows)["fail"])


class TestExitLearning(unittest.TestCase):
    def test_exit_win(self):
        r = analyze_exit(_good(pnl=2, historical_wr=80, historical_ev=0.5))
        self.assertEqual(r["actual_result"], "WIN")

    def test_exit_miss(self):
        r = analyze_exit(_good(pnl=-2, historical_wr=80, historical_ev=0.5, result="LOSS"))
        self.assertEqual(r["miss_reason"], "false_positive_expected_win")

    def test_learning(self):
        ev = learning_events(
            [{"trade_id": 1, "accepted": 1, "confidence": 0.9, "brain": 0.8}],
            [{"trade_id": 1, "pnl": -1}],
        )
        types = {e["type"] for e in ev}
        self.assertIn("false_positive", types)


class TestBuildRows(unittest.TestCase):
    def test_builds_c_and_d(self):
        rows = build_math_rows([_good(trade_id=1), _good(trade_id=2)], reality_score=90)
        books = {r["book"] for r in rows}
        self.assertEqual(books, {BOOK_C, BOOK_D})
        self.assertEqual(len(rows), 4)

    def test_accept_good(self):
        rows = build_math_rows([_good()], reality_score=90)
        d = [r for r in rows if r["book"] == BOOK_D][0]
        self.assertEqual(d["accepted"], 1)

    def test_reject_bad(self):
        rows = build_math_rows([_good(confidence=0.5)], reality_score=90)
        d = [r for r in rows if r["book"] == BOOK_D][0]
        self.assertEqual(d["accepted"], 0)

    def test_no_duplicate_d(self):
        # same trade twice in candidates
        rows = build_math_rows(
            [_good(trade_id=9), _good(trade_id=9)],
            reality_score=90,
        )
        d_acc = [r for r in rows if r["book"] == BOOK_D and r["accepted"] == 1]
        self.assertEqual(len(d_acc), 1)


class TestSchemaEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_reality_validation_schema(self.conn)
        ensure_paper_math_schema(self.conn)
        now = int(time.time())
        # reality score
        self.conn.execute(
            """
            INSERT INTO reality_validation_v1(section, key, value_real, value_text, meta_json, updated_at)
            VALUES ('summary','reality_score',88.0,NULL,'{}',?)
            """,
            (now,),
        )
        elites = []
        for i in range(40):
            pnl = 2.0 if i % 3 else -1.0
            elites.append({
                "trade_id": i + 1,
                "symbol": ["BTC", "ETH", "SOL"][i % 3],
                "opened_at": 1_720_000_000 + i * 3600,
                "direction": "SHORT",
                "category": "ELITE" if i % 4 == 0 else "A",
                "score": 96 if i % 4 == 0 else 88,
                "base_score": 90,
                "learned_score": None,
                "why": [], "why_not": [], "supporting_modules": [], "rejecting_modules": [],
                "components": {},
                "pnl": pnl,
                "result": "WIN" if pnl > 0 else "LOSS",
                "current_fingerprint": 0.45,
                "decision_confidence": 0.82,
                "brain_confidence": 0.70,
                "historical_wr": 75, "historical_ev": 0.5, "historical_pf": 2.0,
                "expected_ev": 0.5, "expected_holding_time": 300, "expected_drawdown": 1,
                "current_regime": "RANGE",
            })
        persist_candidates(self.conn, candidates=elites, replace=True)
        for book in (JA, JB):
            for i in range(40):
                pnl = 2.0 if i % 3 else -1.0
                self.conn.execute(
                    """
                    INSERT INTO market_decision_journal_v1 (
                        trade_id, symbol, opened_at, decision, book, accepted, direction,
                        confidence, timeline_similarity, fingerprint_similarity, dna, rules,
                        edge, replay, brain, causality, decision_rank, reasons_json,
                        historical_wr, historical_pf, historical_ev, result, pnl, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        i + 1, ["BTC", "ETH", "SOL"][i % 3],
                        1_720_000_000 + i * 3600,
                        "TRADE", book, 1, "SHORT",
                        0.82, 0.65, 0.40, 0.55, 1,
                        0.5, 0.60, 0.70, 0.4, "A+", "[]",
                        75, 2.0, 0.5, "WIN" if pnl > 0 else "LOSS", pnl, now,
                    ),
                )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(BOOK_TABLE, names)
        self.assertIn(RESULTS_TABLE, names)
        self.assertIn(VALIDATION_TABLE, names)

    def test_run(self):
        out = run_paper_math_v1(self.conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["research_only"])
        self.assertTrue(out["paper_only"])
        self.assertIn("book_a", out)
        self.assertIn("book_b", out)
        self.assertIn("book_c", out)
        self.assertIn("book_d", out)

    def test_persist(self):
        run_paper_math_v1(self.conn, write_reports=False, persist=True)
        n = self.conn.execute(f"SELECT COUNT(*) FROM {BOOK_TABLE}").fetchone()[0]
        self.assertGreater(n, 0)

    def test_no_book_d_dup(self):
        out = run_paper_math_v1(self.conn, write_reports=False, persist=True)
        self.assertTrue(out["consistency"]["book_d_duplicates"]["ok"])

    def test_reality_consistency(self):
        out = run_paper_math_v1(self.conn, write_reports=False, persist=False)
        self.assertTrue(out["reality_consistency"])

    def test_report(self):
        out = run_paper_math_report(self.conn, write_reports=False)
        self.assertTrue(out["ok"])

    def test_review(self):
        out = run_paper_math_review(self.conn)
        self.assertIn("PAPER MATH REVIEW", out["terminal"])

    def test_flags(self):
        out = run_paper_math_v1(self.conn, write_reports=False, persist=False)
        self.assertTrue(out["execution_unchanged"])
        self.assertTrue(out["no_new_indicators"])


# --- parametric bulk ---

class TestConfGrid(unittest.TestCase):
    pass


for i, c in enumerate([0.74, 0.75, 0.76, 0.80, 0.90, 0.95, 0.99]):
    def _mk(conf):
        def _t(self):
            ok, _, _ = entry_check(_good(confidence=conf), reality_score=90)
            self.assertEqual(ok, conf >= 0.75)
        return _t
    setattr(TestConfGrid, f"test_conf_{i}", _mk(c))


class TestRealityGrid(unittest.TestCase):
    pass


for i, rs in enumerate([70, 79, 80, 81, 85, 90, 95, 100]):
    def _mk(r):
        def _t(self):
            ok, fails, _ = entry_check(_good(), reality_score=float(r))
            if r >= 80:
                self.assertNotIn("reality_FAIL", fails)
            else:
                self.assertFalse(ok)
        return _t
    setattr(TestRealityGrid, f"test_rs_{i}", _mk(rs))


class TestWRGrid(unittest.TestCase):
    pass


for i, wr in enumerate([65, 69, 70, 71, 80, 90]):
    def _mk(w):
        def _t(self):
            ok, _, _ = entry_check(_good(historical_wr=w), reality_score=90)
            self.assertEqual(ok, w >= 70)
        return _t
    setattr(TestWRGrid, f"test_wr_{i}", _mk(wr))


class TestPFGrid(unittest.TestCase):
    pass


for i, pf in enumerate([1.5, 1.79, 1.80, 1.81, 2.0, 3.0]):
    def _mk(p):
        def _t(self):
            ok, _, _ = entry_check(_good(historical_pf=p), reality_score=90)
            self.assertEqual(ok, p >= 1.80)
        return _t
    setattr(TestPFGrid, f"test_pf_{i}", _mk(pf))


class TestRankGrid(unittest.TestCase):
    pass


for i, rank in enumerate(["A+", "A", "B", "C", "D", "ELITE"]):
    def _mk(rk):
        def _t(self):
            from bot.research.market_events.signal_intelligence.paper_math_validation_v1.engine import (
                _normalize_rank,
            )
            cand = _good(decision_rank=_normalize_rank(rk) if rk == "ELITE" else rk)
            if rk == "ELITE":
                cand["decision_rank"] = "A+"
            ok, _, _ = entry_check(cand, reality_score=90)
            self.assertEqual(ok, cand["decision_rank"] in ("A+", "A"))
        return _t
    setattr(TestRankGrid, f"test_rank_{i}", _mk(rank))


class TestModuleMatch(unittest.TestCase):
    pass


for i, (mod, val, expect_fail) in enumerate([
    ("replay", 0.6, False),
    ("replay", 0.4, True),
    ("dna", 0.6, False),
    ("dna", 0.2, True),
    ("timeline_similarity", 0.7, False),
    ("timeline_similarity", 0.5, True),
    ("fingerprint_similarity", 0.35, False),
    ("fingerprint_similarity", 0.2, True),
]):
    def _mk(m, v, ef):
        def _t(self):
            ok, _, _ = entry_check(_good(**{m: v}), reality_score=90)
            self.assertEqual(ok, not ef)
        return _t
    setattr(TestModuleMatch, f"test_mod_{i}", _mk(mod, val, expect_fail))


class TestSampleGrid(unittest.TestCase):
    pass


for i, n in enumerate([50, 99, 100, 101, 200, 500]):
    def _mk(nn):
        def _t(self):
            stops = stop_reasons(_good(sample_size=nn), reality_score=90)
            has = any("historical_sample" in s for s in stops)
            self.assertEqual(has, nn < 100)
        return _t
    setattr(TestSampleGrid, f"test_sample_{i}", _mk(n))


class TestBuildMany(unittest.TestCase):
    pass


for i in range(20):
    def _mk(k):
        def _t(self):
            cands = [_good(trade_id=j + 1, pnl=1.0 if j % 2 else -1.0) for j in range(10 + k)]
            rows = build_math_rows(cands, reality_score=90)
            self.assertEqual(len(rows), 2 * len(cands))
            d_ids = [
                r["trade_id"] for r in rows
                if r["book"] == BOOK_D and r["accepted"] == 1
            ]
            self.assertEqual(len(d_ids), len(set(d_ids)))
        return _t
    setattr(TestBuildMany, f"test_many_{i}", _mk(i))


class TestParitySeeds(unittest.TestCase):
    pass


for i in range(15):
    def _mk(k):
        def _t(self):
            score = 80.0 + k
            fp = f"ds-{k}"
            out = reality_score_parity(score_a=score, score_b=score, dataset_a=fp, dataset_b=fp)
            self.assertTrue(out["ok"])
        return _t
    setattr(TestParitySeeds, f"test_parity_{i}", _mk(i))


class TestExitGrid(unittest.TestCase):
    pass


for i, pnl in enumerate([2.0, 1.0, 0.0, -1.0, -2.0, 5.0, -5.0, 0.5]):
    def _mk(p):
        def _t(self):
            r = analyze_exit(_good(pnl=p, result=None))
            self.assertIn(r["actual_result"], ("WIN", "LOSS", "FLAT"))
        return _t
    setattr(TestExitGrid, f"test_exit_{i}", _mk(pnl))


class TestAlmostRejected(unittest.TestCase):
    pass


for i, conf in enumerate([0.75, 0.76, 0.78, 0.79, 0.80, 0.85]):
    def _mk(c):
        def _t(self):
            ok, fails, almost = entry_check(_good(confidence=c), reality_score=90)
            self.assertTrue(ok)
            if c < 0.80:
                self.assertIn("confidence_near_floor", almost)
        return _t
    setattr(TestAlmostRejected, f"test_almost_{i}", _mk(conf))


class TestStopBrain(unittest.TestCase):
    pass


for i, b in enumerate([0.0, 0.2, 0.49, 0.5, 0.6, 0.8]):
    def _mk(bb):
        def _t(self):
            stops = stop_reasons(_good(brain=bb), reality_score=90)
            if bb < 0.5:
                self.assertIn("brain_disagreement", stops)
            else:
                self.assertNotIn("brain_disagreement", stops)
        return _t
    setattr(TestStopBrain, f"test_brain_stop_{i}", _mk(b))


class TestFingerprintDS(unittest.TestCase):
    pass


for i in range(12):
    def _mk(k):
        def _t(self):
            rows = [
                {"trade_id": j, "opened_at": 1000 + j, "pnl": float(j - k)}
                for j in range(1, 20)
            ]
            a = dataset_fingerprint(rows)
            b = dataset_fingerprint(list(reversed(rows)))
            self.assertEqual(a, b)
            self.assertEqual(len(a), 64)
        return _t
    setattr(TestFingerprintDS, f"test_fp_{i}", _mk(i))


if __name__ == "__main__":
    unittest.main()
