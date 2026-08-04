"""test_decision_books + high_confidence + overlap — Paper Decision A/B/C."""

from __future__ import annotations

import time
import unittest

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
    C_MIN_CONFIDENCE,
    C_MIN_FINGERPRINT_SIM,
    C_MIN_RULES_MATCHED,
    C_MIN_TIMELINE_SIM,
    book_a_accepts,
    book_b_accepts,
    book_c_accepts,
    extract_module_signals,
    rejection_reasons,
    route_decision,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_overlap,
    book_stats_from_pnls,
    book_stats_from_rows,
    improvement,
    partition_by_book,
    trade_id_set,
)


def _decision(
    *,
    trade: bool = True,
    conf: float = 0.8,
    tl: float = 80.0,
    fp: float = 70.0,
    rules_pass: bool = True,
    tid: int = 1,
) -> dict:
    return {
        "trade_id": tid,
        "symbol": "BTC",
        "decision": "TRADE" if trade else "NO TRADE",
        "direction": "LONG" if trade else None,
        "confidence": conf,
        "why": ["ok"] if trade else ["no edge"],
        "historical_wr": 60.0,
        "historical_pf": 1.5,
        "historical_ev": 1.0,
        "scores": {
            "fingerprint": {"pass": True, "similarity_pct": fp, "ok": True},
            "timeline": {"pass": True, "similarity_pct": tl, "ok": True},
            "dna": {"pass": True, "similarity_pct": 75, "ok": True},
            "rules": {"pass": rules_pass, "blocked": False, "rule": "R1", "ok": True},
            "edge": {"pass": True, "similarity_pct": 70, "ok": True},
            "replay": {"pass": True, "similarity_pct": 65, "ok": True},
            "brain": {"pass": True, "confidence": conf, "ok": True},
            "causality": {"pass": True, "similarity_pct": 60, "ok": True},
        },
    }


class TestBookA(unittest.TestCase):
    def test_accepts_trade(self):
        self.assertTrue(book_a_accepts(_decision(trade=True)))

    def test_accepts_no_trade(self):
        self.assertTrue(book_a_accepts(_decision(trade=False)))

    def test_route_a_always(self):
        self.assertTrue(route_decision(_decision(trade=False))[BOOK_A])


class TestBookB(unittest.TestCase):
    def test_accepts_trade(self):
        self.assertTrue(book_b_accepts(_decision(trade=True)))

    def test_rejects_no_trade(self):
        self.assertFalse(book_b_accepts(_decision(trade=False)))

    def test_route_b_trade(self):
        r = route_decision(_decision(trade=True))
        self.assertTrue(r[BOOK_B])

    def test_route_b_no_trade(self):
        r = route_decision(_decision(trade=False))
        self.assertFalse(r[BOOK_B])

    def test_rejection_reasons_b(self):
        rs = rejection_reasons(_decision(trade=False), BOOK_B)
        self.assertTrue(rs)


class TestHighConfidenceFilter(unittest.TestCase):
    def test_c_accepts_when_all_pass(self):
        self.assertTrue(book_c_accepts(_decision()))

    def test_c_rejects_no_trade(self):
        self.assertFalse(book_c_accepts(_decision(trade=False)))

    def test_c_rejects_low_conf(self):
        self.assertFalse(book_c_accepts(_decision(conf=0.5)))

    def test_c_rejects_low_timeline(self):
        self.assertFalse(book_c_accepts(_decision(tl=50)))

    def test_c_rejects_low_fingerprint(self):
        self.assertFalse(book_c_accepts(_decision(fp=40)))

    def test_c_rejects_no_rules(self):
        self.assertFalse(book_c_accepts(_decision(rules_pass=False)))

    def test_c_boundary_conf(self):
        self.assertTrue(book_c_accepts(_decision(conf=C_MIN_CONFIDENCE)))

    def test_c_boundary_tl(self):
        self.assertTrue(book_c_accepts(_decision(tl=C_MIN_TIMELINE_SIM * 100)))

    def test_c_boundary_fp(self):
        self.assertTrue(book_c_accepts(_decision(fp=C_MIN_FINGERPRINT_SIM * 100)))

    def test_c_rejection_reasons(self):
        rs = rejection_reasons(_decision(conf=0.4, rules_pass=False), BOOK_C)
        self.assertTrue(any("confidence" in r for r in rs))
        self.assertTrue(any("rules" in r for r in rs))

    def test_thresholds_constants(self):
        self.assertEqual(C_MIN_CONFIDENCE, 0.70)
        self.assertEqual(C_MIN_TIMELINE_SIM, 0.70)
        self.assertEqual(C_MIN_FINGERPRINT_SIM, 0.60)
        self.assertEqual(C_MIN_RULES_MATCHED, 1)


class TestExtractSignals(unittest.TestCase):
    def test_sim_scale(self):
        sig = extract_module_signals(_decision(tl=80, fp=60))
        self.assertAlmostEqual(sig["timeline_similarity"], 0.8)
        self.assertAlmostEqual(sig["fingerprint_similarity"], 0.6)
        self.assertEqual(sig["rules"], 1)

    def test_rules_zero_when_fail(self):
        sig = extract_module_signals(_decision(rules_pass=False))
        self.assertEqual(sig["rules"], 0)

    def test_route_perf_under_1ms(self):
        d = _decision()
        t0 = time.perf_counter()
        for _ in range(1000):
            route_decision(d)
        ms = (time.perf_counter() - t0) * 1000.0 / 1000.0
        self.assertLess(ms, 1.0)


class TestBookOverlap(unittest.TestCase):
    def _rows(self, book, ids, accepted=True):
        return [
            {"book": book, "trade_id": i, "accepted": 1 if accepted else 0, "pnl": 1.0}
            for i in ids
        ]

    def test_overlap_counts(self):
        a = self._rows(BOOK_A, [1, 2, 3, 4])
        b = self._rows(BOOK_B, [1, 2])
        c = self._rows(BOOK_C, [1])
        ov = book_overlap(a, b, c)
        self.assertEqual(ov["a_and_b"], 2)
        self.assertEqual(ov["a_and_c"], 1)
        self.assertEqual(ov["b_and_c"], 1)
        self.assertEqual(ov["filtered_b"], 2)
        self.assertEqual(ov["filtered_c"], 3)

    def test_trade_id_set_skips_rejected(self):
        rows = self._rows(BOOK_B, [1, 2], accepted=True) + self._rows(BOOK_B, [3], accepted=False)
        self.assertEqual(trade_id_set(rows), {1, 2})

    def test_partition(self):
        rows = self._rows(BOOK_A, [1]) + self._rows(BOOK_B, [2]) + self._rows(BOOK_C, [3])
        p = partition_by_book(rows)
        self.assertEqual(len(p[BOOK_A]), 1)
        self.assertEqual(len(p[BOOK_B]), 1)
        self.assertEqual(len(p[BOOK_C]), 1)

    def test_stats_from_pnls(self):
        s = book_stats_from_pnls([1.0, -0.5, 2.0])
        self.assertEqual(s["trades"], 3)
        self.assertIsNotNone(s["wr"])

    def test_stats_from_rows(self):
        rows = [
            {"accepted": 1, "pnl": 1.0},
            {"accepted": 1, "pnl": -1.0},
            {"accepted": 0, "pnl": None},
        ]
        s = book_stats_from_rows(rows)
        self.assertEqual(s["trades"], 2)

    def test_improvement(self):
        base = {"wr": 50.0, "pf": 1.0, "ev": 0.5, "sharpe": 0.1, "trades": 100}
        other = {"wr": 60.0, "pf": 1.5, "ev": 1.0, "sharpe": 0.2, "trades": 40}
        imp = improvement(base, other)
        self.assertEqual(imp["wr_delta"], 10.0)
        self.assertEqual(imp["trades_delta"], -60)

    def test_empty_overlap(self):
        ov = book_overlap([], [], [])
        self.assertEqual(ov["a_and_b"], 0)


class TestDecisionBooksIds(unittest.TestCase):
    def test_book_ids(self):
        self.assertEqual(BOOK_A, "paper_baseline")
        self.assertEqual(BOOK_B, "paper_decision")
        self.assertEqual(BOOK_C, "paper_high_confidence")

    def test_route_all_three_keys(self):
        r = route_decision(_decision())
        self.assertEqual(set(r), {BOOK_A, BOOK_B, BOOK_C})


if __name__ == "__main__":
    unittest.main()
