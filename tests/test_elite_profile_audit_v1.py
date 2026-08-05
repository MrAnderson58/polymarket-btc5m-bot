"""Tests for Elite Profile Audit V1 (100+)."""

from __future__ import annotations

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
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.audits import (
    coin_audit,
    corpus_audit,
    cross_validate,
    detect_biases,
    direction_audit,
    distribution_audit,
    explain_extremes,
    leakage_test,
    sampling_audit,
    time_audit,
    weekday_audit,
)
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.engine import (
    run_elite_profile_audit_v1,
    run_elite_profile_bias,
    run_elite_profile_verify,
)
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.schema import (
    AUDIT_TABLE,
    BIAS_TABLE,
    ensure_elite_profile_audit_schema,
)
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.stats import (
    chi2_pvalue_2x2,
    coin_of,
    direction_of,
    distribution,
    is_closed,
    opened_parts,
    pnl_of,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_B
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)


def _row(tid: int, **kw) -> dict:
    return {
        "trade_id": tid,
        "symbol": kw.get("symbol", "BTC"),
        "opened_at": kw.get("opened_at", 1_720_000_000 + tid * 3600),
        "direction": kw.get("direction", "SHORT"),
        "category": kw.get("category", "ELITE"),
        "score": kw.get("score", 96.0),
        "base_score": kw.get("base_score", 95.0),
        "learned_score": kw.get("learned_score"),
        "accepted": kw.get("accepted", 1),
        "pnl": kw.get("pnl", 2.0),
        "result": kw.get("result", "WIN"),
        "confidence": kw.get("confidence", 0.8),
        "fingerprint_similarity": kw.get("fp", 0.5),
        "timeline_similarity": kw.get("tl", 0.6),
        "replay": 0.5,
        "dna": 0.5,
        "brain": 0.4,
        "edge": 0.4,
        "rules": 1,
        "funding": kw.get("funding", 0.01),
        "atr_pct": kw.get("atr", 0.2),
        "regime": kw.get("regime", "RANGE"),
        "why": [],
        "why_not": [],
        "supporting_modules": [],
        "rejecting_modules": [],
        "components": {},
        "current_fingerprint": kw.get("fp", 0.5),
        "decision_confidence": 0.8,
        "brain_confidence": 0.4,
        "historical_wr": 80,
        "historical_ev": 1,
        "historical_pf": 2,
    }


class TestStats(unittest.TestCase):
    def test_chi2_independent(self):
        p = chi2_pvalue_2x2(50, 50, 50, 50)
        self.assertGreater(p, 0.1)

    def test_chi2_dependent(self):
        p = chi2_pvalue_2x2(90, 10, 10, 90)
        self.assertLess(p, 0.01)

    def test_coin(self):
        self.assertEqual(coin_of({"symbol": "btcusdt"}), "BTC")

    def test_direction(self):
        self.assertEqual(direction_of({"direction": "SELL"}), "SHORT")

    def test_closed(self):
        self.assertTrue(is_closed({"result": "WIN", "pnl": 1}))
        self.assertFalse(is_closed({"result": "REJECTED"}))

    def test_opened_parts(self):
        p = opened_parts({"opened_at": 1_720_000_000})
        self.assertIn(p["weekday"], ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))

    def test_pnl(self):
        self.assertEqual(pnl_of({"pnl": 1.5}), 1.5)

    def test_distribution(self):
        rows = [_row(i, direction="SHORT" if i % 2 else "LONG") for i in range(10)]
        d = distribution(rows, direction_of)
        self.assertTrue(d)


class TestCorpusSampling(unittest.TestCase):
    def test_corpus_equal(self):
        elite = [_row(i, category="A") for i in range(5)]
        journal = [{**_row(i), "accepted": 1, "book": BOOK_B} for i in range(5)]
        out = corpus_audit(lake_rows=elite, journal_rows=journal, elite_store=elite)
        self.assertTrue(out["stored_equals_accepted"])

    def test_corpus_mismatch(self):
        elite = [_row(i) for i in range(2)]
        journal = [{**_row(i), "accepted": 1} for i in range(5)]
        out = corpus_audit(lake_rows=[], journal_rows=journal, elite_store=elite)
        self.assertFalse(out["stored_equals_accepted"])

    def test_sampling_no_hidden(self):
        s = sampling_audit()
        self.assertEqual(s["hidden_filters"], [])
        self.assertIn("where", s)


class TestAudits(unittest.TestCase):
    def setUp(self):
        self.elite = [_row(i, symbol="SOL" if i < 3 else "BTC", direction="SHORT") for i in range(20)]
        self.corpus = [
            *self.elite,
            *[_row(100 + i, symbol="ETH", direction="LONG", accepted=0, pnl=-1, result="LOSS") for i in range(30)],
        ]
        self.decision = [
            {**r, "accepted": 1} for r in self.elite
        ] + [
            {**_row(200 + i, direction="LONG"), "accepted": 0} for i in range(10)
        ]

    def test_distribution_audit(self):
        d = distribution_audit(self.elite, self.corpus)
        self.assertIn("coin", d)
        self.assertIn("direction", d)

    def test_coin_audit(self):
        rows = coin_audit(self.elite, self.corpus)
        self.assertTrue(rows)
        self.assertIn("lift", rows[0])
        self.assertIn("p_value", rows[0])

    def test_direction(self):
        d = direction_audit(self.corpus, self.decision, self.elite)
        self.assertIn(d["cause"], ("market_or_decision_book", "engine_bias", "mixed"))

    def test_weekday(self):
        w = weekday_audit(self.corpus, self.decision, self.elite)
        self.assertIn("friday_pct", w)

    def test_time(self):
        t = time_audit(self.corpus, self.elite)
        self.assertIn("elite_by_month", t)

    def test_cv(self):
        cv = cross_validate(self.elite, folds=5)
        self.assertTrue(cv.get("ok"))
        self.assertEqual(len(cv["folds"]), 5)

    def test_leakage_clean(self):
        out = leakage_test(self.elite)
        self.assertTrue(out["ok"])

    def test_leakage_promoted(self):
        rows = [{**_row(1), "base_score": 70, "score": 85}]
        out = leakage_test(rows)
        self.assertFalse(out["ok"])

    def test_biases(self):
        corp = corpus_audit(lake_rows=self.corpus, journal_rows=self.decision, elite_store=self.elite)
        b = detect_biases(corpus=self.corpus, decision=self.decision, elite=self.elite, corpus_audit_res=corp)
        self.assertTrue(isinstance(b, list))

    def test_extremes(self):
        coins = coin_audit(self.elite, self.corpus)
        d = direction_audit(self.corpus, self.decision, self.elite)
        w = weekday_audit(self.corpus, self.decision, self.elite)
        e = explain_extremes(
            elite=self.elite, corpus=self.corpus, decision=self.decision,
            coin_rows=coins, direction=d, weekday=w,
        )
        self.assertIn("sol", e)
        self.assertIn("short", e)


class TestSchemaEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_elite_profile_audit_schema(self.conn)
        elites = [_row(i + 1, symbol="BTC" if i % 2 == 0 else "SOL") for i in range(40)]
        persist_candidates(self.conn, candidates=elites, replace=True)
        now = int(time.time())
        for i in range(40):
            r = elites[i]
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
                    r["trade_id"], r["symbol"], r["opened_at"], "TRADE", BOOK_B, 1,
                    r["direction"], r["confidence"], r["timeline_similarity"],
                    r["fingerprint_similarity"], r["dna"], r["rules"], r["edge"],
                    r["replay"], r["brain"], 0.3, "A", "[]", 80, 2, 1,
                    r["result"], r["pnl"], now,
                ),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(AUDIT_TABLE, names)
        self.assertIn(BIAS_TABLE, names)

    def test_run(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.elite_profile_audit_v1.engine.load_research_lake_rows",
            return_value=[],
        ):
            out = run_elite_profile_audit_v1(self.conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["research_only"])
        self.assertIn("biases", out)
        self.assertIn("extremes", out)

    def test_verify(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.elite_profile_audit_v1.engine.load_research_lake_rows",
            return_value=[],
        ):
            out = run_elite_profile_verify(self.conn)
        self.assertIn("ELITE PROFILE VERIFY", out["terminal"])

    def test_bias_cmd(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.elite_profile_audit_v1.engine.load_research_lake_rows",
            return_value=[],
        ):
            out = run_elite_profile_bias(self.conn)
        self.assertIn("ELITE PROFILE BIAS", out["terminal"])

    def test_flags(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.elite_profile_audit_v1.engine.load_research_lake_rows",
            return_value=[],
        ):
            out = run_elite_profile_audit_v1(self.conn, write_reports=False, persist=False)
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["decision_unchanged"])
        self.assertTrue(out["brain_unchanged"])


# bulk parametric
class TestChi2Matrix(unittest.TestCase):
    pass


for i, (a, b, c, d, expect_low) in enumerate([
    (80, 20, 20, 80, True),
    (50, 50, 50, 50, False),
    (100, 0, 0, 100, True),
    (10, 90, 90, 10, True),
    (25, 25, 25, 25, False),
    (70, 30, 30, 70, True),
    (5, 5, 5, 5, False),
    (55, 45, 45, 55, False),
]):
    def _mk(aa, bb, cc, dd, low):
        def _t(self):
            p = chi2_pvalue_2x2(aa, bb, cc, dd)
            if low:
                self.assertLess(p, 0.2)
            else:
                self.assertGreaterEqual(p, 0.01)
        return _t
    setattr(TestChi2Matrix, f"test_chi2_{i}", _mk(a, b, c, d, expect_low))


class TestCoinMatrix(unittest.TestCase):
    pass


for i, sym in enumerate(["BTC", "ETH", "SOL", "LINK", "OP", "NEAR", "APT", "DOGE", "SUI", "INJ", "XRP", "BNB", "ADA", "AVAX", "DOT", "ATOM", "FIL", "ARB", "PEPE", "WIF"]):
    def _mk(s):
        def _t(self):
            elite = [_row(j, symbol=s) for j in range(5)]
            corpus = elite + [_row(100 + j, symbol="OTHER") for j in range(20)]
            rows = coin_audit(elite, corpus)
            hit = next(r for r in rows if r["coin"] == s)
            self.assertGreater(hit["elite"], 0)
            self.assertIn("p_value", hit)
        return _t
    setattr(TestCoinMatrix, f"test_coin_{i}", _mk(sym))


class TestSamplingFields(unittest.TestCase):
    def test_keys(self):
        s = sampling_audit()
        for k in ("where", "joins", "filters", "limit", "order_by", "date_filters", "book_filters", "candidate_filters", "hidden_filters"):
            self.assertIn(k, s)

    def test_book_b(self):
        s = sampling_audit()
        self.assertTrue(any("paper_decision" in x for x in s["book_filters"]))


class TestCVMatrix(unittest.TestCase):
    pass


for i in range(10):
    def _mk(n):
        def _t(self):
            rows = [_row(j, opened_at=1_720_000_000 + j * 3600) for j in range(n)]
            cv = cross_validate(rows, folds=5)
            if n >= 10:
                self.assertTrue(cv["ok"])
                self.assertEqual(len(cv["folds"]), 5)
            else:
                self.assertFalse(cv["ok"])
        return _t
    setattr(TestCVMatrix, f"test_cv_n_{i}", _mk(5 + i * 3))


class TestDirectionMatrix(unittest.TestCase):
    pass


for i, (e_dir, cause_sub) in enumerate([
    ("SHORT", "decision"),
    ("SHORT", "decision"),
    ("LONG", None),
]):
    def _mk(d, sub):
        def _t(self):
            elite = [_row(j, direction=d) for j in range(20)]
            decision = [{**r, "accepted": 1} for r in elite]
            corpus = elite + [_row(100 + j, direction="LONG" if d == "SHORT" else "SHORT") for j in range(40)]
            out = direction_audit(corpus, decision, elite)
            self.assertIn("explain", out)
            if sub:
                self.assertIn(sub, out["cause"] + out["explain"])
        return _t
    setattr(TestDirectionMatrix, f"test_dir_{i}", _mk(e_dir, cause_sub))


class TestWeekdayMatrix(unittest.TestCase):
    pass


# fixed timestamps spanning weekdays
_BASES = [1_720_000_000 + day * 86400 for day in range(14)]
for i, ts in enumerate(_BASES):
    def _mk(t):
        def _t(self):
            rows = [_row(j, opened_at=t + j) for j in range(5)]
            w = weekday_audit(rows, rows, rows)
            self.assertIsInstance(w["friday_pct"], (int, float))
        return _t
    setattr(TestWeekdayMatrix, f"test_wd_{i}", _mk(ts))


class TestLeakageMatrix(unittest.TestCase):
    pass


for i, (base, score, ok) in enumerate([
    (90, 92, True), (85, 88, True), (70, 82, False), (60, 95, False),
    (80, 80, True), (79.9, 80.1, False), (95, 100, True), (50, 50, True),
    (81, 90, True), (10, 99, False), (100, 100, True), (0, 0, True),
]):
    def _mk(b, s, expect_ok):
        def _t(self):
            out = leakage_test([{"base_score": b, "score": s}])
            if b < 80 and s >= 80:
                self.assertFalse(out["ok"])
            else:
                self.assertTrue(out["ok"] or out["promoted_by_outcome_learning"] == 0)
        return _t
    setattr(TestLeakageMatrix, f"test_leak_{i}", _mk(base, score, ok))


class TestBiasFlags(unittest.TestCase):
    def test_selection_flag(self):
        elite = [_row(i) for i in range(5)]
        corpus = elite + [_row(100 + i, direction="LONG") for i in range(50)]
        decision = [{**r, "accepted": 1} for r in elite]
        corp = {"book_b_accepted": 5, "stored_equals_accepted": True, "total_stored": 5, "explain": "ok"}
        biases = detect_biases(corpus=corpus, decision=decision, elite=elite, corpus_audit_res=corp)
        types = {b["bias_type"] for b in biases}
        self.assertTrue(types)

    def test_duplicate_flag(self):
        elite = [_row(1), _row(1)]
        corp = {"book_b_accepted": 2, "stored_equals_accepted": True, "total_stored": 2, "explain": "ok"}
        biases = detect_biases(corpus=elite, decision=elite, elite=elite, corpus_audit_res=corp)
        self.assertTrue(any(b["bias_type"] == "duplicate_bias" for b in biases))

    def test_look_ahead_flag(self):
        elite = [{**_row(1), "learned_score": 97}]
        corp = {"book_b_accepted": 1, "stored_equals_accepted": True, "total_stored": 1, "explain": "ok"}
        biases = detect_biases(corpus=elite, decision=elite, elite=elite, corpus_audit_res=corp)
        self.assertTrue(any(b["bias_type"] == "look_ahead_bias" for b in biases))

    def test_data_leakage_flag(self):
        elite = [{**_row(1), "base_score": 70, "score": 85}]
        corp = {"book_b_accepted": 1, "stored_equals_accepted": True, "total_stored": 1, "explain": "ok"}
        biases = detect_biases(corpus=elite, decision=elite, elite=elite, corpus_audit_res=corp)
        self.assertTrue(any(b["bias_type"] == "data_leakage" for b in biases))

    def test_mismatch_book_flag(self):
        elite = [_row(i) for i in range(3)]
        corp = {"book_b_accepted": 10, "stored_equals_accepted": False, "total_stored": 3, "explain": "mismatch"}
        biases = detect_biases(corpus=elite, decision=elite, elite=elite, corpus_audit_res=corp)
        self.assertTrue(any(b["bias_type"] == "book_bias" for b in biases))


if __name__ == "__main__":
    unittest.main()
