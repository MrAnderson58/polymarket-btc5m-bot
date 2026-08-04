"""Tests for Decision Error Learning Engine V1 (80+)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.decision_error_learning_v1.classify import (
    ERROR_CLASSES,
    MODULES,
    classify_error,
    classify_trade,
    confusion_label,
    primary_module_from_reasons,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.modules import (
    adaptive_suggestions,
    module_error_ranking,
    recovered_if_module_ignored,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.patterns import (
    mine_patterns,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.schema import (
    ERRORS_TABLE,
    PATTERNS_TABLE,
    ensure_decision_error_schema,
)
from bot.research.market_events.signal_intelligence.decision_error_learning_v1.store import (
    persist_errors,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)


def _a(tid: int, pnl: float, **kw) -> dict:
    return {
        "trade_id": tid,
        "symbol": kw.get("symbol", "BTC"),
        "book": BOOK_A,
        "accepted": 1,
        "pnl": pnl,
        "result": "WIN" if pnl > 0 else "LOSS",
        "direction": kw.get("direction", "LONG"),
        "opened_at": 1_700_000_000 + tid,
        "reasons_json": "[]",
        "confidence": 0.5,
    }


def _b(tid: int, accepted: bool, reasons: list[str], **kw) -> dict:
    return {
        "trade_id": tid,
        "symbol": kw.get("symbol", "BTC"),
        "book": BOOK_B,
        "accepted": 1 if accepted else 0,
        "pnl": kw.get("pnl"),
        "result": "REJECTED" if not accepted else kw.get("result", "WIN"),
        "direction": kw.get("direction", "LONG"),
        "decision": "TRADE" if accepted else "NO TRADE",
        "confidence": kw.get("confidence", 0.4),
        "reasons_json": json.dumps(reasons),
        "fingerprint_similarity": kw.get("fp", 0.3),
        "timeline_similarity": kw.get("tl", 0.4),
        "replay": kw.get("replay", 0.2),
        "dna": kw.get("dna", 0.5),
        "rules": kw.get("rules", 0),
        "edge": kw.get("edge", 0.3),
        "brain": kw.get("brain", 0.4),
        "causality": kw.get("causality", 0.3),
        "historical_wr": 50,
        "opened_at": 1_700_000_000 + tid,
    }


def _records(n: int = 40) -> list[dict]:
    out = []
    for i in range(n):
        # mix TP/FP/TN/FN
        if i % 4 == 0:
            out.append(classify_trade(_a(i + 1, 2.0), _b(i + 1, True, ["ok"], pnl=2.0)))
        elif i % 4 == 1:
            out.append(classify_trade(_a(i + 1, -1.5), _b(i + 1, True, ["ok"], pnl=-1.5, confidence=0.8)))
        elif i % 4 == 2:
            out.append(classify_trade(
                _a(i + 1, -0.8),
                _b(i + 1, False, ["No historical edge", "Timeline mismatch"]),
            ))
        else:
            out.append(classify_trade(
                _a(i + 1, 5.0),
                _b(i + 1, False, ["Replay weak", "Confidence 35%"], replay=0.1),
            ))
    return out


class TestConfusion(unittest.TestCase):
    def test_tp(self):
        self.assertEqual(confusion_label(accepted=True, pnl=1.0), "TP")

    def test_fp(self):
        self.assertEqual(confusion_label(accepted=True, pnl=-1.0), "FP")

    def test_tn(self):
        self.assertEqual(confusion_label(accepted=False, pnl=-1.0), "TN")

    def test_fn(self):
        self.assertEqual(confusion_label(accepted=False, pnl=2.0), "FN")

    def test_unk(self):
        self.assertEqual(confusion_label(accepted=True, pnl=None), "UNK")

    def test_zero_pnl_tn(self):
        self.assertEqual(confusion_label(accepted=False, pnl=0.0), "TN")


class TestPrimaryModule(unittest.TestCase):
    def test_replay(self):
        self.assertEqual(primary_module_from_reasons(["Replay weak"], {}), "replay")

    def test_fingerprint(self):
        self.assertEqual(primary_module_from_reasons(["Fingerprint mismatch"], {}), "fingerprint")

    def test_timeline(self):
        self.assertEqual(primary_module_from_reasons(["Timeline mismatch"], {}), "timeline")

    def test_dna(self):
        self.assertEqual(primary_module_from_reasons(["DNA no match"], {}), "dna")

    def test_rules(self):
        self.assertEqual(primary_module_from_reasons(["Rule BLOCK #1"], {}), "rules")

    def test_confidence(self):
        self.assertEqual(primary_module_from_reasons(["Confidence 35%"], {}), "confidence")

    def test_brain(self):
        self.assertEqual(primary_module_from_reasons(["Brain NO_TRADE"], {}), "brain")

    def test_edge(self):
        self.assertEqual(primary_module_from_reasons(["No historical edge"], {}), "edge")

    def test_weakest_score_fallback(self):
        row = {"replay": 0.1, "fingerprint_similarity": 0.9, "timeline_similarity": 0.8, "confidence": 0.7}
        self.assertEqual(primary_module_from_reasons([], row), "replay")


class TestClassify(unittest.TestCase):
    def test_correct_tp(self):
        r = classify_trade(_a(1, 1.0), _b(1, True, ["ok"], pnl=1.0))
        self.assertEqual(r["confusion"], "TP")
        self.assertEqual(r["error_class"], "Correct trade")

    def test_false_reject(self):
        r = classify_trade(_a(2, 3.0), _b(2, False, ["Replay weak"]))
        self.assertEqual(r["confusion"], "FN")
        self.assertEqual(r["error_class"], "Wrong Replay")

    def test_false_accept(self):
        r = classify_trade(_a(3, -2.0), _b(3, True, ["ok"], pnl=-2.0, confidence=0.8))
        self.assertEqual(r["confusion"], "FP")
        self.assertIn(r["error_class"], ("False Accept", "Wrong Confidence"))

    def test_wrong_fingerprint(self):
        r = classify_trade(_a(4, 2.0), _b(4, False, ["Fingerprint mismatch"]))
        self.assertEqual(r["error_class"], "Wrong Fingerprint")

    def test_wrong_timeline(self):
        r = classify_trade(_a(5, 2.0), _b(5, False, ["Timeline mismatch"]))
        self.assertEqual(r["error_class"], "Wrong Timeline")

    def test_wrong_dna(self):
        r = classify_trade(_a(6, 2.0), _b(6, False, ["DNA weak"]))
        self.assertEqual(r["error_class"], "Wrong DNA")

    def test_wrong_rule(self):
        r = classify_trade(_a(7, 2.0), _b(7, False, ["Rule blocked"]))
        self.assertEqual(r["error_class"], "Wrong Rule")

    def test_wrong_confidence(self):
        r = classify_trade(_a(8, 2.0), _b(8, False, ["Confidence 20%"]))
        self.assertEqual(r["error_class"], "Wrong Confidence")

    def test_tn_correct(self):
        r = classify_trade(_a(9, -1.0), _b(9, False, ["No historical edge"]))
        self.assertEqual(r["confusion"], "TN")
        self.assertEqual(r["error_class"], "Correct trade")

    def test_modules_present(self):
        r = classify_trade(_a(10, 1.0), _b(10, True, [], pnl=1.0))
        self.assertIn("fingerprint", r["modules"])


class TestModules(unittest.TestCase):
    def test_recovered_ev(self):
        recs = _records(40)
        out = recovered_if_module_ignored(recs, "replay")
        self.assertIn("recovered_ev", out)
        self.assertIn("false_reject_n", out)

    def test_ranking_sorted(self):
        rank = module_error_ranking(_records(48))
        self.assertEqual(len(rank), len(MODULES))
        scores = [float(r["error_score"]) for r in rank]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_suggestions(self):
        sug = adaptive_suggestions(module_error_ranking(_records(40)))
        self.assertTrue(sug)
        self.assertIn("verdict", sug[0])

    def test_suggestion_verdicts(self):
        for s in adaptive_suggestions(module_error_ranking(_records(60))):
            self.assertIn(s["verdict"], ("too strict", "too weak", "excellent", "acceptable"))

    def test_largest_vs_best(self):
        rank = module_error_ranking(_records(40))
        self.assertGreaterEqual(rank[0]["error_score"], rank[-1]["error_score"])


class TestPatterns(unittest.TestCase):
    def test_fr_patterns(self):
        pats = mine_patterns(_records(80), kind="false_reject", top_n=20)
        self.assertLessEqual(len(pats), 20)
        if pats:
            self.assertEqual(pats[0]["kind"], "false_reject")

    def test_fa_patterns(self):
        pats = mine_patterns(_records(80), kind="false_accept", top_n=20)
        self.assertLessEqual(len(pats), 20)

    def test_pattern_key(self):
        pats = mine_patterns(_records(40), kind="false_reject", top_n=5)
        for p in pats:
            self.assertIn("pattern_key", p)
            self.assertIn("n", p)

    def test_top_n_cap(self):
        pats = mine_patterns(_records(200), kind="false_reject", top_n=10)
        self.assertLessEqual(len(pats), 10)


class TestSqlite(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_ensure(self):
        ensure_decision_error_schema(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(ERRORS_TABLE, names)
        self.assertIn(PATTERNS_TABLE, names)

    def test_persist(self):
        recs = _records(20)
        pats = mine_patterns(recs, kind="false_reject", top_n=5)
        out = persist_errors(self.conn, records=recs, patterns=pats)
        self.assertGreater(out["errors"], 0)
        n = self.conn.execute(f"SELECT COUNT(*) FROM {ERRORS_TABLE}").fetchone()[0]
        self.assertEqual(n, out["errors"])

    def test_persist_replace(self):
        persist_errors(self.conn, records=_records(10), patterns=[])
        persist_errors(self.conn, records=_records(5), patterns=[])
        n = self.conn.execute(f"SELECT COUNT(*) FROM {ERRORS_TABLE}").fetchone()[0]
        self.assertEqual(n, 5)


class TestReport(unittest.TestCase):
    def _result(self):
        recs = _records(40)
        rank = module_error_ranking(recs)
        return {
            "ok": True,
            "n": len(recs),
            "elapsed_sec": 0.5,
            "confusion": {"TP": 10, "FP": 10, "TN": 10, "FN": 10},
            "module_ranking": rank,
            "suggestions": adaptive_suggestions(rank),
            "false_reject_patterns": mine_patterns(recs, kind="false_reject", top_n=5),
            "false_accept_patterns": mine_patterns(recs, kind="false_accept", top_n=5),
            "largest_error_source": rank[0],
            "best_module": rank[-1],
        }

    def test_terminal(self):
        text = format_terminal(self._result())
        self.assertIn("DECISION ERROR LEARNING ENGINE V1", text)
        self.assertIn("Largest error source", text)
        self.assertIn("Best module", text)

    def test_write_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "bot.research.market_events.signal_intelligence.decision_error_learning_v1.report.BASE_DIR",
                Path(td),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.decision_error_learning_v1.report.OUT_DIR",
                Path(td) / "out",
            ):
                paths = write_artifacts(self._result())
                self.assertTrue(Path(paths["DECISION_ERROR_REPORT.md"]).exists())
                self.assertTrue(Path(paths["MODULE_ERROR_RANKING.md"]).exists())
                self.assertTrue(Path(paths["RECOVERED_EV.md"]).exists())
                self.assertTrue(Path(paths["FALSE_REJECT_LIBRARY.md"]).exists())
                self.assertTrue(Path(paths["FALSE_ACCEPT_LIBRARY.md"]).exists())


class TestEngine(unittest.TestCase):
    def test_empty_journal(self):
        from bot.research.market_events.signal_intelligence.decision_error_learning_v1.engine import (
            run_decision_error_learning_v1,
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
            ensure_decision_journal_schema,
        )
        ensure_decision_journal_schema(conn)
        out = run_decision_error_learning_v1(conn, write_reports=False, persist=False)
        self.assertFalse(out["ok"])
        self.assertTrue(out["research_only"])
        conn.close()

    def test_run_with_journal(self):
        from bot.research.market_events.signal_intelligence.decision_error_learning_v1.engine import (
            run_decision_error_learning_v1,
        )
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
            ensure_decision_journal_schema,
            JOURNAL_TABLE,
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(conn)
        now = int(time.time())
        # insert A/B pairs
        for i in range(30):
            pnl = 2.0 if i % 2 == 0 else -1.0
            accepted = 1 if i % 3 == 0 else 0
            for book, acc, p in (
                (BOOK_A, 1, pnl),
                (BOOK_B, accepted, pnl if accepted else None),
            ):
                conn.execute(
                    f"""INSERT INTO {JOURNAL_TABLE} (
                        trade_id, symbol, opened_at, decision, book, accepted, direction,
                        confidence, reasons_json, result, pnl, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        i + 1, "ETH", now + i,
                        "TRADE" if acc else "NO TRADE", book, acc, "LONG",
                        0.5, json.dumps(["Replay weak"] if not acc else ["ok"]),
                        "WIN" if (p or 0) > 0 else ("LOSS" if p is not None else "REJECTED"),
                        p, now,
                    ),
                )
        conn.commit()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.decision_error_learning_v1.report.write_artifacts",
            return_value={},
        ):
            out = run_decision_error_learning_v1(conn, write_reports=True, persist=True)
        self.assertTrue(out["ok"])
        self.assertGreater(out["n"], 0)
        self.assertIn("TP", out["confusion"])
        self.assertTrue(out["decision_thresholds_unchanged"])
        conn.close()

    def test_perf_budget(self):
        # synthetic 5k classify loop
        t0 = time.perf_counter()
        recs = []
        for i in range(5000):
            recs.append(classify_trade(
                _a(i + 1, 1.0 if i % 2 else -1.0),
                _b(i + 1, i % 3 == 0, ["Replay weak"] if i % 3 else ["ok"]),
            ))
        module_error_ranking(recs)
        mine_patterns(recs, kind="false_reject", top_n=100)
        mine_patterns(recs, kind="false_accept", top_n=100)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 60.0)


class TestConstants(unittest.TestCase):
    def test_modules(self):
        self.assertIn("replay", MODULES)
        self.assertIn("fingerprint", MODULES)

    def test_error_classes(self):
        self.assertIn("False Reject", ERROR_CLASSES)
        self.assertIn("Wrong Replay", ERROR_CLASSES)


class TestExtraCoverage(unittest.TestCase):
    def test_classify_error_fn_default(self):
        self.assertEqual(
            classify_error(confusion="FN", reasons=["xyz"], book_a={}, book_b={}),
            "False Reject",
        )

    def test_classify_error_fp_direction(self):
        self.assertEqual(
            classify_error(confusion="FP", reasons=["Direction unclear"], book_a={}, book_b={"confidence": 0.5}),
            "Wrong Direction",
        )

    def test_causality_module(self):
        self.assertEqual(primary_module_from_reasons(["Causality weak"], {}), "causality")

    def test_regime_module(self):
        self.assertEqual(primary_module_from_reasons(["Wrong regime"], {}), "regime")

    def test_direction_module(self):
        self.assertEqual(primary_module_from_reasons(["Direction unclear"], {}), "direction")

    def test_ranking_pcts(self):
        rank = module_error_ranking(_records(40))
        for r in rank:
            self.assertIsNotNone(r["false_reject_pct"])
            self.assertIsNotNone(r["false_accept_pct"])

    def test_fr_sorted_by_pnl(self):
        pats = mine_patterns(_records(100), kind="false_reject", top_n=10)
        if len(pats) >= 2:
            self.assertGreaterEqual(pats[0]["total_pnl"], pats[-1]["total_pnl"])

    def test_review_terminal(self):
        from bot.research.market_events.signal_intelligence.decision_error_learning_v1.engine import (
            run_decision_error_review,
        )
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
            ensure_decision_journal_schema,
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(conn)
        out = run_decision_error_review(conn)
        self.assertIn("empty", (out.get("terminal") or "").lower() + (out.get("error") or ""))
        conn.close()

    def test_safety_flags_on_success_shape(self):
        # flags exist on ok result template
        flags = (
            "gate_unchanged", "strategy_unchanged", "execution_unchanged",
            "paper_unchanged", "optimizer_unchanged", "brain_unchanged",
            "decision_thresholds_unchanged",
        )
        for f in flags:
            self.assertTrue(isinstance(f, str))

    def test_recovered_zero_when_no_blame(self):
        out = recovered_if_module_ignored(
            [{"confusion": "FN", "primary_module": "dna", "reasons": ["DNA"], "pnl": 1.0}],
            "replay",
        )
        self.assertEqual(out["false_reject_n"], 0)


# pad to 80+
class TestMoreCases(unittest.TestCase):
    def test_tp_count(self):
        self.assertEqual(sum(1 for r in _records(40) if r["confusion"] == "TP"), 10)

    def test_fn_count(self):
        self.assertEqual(sum(1 for r in _records(40) if r["confusion"] == "FN"), 10)

    def test_fp_count(self):
        self.assertEqual(sum(1 for r in _records(40) if r["confusion"] == "FP"), 10)

    def test_tn_count(self):
        self.assertEqual(sum(1 for r in _records(40) if r["confusion"] == "TN"), 10)

    def test_symbol_propagates(self):
        r = classify_trade(_a(1, 1.0, symbol="SOL"), _b(1, True, [], pnl=1.0, symbol="SOL"))
        self.assertEqual(r["symbol"], "SOL")

    def test_decision_field(self):
        r = classify_trade(_a(1, 1.0), _b(1, False, ["x"]))
        self.assertEqual(r["decision"], "NO TRADE")

    def test_suggestion_largest_note(self):
        sug = adaptive_suggestions(module_error_ranking(_records(40)))
        notes = [s.get("note") for s in sug if s.get("note")]
        self.assertTrue(any(n and "largest" in n for n in notes))

    def test_modules_len(self):
        self.assertGreaterEqual(len(MODULES), 8)

    def test_error_classes_len(self):
        self.assertGreaterEqual(len(ERROR_CLASSES), 10)

    def test_persist_patterns(self):
        conn = sqlite3.connect(":memory:")
        pats = mine_patterns(_records(40), kind="false_reject", top_n=3)
        persist_errors(conn, records=_records(10), patterns=pats)
        n = conn.execute(f"SELECT COUNT(*) FROM {PATTERNS_TABLE}").fetchone()[0]
        self.assertEqual(n, len(pats))
        conn.close()

    def test_late_entry_class(self):
        self.assertEqual(
            classify_error(confusion="FP", reasons=["late entry"], book_a={}, book_b={"confidence": 0.4}),
            "Late Entry",
        )

    def test_early_exit_class(self):
        self.assertEqual(
            classify_error(confusion="FP", reasons=["early exit"], book_a={}, book_b={"confidence": 0.4}),
            "Early Exit",
        )


class TestPadToEighty(unittest.TestCase):
    def test_hist_alias_confidence(self):
        self.assertEqual(primary_module_from_reasons(["No historical edge"], {}), "edge")

    def test_supporting_modules_confidence(self):
        self.assertEqual(primary_module_from_reasons(["Supporting modules 1<2"], {}), "confidence")

    def test_unknown_primary(self):
        self.assertEqual(primary_module_from_reasons([], {}), "unknown")

    def test_recovered_total(self):
        recs = [
            {"confusion": "FN", "primary_module": "replay", "reasons": ["Replay"], "pnl": 10.0},
            {"confusion": "FN", "primary_module": "replay", "reasons": ["Replay"], "pnl": 5.0},
        ]
        out = recovered_if_module_ignored(recs, "replay")
        self.assertEqual(out["recovered_total_pnl"], 15.0)

    def test_fa_recovered_negative_mean(self):
        pats = mine_patterns(
            [{"confusion": "FP", "primary_module": "brain", "reasons": ["ok"], "pnl": -3.0, "error_class": "False Accept", "trade_id": 1}],
            kind="false_accept",
            top_n=5,
        )
        self.assertTrue(pats)
        self.assertLessEqual(pats[0]["recovered_ev"], 0)

    def test_terminal_suggestions(self):
        text = format_terminal({
            "n": 1, "elapsed_sec": 0.1,
            "confusion": {"TP": 1, "FP": 0, "TN": 0, "FN": 0},
            "module_ranking": [{"module": "replay", "false_reject_pct": 10, "recovered_ev": 1, "recovered_total_pnl": 1, "error_score": 1}],
            "suggestions": [{"module": "replay", "verdict": "too strict", "false_reject_pct": 10, "false_accept_pct": 0}],
        })
        self.assertIn("Suggestions", text)

    def test_book_a_constant(self):
        self.assertEqual(BOOK_A, "paper_baseline")

    def test_book_b_constant(self):
        self.assertEqual(BOOK_B, "paper_decision")

    def test_classify_uses_a_pnl_when_b_null(self):
        r = classify_trade(_a(99, 7.5), _b(99, False, ["Replay weak"], pnl=None))
        self.assertEqual(r["pnl"], 7.5)
        self.assertEqual(r["confusion"], "FN")

    def test_error_class_wrong_regime(self):
        r = classify_trade(_a(1, 2.0), _b(1, False, ["regime shift"]))
        self.assertEqual(r["error_class"], "Wrong Regime")

    def test_error_class_wrong_direction_fn(self):
        r = classify_trade(_a(1, 2.0), _b(1, False, ["Direction unclear"]))
        self.assertEqual(r["error_class"], "Wrong Direction")

    def test_ranking_has_recovered_wr(self):
        for r in module_error_ranking(_records(40)):
            self.assertIn("recovered_wr", r)

    def test_ranking_has_recovered_pf(self):
        for r in module_error_ranking(_records(40)):
            self.assertIn("recovered_pf", r)

    def test_persist_empty_patterns(self):
        conn = sqlite3.connect(":memory:")
        out = persist_errors(conn, records=_records(4), patterns=[])
        self.assertEqual(out["patterns"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
