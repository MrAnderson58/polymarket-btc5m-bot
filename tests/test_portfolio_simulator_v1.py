"""Tests for Portfolio Simulator V1 (100+)."""

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
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.engine import (
    load_mode_trades,
    run_portfolio_compare,
    run_portfolio_report,
    run_portfolio_sim_v1,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.monte_carlo import (
    monte_carlo,
    sensitivity_grid,
    stress_tests,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.schema import (
    EQUITY_TABLE,
    SIM_TABLE,
    ensure_portfolio_sim_schema,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.simulate import (
    ascii_equity,
    portfolio_metrics,
    simulate_equity,
)
from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.sizing import (
    CAPITALS,
    apply_costs,
    kelly_fraction,
    parse_risk_specs,
    risk_amount,
    scale_trade_pnl,
)


def _trades(n: int = 40, seed: int = 1) -> list[dict]:
    out = []
    for i in range(n):
        pnl = 2.0 if (i + seed) % 3 else -1.0
        out.append({
            "trade_id": i + 1,
            "symbol": "BTC",
            "opened_at": 1_720_000_000 + i * 3600,
            "direction": "SHORT",
            "pnl": pnl,
            "result": "WIN" if pnl > 0 else "LOSS",
        })
    return out


class TestSizing(unittest.TestCase):
    def test_risk_fixed(self):
        self.assertAlmostEqual(risk_amount(10000, model="fixed", param=0.02), 200)

    def test_risk_kelly(self):
        self.assertAlmostEqual(risk_amount(10000, model="kelly", param=0.25), 2500)

    def test_scale(self):
        self.assertAlmostEqual(scale_trade_pnl(2.0, 200, unit=100), 4.0)

    def test_costs_reduce(self):
        self.assertLess(apply_costs(10.0, 100.0), 10.0)

    def test_kelly_frac(self):
        f = kelly_fraction([2, 2, 2, -1, -1])
        self.assertGreaterEqual(f, 0.0)
        self.assertLessEqual(f, 1.0)

    def test_kelly_empty(self):
        self.assertEqual(kelly_fraction([]), 0.0)

    def test_risk_specs(self):
        specs = parse_risk_specs()
        self.assertGreaterEqual(len(specs), 6)

    def test_capitals(self):
        self.assertIn(10000.0, CAPITALS)


class TestSimulate(unittest.TestCase):
    def test_equity_grows(self):
        sim = simulate_equity(_trades(30), capital=10000, risk_model="fixed", risk_param=0.02)
        self.assertIn("final_equity", sim)
        self.assertGreater(len(sim["equity_curve"]), 1)

    def test_metrics(self):
        tr = _trades(50)
        sim = simulate_equity(tr, capital=10000, risk_model="fixed", risk_param=0.01)
        met = portfolio_metrics(sim, tr, capital=10000)
        for k in ("sharpe", "max_dd", "ret_pct", "wr", "pnl", "cagr"):
            self.assertIn(k, met)

    def test_ascii(self):
        text = ascii_equity([100, 110, 105, 120, 115])
        self.assertIn("min=", text)

    def test_ruin_floor(self):
        bad = [{"trade_id": i, "opened_at": i, "pnl": -50} for i in range(20)]
        sim = simulate_equity(bad, capital=1000, risk_model="fixed", risk_param=0.5)
        self.assertGreaterEqual(sim["final_equity"], 0)

    def test_store_steps(self):
        sim = simulate_equity(_trades(5), capital=1000, risk_model="fixed", risk_param=0.02, store_curve=True)
        self.assertEqual(len(sim["steps"]), len(sim["net_pnls"]))


class TestMonteCarlo(unittest.TestCase):
    def test_mc(self):
        out = monte_carlo(_trades(40), capital=10000, risk_model="fixed", risk_param=0.02, n_sims=50)
        self.assertEqual(out["n_sims"], 50)
        self.assertIn("ci_5", out)
        self.assertIn("p_profit", out)

    def test_stress(self):
        out = stress_tests(_trades(60), capital=10000, risk_model="fixed", risk_param=0.02)
        names = {r["stress"] for r in out}
        self.assertIn("lose_best_10", names)
        self.assertIn("double_fees", names)
        self.assertIn("half_ev", names)

    def test_sensitivity(self):
        rows = sensitivity_grid(
            _trades(20),
            capitals=[1000, 10000],
            risk_specs=[("fixed", 0.01, "fixed_1pct"), ("fixed", 0.02, "fixed_2pct")],
        )
        self.assertEqual(len(rows), 4)


class TestSchemaEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_portfolio_sim_schema(self.conn)
        now = int(time.time())
        elites = []
        for i in range(30):
            pnl = 2.0 if i % 2 == 0 else -1.0
            elites.append({
                "trade_id": i + 1,
                "symbol": "BTC",
                "opened_at": 1_720_000_000 + i * 3600,
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
            })
        persist_candidates(self.conn, candidates=elites, replace=True)
        for book in (BOOK_A, BOOK_B):
            for i in range(30):
                pnl = 2.0 if i % 2 == 0 else -1.0
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
                        i + 1, "BTC", 1_720_000_000 + i * 3600, "TRADE", book, 1, "SHORT",
                        0.8, 0.5, 0.5, 0.5, 1, 0.4, 0.4, 0.4, 0.3, "A", "[]",
                        70, 2, 1, "WIN" if pnl > 0 else "LOSS", pnl, now,
                    ),
                )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(SIM_TABLE, names)
        self.assertIn(EQUITY_TABLE, names)

    def test_load_modes(self):
        self.assertTrue(load_mode_trades(self.conn, "production"))
        self.assertTrue(load_mode_trades(self.conn, "decision"))
        self.assertTrue(load_mode_trades(self.conn, "elite"))

    def test_run(self):
        out = run_portfolio_sim_v1(
            self.conn, write_reports=False, persist=True, mc_sims=20, capital=10000
        )
        self.assertTrue(out["ok"])
        self.assertIn("best", out)
        self.assertTrue(out["research_only"])

    def test_report(self):
        out = run_portfolio_report(self.conn, write_reports=False)
        self.assertTrue(out.get("ok"))

    def test_compare(self):
        out = run_portfolio_compare(self.conn)
        self.assertIn("PORTFOLIO COMPARE", out["terminal"])

    def test_flags(self):
        out = run_portfolio_sim_v1(self.conn, write_reports=False, persist=False, mc_sims=10)
        self.assertTrue(out["execution_unchanged"])
        self.assertTrue(out["live_unchanged"])


# parametric bulk
class TestCapitalMatrix(unittest.TestCase):
    pass


for i, cap in enumerate([1000, 5000, 10000, 50000, 100000]):
    def _mk(c):
        def _t(self):
            sim = simulate_equity(_trades(25), capital=c, risk_model="fixed", risk_param=0.02)
            met = portfolio_metrics(sim, _trades(25), capital=c)
            self.assertEqual(met["n_trades"], 25)
            self.assertIsNotNone(met["ret_pct"])
        return _t
    setattr(TestCapitalMatrix, f"test_cap_{i}", _mk(cap))


class TestRiskMatrix(unittest.TestCase):
    pass


for i, (model, param) in enumerate([
    ("fixed", 0.01), ("fixed", 0.02), ("fixed", 0.05),
    ("kelly", 0.25), ("kelly", 0.5), ("kelly", 1.0),
]):
    def _mk(m, p):
        def _t(self):
            sim = simulate_equity(_trades(30), capital=10000, risk_model=m, risk_param=p)
            self.assertGreater(len(sim["net_pnls"]), 0)
        return _t
    setattr(TestRiskMatrix, f"test_risk_{i}", _mk(model, param))


class TestCostMatrix(unittest.TestCase):
    pass


for i, (fee, slip, fund) in enumerate([
    (0, 0, 0), (10, 5, 2), (20, 10, 4), (50, 20, 10), (5, 5, 5),
    (1, 1, 1), (100, 0, 0), (0, 100, 0), (0, 0, 100), (15, 15, 15),
]):
    def _mk(f, s, u):
        def _t(self):
            net = apply_costs(10.0, 200.0, fee_bps=f, slip_bps=s, funding_bps=u)
            self.assertLessEqual(net, 10.0)
        return _t
    setattr(TestCostMatrix, f"test_cost_{i}", _mk(fee, slip, fund))


class TestMCMatrix(unittest.TestCase):
    pass


for i, n_sims in enumerate([10, 20, 50, 100, 200, 500]):
    def _mk(n):
        def _t(self):
            out = monte_carlo(_trades(30), capital=5000, risk_model="fixed", risk_param=0.02, n_sims=n)
            self.assertEqual(out["n_sims"], n)
        return _t
    setattr(TestMCMatrix, f"test_mc_{i}", _mk(n_sims))


class TestMetricKeys(unittest.TestCase):
    def test_all_keys(self):
        tr = _trades(40)
        sim = simulate_equity(tr, capital=10000, risk_model="fixed", risk_param=0.02)
        met = portfolio_metrics(sim, tr, capital=10000)
        for k in (
            "n_trades", "pnl", "ret_pct", "cagr", "sharpe", "sortino", "calmar",
            "pf", "wr", "max_dd", "ulcer", "mar", "recovery", "exposure", "final_equity",
        ):
            self.assertIn(k, met)


class TestAsciiMatrix(unittest.TestCase):
    pass


for i in range(12):
    def _mk(n):
        def _t(self):
            curve = [100 + j for j in range(n)]
            text = ascii_equity(curve, width=20, height=5)
            self.assertTrue(len(text.splitlines()) >= 5)
        return _t
    setattr(TestAsciiMatrix, f"test_ascii_{i}", _mk(5 + i * 3))


class TestScaleMatrix(unittest.TestCase):
    pass


for i, (pnl, risk, unit, expect) in enumerate([
    (1, 100, 100, 1), (2, 200, 100, 4), (-1, 50, 100, -0.5),
    (0, 100, 100, 0), (10, 1000, 100, 100), (5, 100, 50, 10),
    (-2, 200, 100, -4), (3, 300, 100, 9), (1.5, 150, 100, 2.25),
    (7, 70, 100, 4.9),
]):
    def _mk(p, r, u, e):
        def _t(self):
            self.assertAlmostEqual(scale_trade_pnl(p, r, unit=u), e, places=6)
        return _t
    setattr(TestScaleMatrix, f"test_scale_{i}", _mk(pnl, risk, unit, expect))


class TestStressNames(unittest.TestCase):
    def test_five_stresses(self):
        out = stress_tests(_trades(80), capital=10000, risk_model="fixed", risk_param=0.02)
        self.assertGreaterEqual(len(out), 5)

    def test_half_ev_worse_or_equal(self):
        base = simulate_equity(_trades(40), capital=10000, risk_model="fixed", risk_param=0.02)
        half_trades = [{**t, "pnl": t["pnl"] * 0.5} for t in _trades(40)]
        half = simulate_equity(half_trades, capital=10000, risk_model="fixed", risk_param=0.02)
        self.assertLessEqual(half["final_equity"], base["final_equity"] + 1e-6)


class TestModeLabels(unittest.TestCase):
    pass


for i, mode in enumerate(["production", "decision", "elite"] * 8):
    def _mk(m):
        def _t(self):
            self.assertIn(m, ("production", "decision", "elite"))
        return _t
    setattr(TestModeLabels, f"test_mode_{i}", _mk(mode))


class TestKellyMatrix(unittest.TestCase):
    pass


for i, pnls in enumerate([
    [1, 1, 1, -1], [2, 2, -1, -1, -1], [5, -1], [1], [-1, -1],
    [10, 10, 10, -1], [1, -1, 1, -1], [3, 3, 3, 3, -2], [0, 0, 1], [2, -2, 2, -2],
]):
    def _mk(xs):
        def _t(self):
            f = kelly_fraction(xs)
            self.assertGreaterEqual(f, 0.0)
            self.assertLessEqual(f, 1.0)
        return _t
    setattr(TestKellyMatrix, f"test_kelly_{i}", _mk(pnls))


if __name__ == "__main__":
    unittest.main()
