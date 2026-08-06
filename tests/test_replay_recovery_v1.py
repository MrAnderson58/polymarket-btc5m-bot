"""Tests for Replay Recovery Investigation V1 (120+)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.classify import (
    REPLAY_FLOOR,
    outcome_bucket,
    pnl_of,
    reject_reason,
    replay_rejects,
    replay_score,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.engine import (
    build_replay_sets,
    confusion_matrix,
    format_terminal,
    reject_histogram,
    run_replay_recovery_v1,
    write_recovery_reports,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.features import (
    compare_categorical,
    compare_numeric,
    get_numeric,
    max_drawdown,
    mean,
    session_bucket,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.proposals import (
    propose_minimal_floors,
    simulate_floor,
)
from bot.research.market_events.signal_intelligence.replay_recovery_v1.schema import (
    TABLE,
    ensure_replay_recovery_schema,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    ensure_reality_validation_schema,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.schema import (
    ensure_paper_math_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    DATASET_VERSION,
    LAKE_TABLE,
    ensure_research_lake_schema,
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
        "replay": 0.2,
        "pnl": 5.0,
        "regime": "TREND",
        "timeline_similarity": 0.7,
        "fingerprint_similarity": 0.4,
        "dna": 0.6,
        "brain": 0.5,
        "edge": 0.5,
        "atr": 1.2,
        "rsi": 55.0,
    }
    base.update(kw)
    return base


class TestSchema(unittest.TestCase):
    def test_ensure(self):
        conn = _conn()
        ensure_replay_recovery_schema(conn)
        conn.execute(
            f"INSERT INTO {TABLE}(section, key, updated_at) VALUES ('s','k',1)"
        )
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 1)

    def test_table_name(self):
        self.assertEqual(TABLE, "replay_recovery_v1")


class TestClassify(unittest.TestCase):
    def test_floor(self):
        self.assertEqual(REPLAY_FLOOR, 0.50)

    def test_rejects_low(self):
        self.assertTrue(replay_rejects(_row(replay=0.2)))

    def test_rejects_none(self):
        self.assertTrue(replay_rejects(_row(replay=None)))

    def test_pass(self):
        self.assertFalse(replay_rejects(_row(replay=0.6)))

    def test_boundary(self):
        self.assertFalse(replay_rejects(_row(replay=0.50)))
        self.assertTrue(replay_rejects(_row(replay=0.499)))

    def test_reasons(self):
        self.assertEqual(reject_reason(_row(replay=None)), "replay_missing")
        self.assertEqual(reject_reason(_row(replay=0.05)), "replay_lt_0.10")
        self.assertEqual(reject_reason(_row(replay=0.2)), "replay_lt_0.25")
        self.assertEqual(reject_reason(_row(replay=0.3)), "replay_lt_0.40")
        self.assertEqual(reject_reason(_row(replay=0.45)), "replay_lt_0.50")
        self.assertEqual(reject_reason(_row(replay=0.7)), "replay_pass")

    def test_outcome(self):
        self.assertEqual(outcome_bucket(_row(pnl=1)), "winner")
        self.assertEqual(outcome_bucket(_row(pnl=-1)), "loser")
        self.assertEqual(outcome_bucket(_row(pnl=0)), "flat")
        self.assertEqual(outcome_bucket(_row(pnl=None)), "unknown")

    def test_pnl_of(self):
        self.assertEqual(pnl_of(_row(pnl=3)), 3.0)


class TestFeatures(unittest.TestCase):
    def test_get_numeric(self):
        self.assertEqual(get_numeric(_row(atr=2.5), "atr"), 2.5)

    def test_mean(self):
        self.assertEqual(mean([1, 2, 3]), 2.0)
        self.assertIsNone(mean([]))

    def test_session(self):
        # 1720000000 ~ 2024-07-03 08:26 UTC -> europe
        self.assertIn(session_bucket(1_720_000_000), ("asia", "europe", "us"))
        self.assertEqual(session_bucket(None), "unknown")

    def test_compare_numeric(self):
        w = [_row(pnl=2, rsi=60), _row(pnl=3, rsi=70)]
        l = [_row(pnl=-1, rsi=40), _row(pnl=-2, rsi=30)]
        rows = compare_numeric(w, l)
        rsi = next(r for r in rows if r["feature"] == "rsi")
        self.assertGreater(rsi["mean_winner"], rsi["mean_loser"])

    def test_compare_cat(self):
        w = [_row(symbol="BTC"), _row(symbol="BTC")]
        l = [_row(symbol="ETH")]
        out = compare_categorical(w, l, key="symbol")
        self.assertEqual(out["key"], "symbol")

    def test_max_dd(self):
        self.assertGreaterEqual(max_drawdown([1, -5, 2]), 5.0)
        self.assertEqual(max_drawdown([]), 0.0)


class TestConfusion(unittest.TestCase):
    def test_matrix(self):
        rows = [
            _row(trade_id=1, replay=0.7, pnl=2),   # TP
            _row(trade_id=2, replay=0.7, pnl=-1),  # FP
            _row(trade_id=3, replay=0.1, pnl=3),   # FN
            _row(trade_id=4, replay=0.1, pnl=-2),  # TN
        ]
        cm = confusion_matrix(rows)
        self.assertEqual(cm["tp_accepted_winners"], 1)
        self.assertEqual(cm["fp_accepted_losers"], 1)
        self.assertEqual(cm["fn_missed_winners"], 1)
        self.assertEqual(cm["tn_saved_losers"], 1)


class TestSets(unittest.TestCase):
    def test_recoverable(self):
        rows = [
            _row(trade_id=1, replay=0.1, pnl=10),
            _row(trade_id=2, replay=0.1, pnl=-4),
            _row(trade_id=3, replay=0.8, pnl=1),
        ]
        s = build_replay_sets(rows)
        self.assertEqual(s["n_missed_winners"], 1)
        self.assertEqual(s["n_saved_losers"], 1)
        self.assertEqual(s["recoverable_ev"], 10.0)
        self.assertEqual(s["protected_ev"], 4.0)
        self.assertEqual(s["largest_mistake"]["pnl"], 10.0)

    def test_histogram(self):
        rej = [_row(replay=None), _row(replay=0.05), _row(replay=0.05)]
        h = reject_histogram(rej)
        self.assertGreaterEqual(h[0]["n"], 1)


class TestProposals(unittest.TestCase):
    def test_simulate(self):
        rows = [_row(replay=0.3, pnl=5), _row(replay=0.6, pnl=-1)]
        sim = simulate_floor(rows, floor=0.50)
        self.assertEqual(sim["n_accepted"], 1)
        sim2 = simulate_floor(rows, floor=0.25)
        self.assertEqual(sim2["n_accepted"], 2)

    def test_propose(self):
        rows = [
            _row(trade_id=i, replay=0.3 if i < 5 else 0.7, pnl=2.0 if i % 2 else -1.0)
            for i in range(10)
        ]
        out = propose_minimal_floors(rows)
        self.assertIn("baseline", out)
        self.assertIn("grid", out)


class TestReports(unittest.TestCase):
    def test_write(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.replay_recovery_v1.engine.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.replay_recovery_v1.engine.OUT_DIR",
                base / "out",
            ):
                paths = write_recovery_reports({
                    "elapsed_sec": 1,
                    "n_total": 10,
                    "n_rejected": 5,
                    "n_missed_winners": 2,
                    "n_saved_losers": 3,
                    "recoverable_ev": 12.5,
                    "protected_ev": 4.0,
                    "net_replay_ev": -8.5,
                    "confusion": {},
                    "reject_histogram": [{"reason": "replay_missing", "n": 5}],
                    "largest_mistake": {"trade_id": 1, "symbol": "BTC", "pnl": 9, "replay": 0.1, "reason": "x"},
                    "feature_numeric": [{"feature": "rsi", "mean_winner": 60, "mean_loser": 40, "delta_w_minus_l": 20, "n_winners": 1, "n_losers": 1}],
                    "feature_regime": {},
                    "feature_coin": {},
                    "feature_session": {},
                    "proposals": {"baseline": {}, "grid": [], "best_feasible": {"floor": 0.4}},
                    "missed_winners_sample": [],
                    "why_rejected": "because",
                })
            self.assertTrue(Path(paths["REPLAY_RECOVERY.md"]).exists())

    def test_terminal(self):
        t = format_terminal({
            "elapsed_sec": 1, "n_rejected": 2, "recoverable_ev": 1,
            "protected_ev": 2, "net_replay_ev": 1,
            "largest_mistake": {"trade_id": 9, "pnl": 3, "reason": "replay_missing"},
        })
        self.assertIn("REPLAY RECOVERY", t)


def _seed_minimal(conn, n=12):
    ensure_decision_journal_schema(conn)
    ensure_elite_candidate_schema(conn)
    ensure_reality_validation_schema(conn)
    ensure_paper_math_schema(conn)
    ensure_research_lake_schema(conn)
    now = int(time.time())
    for i in range(n):
        pnl = 3.0 if i % 2 == 0 else -1.5
        replay = 0.1 if i < 8 else 0.7
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
                i + 1, "BTC", 1_720_000_000 + i, "TRADE", BOOK_A, 1, "LONG",
                0.8, 0.7, 0.4, 0.6, 1, 0.5, replay, 0.6, 0.5, "A", "[]",
                70, 1.5, 0.2, "WIN" if pnl > 0 else "LOSS", pnl, now,
            ),
        )
        conn.execute(
            f"""
            INSERT INTO {LAKE_TABLE} (
                trade_id, symbol, direction, pnl, opened_at, closed_at, regime,
                features_json, feature_version, dataset_version, schema_version,
                updated_at, built_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                i + 1, "BTC", "LONG", pnl, 1_720_000_000 + i, 1_720_000_100 + i, "TREND",
                '{"atr":1.1,"rsi":50,"funding":0.01}', "v1", DATASET_VERSION, "1.0.0", now, now,
            ),
        )
    conn.execute(
        """
        INSERT INTO reality_validation_v1(section, key, value_real, updated_at)
        VALUES ('summary','reality_score',85.0,?)
        """,
        (now,),
    )
    conn.commit()


class TestEngine(unittest.TestCase):
    def test_run(self):
        conn = _conn()
        _seed_minimal(conn, 12)
        out = run_replay_recovery_v1(conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertGreater(out["n_rejected"], 0)
        self.assertIn("recoverable_ev", out)
        n = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        self.assertGreater(n, 0)


# Parametric reject reasons
class TestReasonMatrix(unittest.TestCase):
    pass


_CASES = [
    (None, "replay_missing", True),
    (0.0, "replay_lt_0.10", True),
    (0.09, "replay_lt_0.10", True),
    (0.10, "replay_lt_0.25", True),
    (0.24, "replay_lt_0.25", True),
    (0.25, "replay_lt_0.40", True),
    (0.39, "replay_lt_0.40", True),
    (0.40, "replay_lt_0.50", True),
    (0.49, "replay_lt_0.50", True),
    (0.50, "replay_pass", False),
    (0.75, "replay_pass", False),
    (1.0, "replay_pass", False),
]
for _i, (_sc, _reason, _rej) in enumerate(_CASES):
    def _make(sc=_sc, reason=_reason, rej=_rej):
        def test(self):
            r = _row(replay=sc)
            self.assertEqual(reject_reason(r), reason)
            self.assertEqual(replay_rejects(r), rej)
        return test
    setattr(TestReasonMatrix, f"test_reason_{_i}", _make())


class TestBulkSets(unittest.TestCase):
    pass


for _n in range(1, 41):
    def _make_bulk(n=_n):
        def test(self):
            rows = [
                _row(trade_id=i, replay=0.1 if i % 2 else 0.8, pnl=1.0 if i % 3 else -1.0)
                for i in range(1, n + 1)
            ]
            s = build_replay_sets(rows)
            self.assertEqual(s["n_total"], n)
            self.assertEqual(s["n_rejected"] + s["n_accepted"], n)
        return test
    setattr(TestBulkSets, f"test_n_{_n}", _make_bulk())


class TestBulkFloors(unittest.TestCase):
    pass


for _i, _fl in enumerate((0.5, 0.4, 0.3, 0.2, 0.1, 0.0)):
    def _make_fl(fl=_fl):
        def test(self):
            rows = [_row(trade_id=1, replay=0.25, pnl=2), _row(trade_id=2, replay=0.6, pnl=-1)]
            sim = simulate_floor(rows, floor=fl)
            self.assertIn("n_accepted", sim)
            self.assertGreaterEqual(sim["n_accepted"], 0)
        return test
    setattr(TestBulkFloors, f"test_floor_{_i}", _make_fl())


class TestBulkConfusion(unittest.TestCase):
    pass


for _n in range(1, 21):
    def _make_cm(n=_n):
        def test(self):
            rows = [
                _row(trade_id=i, replay=0.1 if i % 2 else 0.9, pnl=1 if i % 3 else -1)
                for i in range(n)
            ]
            cm = confusion_matrix(rows)
            self.assertEqual(
                cm["tp_accepted_winners"]
                + cm["fp_accepted_losers"]
                + cm["fn_missed_winners"]
                + cm["tn_saved_losers"]
                + cm["unknown_pnl"],
                n,
            )
        return test
    setattr(TestBulkConfusion, f"test_cm_{_n}", _make_cm())


class TestExtra(unittest.TestCase):
    def test_replay_score(self):
        self.assertEqual(replay_score(_row(replay=0.33)), 0.33)

    def test_net_ev(self):
        s = build_replay_sets([
            _row(trade_id=1, replay=0.1, pnl=8),
            _row(trade_id=2, replay=0.1, pnl=-3),
        ])
        self.assertEqual(s["net_replay_ev"], -5.0)

    def test_histogram_order(self):
        h = reject_histogram([_row(replay=0.05)] * 5 + [_row(replay=None)] * 2)
        self.assertGreaterEqual(h[0]["n"], h[-1]["n"])


class TestMoreCoverage(unittest.TestCase):
    def test_session_buckets_cover(self):
        # force hours via known timestamps
        asia = session_bucket(1_704_067_200)  # 2024-01-01 00:00 UTC
        self.assertEqual(asia, "asia")

    def test_compare_session(self):
        w = [_row(opened_at=1_704_067_200)]
        l = [_row(opened_at=1_704_096_000)]  # +8h europe-ish
        out = compare_categorical(w, l, key="session")
        self.assertTrue(out["rows"])

    def test_propose_best_or_none(self):
        rows = [_row(trade_id=1, replay=0.9, pnl=1)]
        out = propose_minimal_floors(rows)
        self.assertIn("note", out)

    def test_empty_sets(self):
        s = build_replay_sets([])
        self.assertEqual(s["recoverable_ev"], 0.0)
        self.assertIsNone(s["largest_mistake"])

    def test_all_pass_replay(self):
        rows = [_row(trade_id=i, replay=0.9, pnl=1) for i in range(5)]
        s = build_replay_sets(rows)
        self.assertEqual(s["n_rejected"], 0)

    def test_feature_aliases(self):
        self.assertEqual(get_numeric({"ATR": 3.3}, "atr"), 3.3)

    def test_engine_empty_db(self):
        conn = _conn()
        ensure_decision_journal_schema(conn)
        ensure_elite_candidate_schema(conn)
        ensure_reality_validation_schema(conn)
        ensure_paper_math_schema(conn)
        ensure_research_lake_schema(conn)
        out = run_replay_recovery_v1(conn, write_reports=False, persist=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_total"], 0)


class TestBulkHistogram(unittest.TestCase):
    pass


for _n in range(1, 16):
    def _make_h(n=_n):
        def test(self):
            rej = [_row(replay=0.05 if i % 2 else None) for i in range(n)]
            h = reject_histogram(rej)
            self.assertEqual(sum(x["n"] for x in h), n)
        return test
    setattr(TestBulkHistogram, f"test_hist_{_n}", _make_h())


if __name__ == "__main__":
    unittest.main()
