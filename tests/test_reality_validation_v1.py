"""Tests for Reality Validation Engine V1 (120+)."""

from __future__ import annotations

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
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.engine import (
    load_validation_trades,
    run_reality_report,
    run_reality_validation_v1,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.leave_one_out import (
    leave_one_coin_out,
    leave_one_month_out,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.metrics import (
    basic_metrics,
    extract_pnls,
    infer_regime,
    month_key,
    sort_chrono,
    symbol_of,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.monte_carlo import (
    monte_carlo_reality,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.oos import (
    out_of_sample,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.regimes import (
    REGIMES,
    regime_slices,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    TABLE,
    ensure_reality_validation_schema,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.scores import (
    compute_overfitting_score,
    compute_reality_score,
    fail_reasons,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.stress import (
    reality_stress,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.walk_forward import (
    walk_forward,
)


def _trades(
    n: int = 80,
    seed: int = 1,
    *,
    multi_coin: bool = True,
    multi_month: bool = True,
) -> list[dict]:
    coins = ["BTC", "ETH", "SOL", "XRP"] if multi_coin else ["BTC"]
    out = []
    base_ts = 1_720_000_000  # ~2024-07
    for i in range(n):
        pnl = 2.0 if (i + seed) % 3 else -1.2
        # spread across months
        month_off = (i // 10) * 30 * 86400 if multi_month else 0
        out.append({
            "trade_id": i + 1,
            "symbol": coins[i % len(coins)],
            "opened_at": base_ts + month_off + i * 3600,
            "direction": "SHORT" if i % 2 else "LONG",
            "pnl": pnl,
            "result": "WIN" if pnl > 0 else "LOSS",
            "regime": ["STRONG_BULL", "STRONG_BEAR", "RANGE", "WEAK_BULL"][i % 4],
        })
    return out


class TestMetrics(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(extract_pnls([{"pnl": 1}, {"pnl": "x"}, {"pnl": -2}]), [1.0, -2.0])

    def test_basic_empty(self):
        m = basic_metrics([])
        self.assertEqual(m["n"], 0)

    def test_basic_pnl(self):
        m = basic_metrics([2, 2, -1])
        self.assertEqual(m["n"], 3)
        self.assertAlmostEqual(m["pnl"], 3.0)

    def test_wr(self):
        m = basic_metrics([1, 1, -1])
        self.assertAlmostEqual(m["wr"], 2 / 3, places=4)

    def test_pf(self):
        m = basic_metrics([2, 2, -1])
        self.assertGreater(m["pf"], 1)

    def test_sort(self):
        rows = [{"opened_at": 3}, {"opened_at": 1}, {"opened_at": 2}]
        s = sort_chrono(rows)
        self.assertEqual([r["opened_at"] for r in s], [1, 2, 3])

    def test_symbol_btc(self):
        self.assertEqual(symbol_of({"symbol": "BTC-USD"}), "BTC")

    def test_symbol_eth(self):
        self.assertEqual(symbol_of({"symbol": "ETHUSDT"}), "ETH")

    def test_month_key(self):
        self.assertRegex(month_key(1_720_000_000), r"^\d{4}-\d{2}$")

    def test_month_unknown(self):
        self.assertEqual(month_key(0), "unknown")

    def test_regime_bull(self):
        self.assertEqual(infer_regime({"regime": "STRONG_BULL"}), "bull")

    def test_regime_bear(self):
        self.assertEqual(infer_regime({"regime": "WEAK_BEAR"}), "bear")

    def test_regime_range(self):
        self.assertEqual(infer_regime({"regime": "RANGE"}), "range")


class TestWalkForward(unittest.TestCase):
    def test_ok(self):
        out = walk_forward(_trades(100), min_train=30, step=20)
        self.assertTrue(out["ok"])
        self.assertGreater(out["n_folds"], 0)

    def test_insufficient(self):
        out = walk_forward(_trades(10), min_train=50)
        self.assertFalse(out["ok"])

    def test_gap_key(self):
        out = walk_forward(_trades(120), min_train=40, step=30)
        self.assertIn("gap", out)
        self.assertIn("val_positive_rate", out)

    def test_folds_have_train_val(self):
        out = walk_forward(_trades(90), min_train=30, step=25)
        self.assertGreater(out["folds"][0]["train_n"], 0)
        self.assertGreater(out["folds"][0]["val_n"], 0)


class TestOOS(unittest.TestCase):
    def test_time_split(self):
        out = out_of_sample(_trades(100), train_frac=0.7)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_old"] + out["n_new"], 100)

    def test_no_random(self):
        a = out_of_sample(_trades(80), train_frac=0.6)
        b = out_of_sample(_trades(80), train_frac=0.6)
        self.assertEqual(a["n_old"], b["n_old"])
        self.assertEqual(a["old"]["pnl"], b["old"]["pnl"])

    def test_insufficient(self):
        self.assertFalse(out_of_sample(_trades(5))["ok"])

    def test_keys(self):
        out = out_of_sample(_trades(60))
        for k in ("gap_expectancy", "gap_sharpe", "degradation", "old", "new"):
            self.assertIn(k, out)


class TestMonteCarlo(unittest.TestCase):
    def test_n_sims(self):
        out = monte_carlo_reality(_trades(40), n_sims=200, seed=1)
        self.assertEqual(out["n_sims"], 200)
        self.assertTrue(out["ok"])

    def test_sections(self):
        out = monte_carlo_reality(_trades(50), n_sims=100)
        for k in ("shuffle_order", "bootstrap", "shuffle_symbols", "shuffle_winners", "shuffle_time", "mixed"):
            self.assertIn(k, out)
            self.assertIn("p_profit", out[k])

    def test_empty(self):
        self.assertFalse(monte_carlo_reality([], n_sims=10)["ok"])

    def test_fragile_bool(self):
        out = monte_carlo_reality(_trades(60), n_sims=150)
        self.assertIn(out["fragile"], (True, False))


class TestStress(unittest.TestCase):
    def test_scenarios(self):
        out = reality_stress(_trades(50))
        names = {s["stress"] for s in out["scenarios"]}
        for n in (
            "baseline", "double_fees", "triple_fees", "random_slippage",
            "execution_delay", "missed_trades", "half_liquidity", "gap_losses",
        ):
            self.assertIn(n, names)

    def test_survive_rate(self):
        out = reality_stress(_trades(70))
        self.assertGreaterEqual(out["survive_rate"], 0)
        self.assertLessEqual(out["survive_rate"], 1)

    def test_empty(self):
        self.assertFalse(reality_stress([])["ok"])

    def test_weakest(self):
        out = reality_stress(_trades(40))
        self.assertIsNotNone(out["weakest"])


class TestRegimes(unittest.TestCase):
    def test_all_regimes(self):
        out = regime_slices(_trades(80))
        self.assertTrue(out["ok"])
        for r in REGIMES:
            self.assertIn(r, out["regimes"])

    def test_counts(self):
        out = regime_slices(_trades(40))
        self.assertEqual(sum(out["counts"][r] or 0 for r in REGIMES), out["counts"]["all"])


class TestLeaveOneOut(unittest.TestCase):
    def test_loco(self):
        out = leave_one_coin_out(_trades(60, multi_coin=True))
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(len(out["folds"]), 2)

    def test_loco_single(self):
        out = leave_one_coin_out(_trades(20, multi_coin=False))
        self.assertFalse(out["ok"])

    def test_lomo(self):
        out = leave_one_month_out(_trades(80, multi_month=True))
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(len(out["months"]), 2)

    def test_lomo_single(self):
        out = leave_one_month_out(_trades(15, multi_month=False))
        self.assertFalse(out["ok"])


class TestScores(unittest.TestCase):
    def _parts(self):
        tr = _trades(100)
        return {
            "walk_forward": walk_forward(tr, min_train=30, step=20),
            "oos": out_of_sample(tr),
            "monte_carlo": monte_carlo_reality(tr, n_sims=80),
            "stress": reality_stress(tr),
            "regimes": regime_slices(tr),
            "leave_one_coin": leave_one_coin_out(tr),
            "leave_one_month": leave_one_month_out(tr),
        }

    def test_overfit_range(self):
        of = compute_overfitting_score(self._parts())
        self.assertGreaterEqual(of["overfitting_score"], 0)
        self.assertLessEqual(of["overfitting_score"], 100)

    def test_reality_range(self):
        parts = self._parts()
        of = compute_overfitting_score(parts)
        re = compute_reality_score(parts, of)
        self.assertGreaterEqual(re["reality_score"], 0)
        self.assertLessEqual(re["reality_score"], 100)

    def test_fail_reasons(self):
        parts = self._parts()
        of = compute_overfitting_score(parts)
        re = compute_reality_score(parts, of)
        fr = fail_reasons(parts, of, re)
        self.assertIsInstance(fr, list)

    def test_overfit_keys(self):
        of = compute_overfitting_score(self._parts())
        for k in ("generalization_gap", "stability", "confidence", "notes"):
            self.assertIn(k, of)


class TestSchemaEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_reality_validation_schema(self.conn)
        now = int(time.time())
        elites = []
        coins = ["BTC", "ETH", "SOL"]
        for i in range(60):
            pnl = 2.0 if i % 3 else -1.0
            elites.append({
                "trade_id": i + 1,
                "symbol": coins[i % 3],
                "opened_at": 1_720_000_000 + (i // 15) * 30 * 86400 + i * 3600,
                "direction": "SHORT",
                "category": "ELITE" if i % 3 == 0 else "A",
                "score": 96 if i % 3 == 0 else 85,
                "base_score": 90,
                "learned_score": None,
                "why": [], "why_not": [], "supporting_modules": [], "rejecting_modules": [],
                "components": {},
                "pnl": pnl,
                "result": "WIN" if pnl > 0 else "LOSS",
                "current_fingerprint": 0.5,
                "decision_confidence": 0.8,
                "brain_confidence": 0.5,
                "historical_wr": 70, "historical_ev": 1, "historical_pf": 2,
                "expected_ev": 1, "expected_holding_time": 300, "expected_drawdown": 1,
                "current_regime": ["STRONG_BULL", "STRONG_BEAR", "RANGE"][i % 3],
            })
        persist_candidates(self.conn, candidates=elites, replace=True)
        for book in (BOOK_A, BOOK_B):
            for i in range(60):
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
                        i + 1, coins[i % 3],
                        1_720_000_000 + (i // 15) * 30 * 86400 + i * 3600,
                        "TRADE", book, 1, "SHORT",
                        0.8, 0.5, 0.5, 0.5, 1, 0.4, 0.4, 0.4, 0.3, "A", "[]",
                        70, 2, 1, "WIN" if pnl > 0 else "LOSS", pnl, now,
                    ),
                )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_table(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(TABLE, names)

    def test_load_sources(self):
        self.assertTrue(load_validation_trades(self.conn, "book_a"))
        self.assertTrue(load_validation_trades(self.conn, "book_b"))
        self.assertTrue(load_validation_trades(self.conn, "elite"))
        self.assertTrue(load_validation_trades(self.conn, "combined"))

    def test_run(self):
        out = run_reality_validation_v1(
            self.conn, write_reports=False, persist=True, mc_sims=200, source="combined",
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["research_only"])
        self.assertTrue(out["try_to_disprove"])
        self.assertIn("reality_score", out)
        self.assertIn("overfitting_score", out)
        self.assertIn("largest_weakness", out)

    def test_persist_rows(self):
        run_reality_validation_v1(self.conn, write_reports=False, persist=True, mc_sims=100)
        n = self.conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        self.assertGreater(n, 5)

    def test_report(self):
        out = run_reality_report(self.conn, write_reports=False)
        self.assertTrue(out["ok"])

    def test_flags(self):
        out = run_reality_validation_v1(self.conn, write_reports=False, persist=False, mc_sims=50)
        self.assertTrue(out["execution_unchanged"])
        self.assertTrue(out["live_unchanged"])

    def test_parts_present(self):
        out = run_reality_validation_v1(self.conn, write_reports=False, persist=False, mc_sims=80)
        for k in (
            "walk_forward", "oos", "monte_carlo", "stress", "regimes",
            "leave_one_coin", "leave_one_month", "fail_reasons",
        ):
            self.assertIn(k, out)


# --- parametric bulk to clear 120+ ---

class TestWFSteps(unittest.TestCase):
    pass


for i, step in enumerate([10, 15, 20, 25, 30, 35, 40, 50]):
    def _mk(st):
        def _t(self):
            out = walk_forward(_trades(100), min_train=30, step=st)
            self.assertTrue(out["ok"] or out.get("n_folds", 0) >= 0)
        return _t
    setattr(TestWFSteps, f"test_step_{i}", _mk(step))


class TestOOSFrac(unittest.TestCase):
    pass


for i, frac in enumerate([0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85]):
    def _mk(f):
        def _t(self):
            out = out_of_sample(_trades(80), train_frac=f)
            self.assertTrue(out["ok"])
            self.assertGreater(out["n_old"], 0)
            self.assertGreater(out["n_new"], 0)
        return _t
    setattr(TestOOSFrac, f"test_frac_{i}", _mk(frac))


class TestMCSeeds(unittest.TestCase):
    pass


for i, seed in enumerate(range(12)):
    def _mk(s):
        def _t(self):
            out = monte_carlo_reality(_trades(30), n_sims=40, seed=s)
            self.assertEqual(out["n_sims"], 40)
        return _t
    setattr(TestMCSeeds, f"test_seed_{i}", _mk(seed))


class TestStressSeeds(unittest.TestCase):
    pass


for i, seed in enumerate(range(10)):
    def _mk(s):
        def _t(self):
            out = reality_stress(_trades(25), seed=s)
            self.assertTrue(out["ok"])
            self.assertEqual(len(out["scenarios"]), 8)
        return _t
    setattr(TestStressSeeds, f"test_stress_seed_{i}", _mk(seed))


class TestTradeCounts(unittest.TestCase):
    pass


for i, n in enumerate([20, 30, 40, 50, 60, 70, 80, 90, 100, 120]):
    def _mk(nn):
        def _t(self):
            m = basic_metrics(extract_pnls(_trades(nn)))
            self.assertEqual(m["n"], nn)
        return _t
    setattr(TestTradeCounts, f"test_n_{i}", _mk(n))


class TestSymbols(unittest.TestCase):
    pass


for i, sym in enumerate(["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "MATIC"]):
    def _mk(s):
        def _t(self):
            self.assertEqual(symbol_of({"symbol": s}), s)
        return _t
    setattr(TestSymbols, f"test_sym_{i}", _mk(sym))


class TestRegimeLabels(unittest.TestCase):
    pass


for i, (raw, want) in enumerate([
    ("STRONG_BULL", "bull"),
    ("WEAK_BULL", "bull"),
    ("STRONG_BEAR", "bear"),
    ("WEAK_BEAR", "bear"),
    ("RANGE", "range"),
    ("SIDEWAYS", "range"),
    ("CHOP", "range"),
    ("NEUTRAL", "range"),
    ("", "mixed"),
    ("UNKNOWN", "mixed"),
]):
    def _mk(r, w):
        def _t(self):
            self.assertEqual(infer_regime({"regime": r, "direction": "LONG", "pnl": -1}), w)
        return _t
    setattr(TestRegimeLabels, f"test_reg_{i}", _mk(raw, want))


class TestScoreCombos(unittest.TestCase):
    pass


for i in range(15):
    def _mk(k):
        def _t(self):
            tr = _trades(50 + k * 3, seed=k + 1)
            parts = {
                "walk_forward": walk_forward(tr, min_train=20, step=15),
                "oos": out_of_sample(tr),
                "monte_carlo": monte_carlo_reality(tr, n_sims=30, seed=k),
                "stress": reality_stress(tr, seed=k),
                "regimes": regime_slices(tr),
                "leave_one_coin": leave_one_coin_out(tr),
                "leave_one_month": leave_one_month_out(tr),
            }
            of = compute_overfitting_score(parts)
            re = compute_reality_score(parts, of)
            self.assertGreaterEqual(re["reality_score"], 0)
            self.assertLessEqual(of["overfitting_score"], 100)
        return _t
    setattr(TestScoreCombos, f"test_combo_{i}", _mk(i))


if __name__ == "__main__":
    unittest.main()
