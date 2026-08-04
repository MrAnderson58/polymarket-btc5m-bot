"""test_decision_journal — schema + batched inserts."""

from __future__ import annotations

import json
import sqlite3
import time
import unittest

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    build_journal_rows,
    clear_journal,
    insert_journal_batch,
    journal_row_from_decision,
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    JOURNAL_TABLE,
    ensure_decision_journal_schema,
)


def _decision(trade=True, tid=1, conf=0.8):
    return {
        "trade_id": tid,
        "symbol": "ETH",
        "decision": "TRADE" if trade else "NO TRADE",
        "direction": "LONG" if trade else None,
        "confidence": conf,
        "why": ["Fingerprint 80%"] if trade else ["No historical edge"],
        "historical_wr": 55.0,
        "historical_pf": 1.2,
        "historical_ev": 0.8,
        "scores": {
            "fingerprint": {"pass": True, "similarity_pct": 80, "ok": True},
            "timeline": {"pass": True, "similarity_pct": 75, "ok": True},
            "dna": {"pass": True, "similarity_pct": 70, "ok": True},
            "rules": {"pass": True, "blocked": False, "rule": "R1", "ok": True},
            "edge": {"pass": True, "similarity_pct": 65, "ok": True},
            "replay": {"pass": False, "similarity_pct": 40, "ok": True},
            "brain": {"pass": True, "confidence": conf, "ok": True},
            "causality": {"pass": False, "ok": True},
        },
    }


def _trade(tid=1, pnl=1.5):
    return {
        "trade_id": tid,
        "symbol": "ETH",
        "direction": "LONG",
        "opened_at": 1_700_000_000 + tid,
        "pnl": pnl,
        "result": "WIN" if pnl > 0 else "LOSS",
    }


class TestJournalSchema(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_ensure_creates_table(self):
        ensure_decision_journal_schema(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (JOURNAL_TABLE,),
        ).fetchone()
        self.assertIsNotNone(row)

    def test_table_has_required_columns(self):
        ensure_decision_journal_schema(self.conn)
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({JOURNAL_TABLE})")}
        for c in (
            "trade_id", "symbol", "opened_at", "decision", "book", "direction",
            "confidence", "timeline_similarity", "fingerprint_similarity",
            "dna", "rules", "edge", "replay", "brain", "causality",
            "decision_rank", "reasons_json", "historical_wr", "historical_pf",
            "historical_ev", "result", "pnl",
        ):
            self.assertIn(c, cols)

    def test_unique_trade_book(self):
        ensure_decision_journal_schema(self.conn)
        idx = self.conn.execute(
            f"SELECT sql FROM sqlite_master WHERE tbl_name='{JOURNAL_TABLE}'"
        ).fetchall()
        text = " ".join(str(r[0] or "") for r in idx).replace(" ", "").lower()
        self.assertIn("unique(trade_id,book)", text)


class TestJournalRows(unittest.TestCase):
    def test_build_three_rows(self):
        rows = build_journal_rows(_decision(), _trade())
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["book"] for r in rows}, {BOOK_A, BOOK_B, BOOK_C})

    def test_book_a_always_accepted(self):
        rows = build_journal_rows(_decision(trade=False), _trade())
        a = next(r for r in rows if r["book"] == BOOK_A)
        self.assertEqual(a["accepted"], 1)
        self.assertEqual(a["result"], "WIN")

    def test_book_b_rejected_on_no_trade(self):
        rows = build_journal_rows(_decision(trade=False), _trade())
        b = next(r for r in rows if r["book"] == BOOK_B)
        self.assertEqual(b["accepted"], 0)
        self.assertEqual(b["result"], "REJECTED")
        self.assertIsNone(b["pnl"])

    def test_book_c_accepted_high_conf(self):
        rows = build_journal_rows(_decision(trade=True, conf=0.85), _trade())
        c = next(r for r in rows if r["book"] == BOOK_C)
        self.assertEqual(c["accepted"], 1)

    def test_reasons_json(self):
        row = journal_row_from_decision(_decision(), _trade(), BOOK_A, accepted=True)
        reasons = json.loads(row["reasons_json"])
        self.assertTrue(reasons)

    def test_module_fields_present(self):
        row = journal_row_from_decision(_decision(), _trade(), BOOK_B, accepted=True)
        self.assertIsNotNone(row["timeline_similarity"])
        self.assertIsNotNone(row["fingerprint_similarity"])
        self.assertEqual(row["rules"], 1)

    def test_loss_result(self):
        row = journal_row_from_decision(_decision(), _trade(pnl=-2.0), BOOK_A, accepted=True)
        self.assertEqual(row["result"], "LOSS")


class TestJournalBatch(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_insert_batch(self):
        rows = []
        for i in range(5):
            rows.extend(build_journal_rows(_decision(tid=i + 1), _trade(tid=i + 1, pnl=1.0)))
        n = insert_journal_batch(self.conn, rows)
        self.assertEqual(n, 15)
        loaded = load_journal_rows(self.conn)
        self.assertEqual(len(loaded), 15)

    def test_upsert_idempotent(self):
        rows = build_journal_rows(_decision(tid=9), _trade(tid=9))
        insert_journal_batch(self.conn, rows)
        insert_journal_batch(self.conn, rows)
        self.assertEqual(len(load_journal_rows(self.conn)), 3)

    def test_filter_by_book(self):
        insert_journal_batch(self.conn, build_journal_rows(_decision(), _trade()))
        b = load_journal_rows(self.conn, book=BOOK_B)
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["book"], BOOK_B)

    def test_accepted_only(self):
        insert_journal_batch(
            self.conn,
            build_journal_rows(_decision(trade=False), _trade()),
        )
        acc = load_journal_rows(self.conn, accepted_only=True)
        self.assertTrue(all(int(r["accepted"]) == 1 for r in acc))
        self.assertEqual(len(acc), 1)  # only A

    def test_clear_journal(self):
        insert_journal_batch(self.conn, build_journal_rows(_decision(), _trade()))
        clear_journal(self.conn)
        self.assertEqual(load_journal_rows(self.conn), [])

    def test_empty_batch(self):
        self.assertEqual(insert_journal_batch(self.conn, []), 0)

    def test_batch_no_n_plus_one(self):
        """Single executemany — timing sanity for 300 rows."""
        rows = []
        for i in range(100):
            rows.extend(build_journal_rows(_decision(tid=i + 1), _trade(tid=i + 1)))
        t0 = time.perf_counter()
        insert_journal_batch(self.conn, rows)
        ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(ms, 500.0)
        self.assertEqual(len(load_journal_rows(self.conn)), 300)


if __name__ == "__main__":
    unittest.main()
