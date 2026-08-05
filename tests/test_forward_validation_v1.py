"""Tests for Forward Validation Monitor V1 (100+)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
    BOOK_D,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.engine import (
    _close_fields,
    _is_closed,
    incremental_sync,
    load_tracked,
    run_forward_monitor_v1,
    run_forward_report,
    run_forward_weekly,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.metrics import (
    closed_metrics,
    detect_alerts,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.schema import (
    STATE_TABLE,
    TABLE,
    ensure_forward_validation_schema,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A as JA,
    BOOK_B as JB,
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


def _seed_journal(conn, n=30):
    now = int(time.time())
    for book in (JA, JB):
        for i in range(n):
            pnl = 2.0 if i % 2 == 0 else -1.0
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
                    i + 1, "BTC", 1_720_000_000 + i * 3600, "TRADE", book, 1, "SHORT",
                    0.8, 0.65, 0.4, 0.55, 1, 0.5, 0.6, 0.7, 0.4, "A", "[]",
                    75, 2.0, 0.5, "WIN" if pnl > 0 else "LOSS", pnl, now,
                ),
            )
    conn.commit()


class TestCloseLogic(unittest.TestCase):
    def test_is_closed_pnl(self):
        self.assertTrue(_is_closed({"pnl": 1.0}))

    def test_is_closed_result(self):
        self.assertTrue(_is_closed({"result": "WIN"}))

    def test_not_closed(self):
        self.assertFalse(_is_closed({"result": None, "pnl": None}))

    def test_rejected_not_closed(self):
        self.assertFalse(_is_closed({"result": "REJECTED"}))

    def test_close_fields_win(self):
        f = _close_fields({"pnl": 2.0, "historical_wr": 80, "opened_at": 100}, 100)
        self.assertEqual(f["status"], "closed")
        self.assertEqual(f["actual_result"], "WIN")
        self.assertEqual(f["prediction_correct"], 1)

    def test_close_fields_miss(self):
        f = _close_fields({"pnl": -1.0, "historical_wr": 80, "result": "LOSS"}, 100)
        self.assertEqual(f["prediction_correct"], 0)

    def test_mae_mfe(self):
        f = _close_fields({"pnl": -2.0, "result": "LOSS"}, 1)
        self.assertGreaterEqual(f["mae"], 2.0)
        self.assertEqual(f["mfe"], 0.0)


class TestMetrics(unittest.TestCase):
    def test_empty(self):
        m = closed_metrics([])
        self.assertEqual(m["n_closed"], 0)

    def test_wr(self):
        rows = [
            {"status": "closed", "pnl": 2, "prediction_correct": 1, "historical_wr": 70},
            {"status": "closed", "pnl": -1, "prediction_correct": 0, "historical_wr": 70},
            {"status": "open"},
        ]
        m = closed_metrics(rows)
        self.assertEqual(m["n_closed"], 2)
        self.assertEqual(m["n_open"], 1)
        self.assertAlmostEqual(m["wr"], 50.0)

    def test_alerts_reality_drop(self):
        alerts = detect_alerts(
            current={"reality_score": 70, "wr": 50, "pf": 1.5, "prediction_accuracy": 60},
            previous={"reality_score": 90, "wr": 55, "pf": 1.6, "prediction_accuracy": 62},
            book_metrics={"book_d": {"n_closed": 0}, "book_b": {"n_closed": 0}},
        )
        types = {a["type"] for a in alerts}
        self.assertIn("reality_drop", types)

    def test_book_d_under(self):
        alerts = detect_alerts(
            current={"wr": 50},
            previous={"wr": 50},
            book_metrics={
                "book_d": {"n_closed": 5, "ev": 0.1},
                "book_b": {"n_closed": 5, "ev": 1.0},
            },
        )
        self.assertTrue(any(a["type"] == "book_d_underperforms" for a in alerts))

    def test_book_d_improves(self):
        alerts = detect_alerts(
            current={"wr": 50},
            previous={"wr": 50},
            book_metrics={
                "book_d": {"n_closed": 5, "ev": 2.0},
                "book_b": {"n_closed": 5, "ev": 1.0},
            },
        )
        self.assertTrue(any(a["type"] == "book_d_improves" for a in alerts))


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_reality_validation_schema(self.conn)
        ensure_paper_math_schema(self.conn)
        ensure_forward_validation_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO reality_validation_v1(section, key, value_real, value_text, meta_json, updated_at)
            VALUES ('summary','reality_score',88.0,NULL,'{}',?)
            """,
            (int(time.time()),),
        )
        _seed_journal(self.conn, 25)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(TABLE, names)
        self.assertIn(STATE_TABLE, names)

    def test_incremental_new(self):
        out = incremental_sync(self.conn)
        self.assertGreater(out["new_tracked"], 0)
        rows = load_tracked(self.conn)
        self.assertGreater(len(rows), 0)

    def test_incremental_idempotent(self):
        a = incremental_sync(self.conn)
        b = incremental_sync(self.conn)
        self.assertEqual(b["new_tracked"], 0)
        self.assertGreaterEqual(a["new_tracked"], b["new_tracked"])

    def test_run(self):
        out = run_forward_monitor_v1(self.conn, write_reports=False)
        self.assertTrue(out["ok"])
        self.assertTrue(out["observe_only"])
        self.assertIn("new_tracked", out)
        self.assertIn("closed_tracked", out)

    def test_report(self):
        out = run_forward_report(self.conn, write_reports=False)
        self.assertTrue(out["ok"])

    def test_weekly(self):
        out = run_forward_weekly(self.conn, write_reports=False)
        self.assertTrue(out["ok"])
        self.assertIn("by_book", out)

    def test_flags(self):
        out = run_forward_monitor_v1(self.conn, write_reports=False)
        self.assertTrue(out["execution_unchanged"])
        self.assertTrue(out["no_new_indicators"])

    def test_books_present(self):
        incremental_sync(self.conn)
        books = {r["book"] for r in load_tracked(self.conn)}
        self.assertIn(BOOK_A, books)
        self.assertIn(BOOK_B, books)


# parametric
class TestCloseGrid(unittest.TestCase):
    pass


for i, pnl in enumerate([2, 1, 0, -1, -2, 5, -5, 0.5, -0.5, 3.3]):
    def _mk(p):
        def _t(self):
            f = _close_fields({"pnl": p, "historical_wr": 60}, 1000)
            self.assertEqual(f["status"], "closed")
            self.assertIn(f["actual_result"], ("WIN", "LOSS", "FLAT"))
        return _t
    setattr(TestCloseGrid, f"test_pnl_{i}", _mk(pnl))


class TestPredGrid(unittest.TestCase):
    pass


for i, (wr, pnl, expect) in enumerate([
    (80, 1, 1), (80, -1, 0), (40, -1, 1), (40, 1, 0),
    (50, 1, 1), (50, -1, 0), (90, 2, 1), (10, -2, 1),
]):
    def _mk(w, p, e):
        def _t(self):
            f = _close_fields({"pnl": p, "historical_wr": w}, 1)
            self.assertEqual(f["prediction_correct"], e)
        return _t
    setattr(TestPredGrid, f"test_pred_{i}", _mk(wr, pnl, expect))


class TestAlertWR(unittest.TestCase):
    pass


for i, (prev, cur, expect) in enumerate([
    (60, 50, True), (60, 56, False), (70, 60, True), (55, 55, False),
    (80, 70, True), (40, 39, False),
]):
    def _mk(p, c, e):
        def _t(self):
            alerts = detect_alerts(
                current={"wr": c, "reality_score": 90, "pf": 2, "prediction_accuracy": 70},
                previous={"wr": p, "reality_score": 90, "pf": 2, "prediction_accuracy": 70},
                book_metrics={"book_d": {"n_closed": 0}, "book_b": {"n_closed": 0}},
            )
            has = any(a["type"] == "wr_drop" for a in alerts)
            self.assertEqual(has, e)
        return _t
    setattr(TestAlertWR, f"test_wr_alert_{i}", _mk(prev, cur, expect))


class TestMetricsN(unittest.TestCase):
    pass


for i, n in enumerate([0, 1, 2, 5, 10, 20, 30, 40]):
    def _mk(nn):
        def _t(self):
            rows = [
                {"status": "closed", "pnl": 1.0 if j % 2 == 0 else -0.5,
                 "holding_time_sec": 300, "mae": 0.5, "prediction_correct": 1,
                 "historical_wr": 70, "historical_ev": 0.4}
                for j in range(nn)
            ]
            m = closed_metrics(rows)
            self.assertEqual(m["n_closed"], nn)
        return _t
    setattr(TestMetricsN, f"test_n_{i}", _mk(n))


class TestBookConstants(unittest.TestCase):
    pass


for i, book in enumerate([BOOK_A, BOOK_B, BOOK_C, BOOK_D]):
    def _mk(b):
        def _t(self):
            self.assertTrue(isinstance(b, str))
            self.assertTrue(len(b) > 0)
        return _t
    setattr(TestBookConstants, f"test_book_{i}", _mk(book))


class TestIncrementalSizes(unittest.TestCase):
    pass


for i, n in enumerate([5, 10, 15, 20, 25, 30]):
    def _mk(nn):
        def _t(self):
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            ensure_decision_journal_schema(conn)
            ensure_elite_candidate_schema(conn)
            ensure_reality_validation_schema(conn)
            ensure_paper_math_schema(conn)
            ensure_forward_validation_schema(conn)
            _seed_journal(conn, nn)
            out = incremental_sync(conn)
            self.assertGreater(out["new_tracked"], 0)
            out2 = incremental_sync(conn)
            self.assertEqual(out2["new_tracked"], 0)
            conn.close()
            Path(tmp.name).unlink(missing_ok=True)
        return _t
    setattr(TestIncrementalSizes, f"test_size_{i}", _mk(n))


class TestHolding(unittest.TestCase):
    pass


for i, (o, c) in enumerate([(100, 400), (0, 0), (1000, 1000), (100, 100)]):
    def _mk(oo, cc):
        def _t(self):
            f = _close_fields({"pnl": 1, "opened_at": oo, "closed_at": cc, "historical_wr": 60}, oo)
            self.assertIsNotNone(f["holding_time_sec"])
        return _t
    setattr(TestHolding, f"test_hold_{i}", _mk(o, c))


class TestPFAlert(unittest.TestCase):
    pass


for i, (pp, cp, exp) in enumerate([
    (2.0, 1.5, True), (2.0, 1.9, False), (3.0, 2.0, True), (1.5, 1.5, False),
]):
    def _mk(p, c, e):
        def _t(self):
            alerts = detect_alerts(
                current={"pf": c, "wr": 50, "reality_score": 90, "prediction_accuracy": 50},
                previous={"pf": p, "wr": 50, "reality_score": 90, "prediction_accuracy": 50},
                book_metrics={"book_d": {"n_closed": 0}, "book_b": {"n_closed": 0}},
            )
            has = any(a["type"] == "pf_drop" for a in alerts)
            self.assertEqual(has, e)
        return _t
    setattr(TestPFAlert, f"test_pf_{i}", _mk(pp, cp, exp))


class TestPredDrift(unittest.TestCase):
    pass


for i, (p, c, e) in enumerate([
    (80, 60, True), (80, 75, False), (70, 50, True), (55, 50, False),
]):
    def _mk(pp, cc, ee):
        def _t(self):
            alerts = detect_alerts(
                current={"prediction_accuracy": cc, "wr": 50, "reality_score": 90, "pf": 2},
                previous={"prediction_accuracy": pp, "wr": 50, "reality_score": 90, "pf": 2},
                book_metrics={"book_d": {"n_closed": 0}, "book_b": {"n_closed": 0}},
            )
            has = any(a["type"] == "prediction_drift" for a in alerts)
            self.assertEqual(has, ee)
        return _t
    setattr(TestPredDrift, f"test_drift_{i}", _mk(p, c, e))


class TestIsClosedGrid(unittest.TestCase):
    pass


for i, (sig, exp) in enumerate([
    ({"pnl": 1}, True),
    ({"result": "WIN"}, True),
    ({"result": "LOSS"}, True),
    ({"result": "FLAT"}, True),
    ({"result": "REJECTED"}, False),
    ({}, False),
    ({"result": "CLOSED"}, True),
    ({"pnl": 0}, True),
]):
    def _mk(s, e):
        def _t(self):
            self.assertEqual(_is_closed(s), e)
        return _t
    setattr(TestIsClosedGrid, f"test_closed_{i}", _mk(sig, exp))


class TestRealityAlert(unittest.TestCase):
    pass


for i, (p, c, e) in enumerate([
    (90, 80, True), (90, 86, False), (85, 70, True), (80, 80, False),
    (95, 85, True), (70, 66, False),
]):
    def _mk(pp, cc, ee):
        def _t(self):
            alerts = detect_alerts(
                current={"reality_score": cc, "wr": 50, "pf": 2, "prediction_accuracy": 50},
                previous={"reality_score": pp, "wr": 50, "pf": 2, "prediction_accuracy": 50},
                book_metrics={"book_d": {"n_closed": 0}, "book_b": {"n_closed": 0}},
            )
            has = any(a["type"] == "reality_drop" for a in alerts)
            self.assertEqual(has, ee)
        return _t
    setattr(TestRealityAlert, f"test_real_{i}", _mk(p, c, e))


class TestExpWrNorm(unittest.TestCase):
    pass


for i, wr in enumerate([0.7, 0.8, 70, 75, 90, 55, 0.55, 100]):
    def _mk(w):
        def _t(self):
            f = _close_fields({"pnl": 1.0, "historical_wr": w}, 100)
            self.assertEqual(f["prediction_correct"], 1)
        return _t
    setattr(TestExpWrNorm, f"test_ewr_{i}", _mk(wr))


class TestPnLPct(unittest.TestCase):
    pass


for i, (pnl, entry) in enumerate([
    (2.0, 100.0), (-1.0, 50.0), (5.0, None), (0.0, 10.0), (3.0, 200.0),
]):
    def _mk(p, e):
        def _t(self):
            f = _close_fields({"pnl": p, "entry_price": e, "historical_wr": 60}, 1)
            self.assertIsNotNone(f["pnl_pct"])
        return _t
    setattr(TestPnLPct, f"test_pct_{i}", _mk(pnl, entry))


if __name__ == "__main__":
    unittest.main()
