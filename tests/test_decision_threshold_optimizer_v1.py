"""Tests for Decision Threshold Optimizer V1."""

from __future__ import annotations

import unittest

import numpy as np

from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.evaluate import (
    accept_mask,
    binding_threshold,
    evaluate_thresholds,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.false_rejects import (
    analyze_top_false_rejects,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.grid import (
    BASELINE,
    ThresholdSet,
    grid_size,
    iter_threshold_grid,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.pareto import (
    best_overall,
    dominates,
    pareto_frontier,
    select_profiles,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.report import (
    format_best_profile_md,
    format_heatmap_md,
    format_pareto_md,
    format_terminal,
    format_threshold_report,
)


def _cache(n: int = 40) -> dict:
    rng = np.random.default_rng(42)
    pnl = rng.normal(0.5, 2.0, size=n)
    return {
        "ok": True,
        "n": n,
        "fp_sim": rng.uniform(0, 80, size=n),
        "tl_sim": rng.uniform(0, 90, size=n),
        "hist_wr": rng.uniform(30, 80, size=n),
        "hist_pf": rng.uniform(0.5, 2.5, size=n),
        "hist_ev": rng.uniform(-0.5, 2.0, size=n),
        "conf": rng.uniform(0.1, 0.9, size=n),
        "rules_n": rng.integers(0, 3, size=n).astype(np.int16),
        "dna_pass": rng.random(n) > 0.5,
        "edge_pass": rng.random(n) > 0.5,
        "replay_pass": rng.random(n) > 0.6,
        "causal_pass": rng.random(n) > 0.6,
        "brain_pass": rng.random(n) > 0.4,
        "blocked": np.zeros(n, dtype=bool),
        "brain_no": np.zeros(n, dtype=bool),
        "conflict": np.zeros(n, dtype=bool),
        "dir_ok": np.ones(n, dtype=bool),
        "pnl": pnl,
        "trade_ids": np.arange(1, n + 1, dtype=np.int64),
        "symbols": ["BTC"] * n,
        "why_baseline": [["x"]] * n,
    }


class TestGrid(unittest.TestCase):
    def test_grid_size(self):
        self.assertEqual(grid_size(), 3 * 6 * 6 * 6 * 4 * 4 * 3)

    def test_iter_count(self):
        self.assertEqual(sum(1 for _ in iter_threshold_grid()), grid_size())

    def test_baseline_fields(self):
        self.assertEqual(BASELINE.min_supporting, 2)
        self.assertAlmostEqual(BASELINE.min_confidence, 0.45)

    def test_threshold_label(self):
        self.assertIn("sup>=", BASELINE.label())


class TestEvaluate(unittest.TestCase):
    def test_accept_mask_shape(self):
        c = _cache(20)
        m = accept_mask(c, BASELINE)
        self.assertEqual(m.shape, (20,))
        self.assertEqual(m.dtype, bool)

    def test_looser_accepts_more(self):
        c = _cache(80)
        loose = ThresholdSet(1, 0.20, 10.0, 20.0, 40.0, 1.0, 0)
        tight = ThresholdSet(3, 0.70, 60.0, 70.0, 70.0, 2.0, 2)
        self.assertGreaterEqual(int(np.sum(accept_mask(c, loose))), int(np.sum(accept_mask(c, tight))))

    def test_evaluate_metrics_keys(self):
        out = evaluate_thresholds(_cache(50), BASELINE)
        for k in (
            "trades", "wr", "pf", "ev", "sharpe", "max_dd",
            "false_rejects", "false_approvals", "precision", "recall", "f1",
            "accepted", "rejected", "top_rejection_reason",
        ):
            self.assertIn(k, out)

    def test_binding_none_when_accepted(self):
        c = _cache(5)
        # force accept all soft gates
        c["fp_sim"][:] = 90
        c["tl_sim"][:] = 90
        c["hist_wr"][:] = 90
        c["hist_pf"][:] = 3
        c["conf"][:] = 0.9
        c["rules_n"][:] = 2
        c["dna_pass"][:] = True
        thr = ThresholdSet(1, 0.2, 10, 20, 40, 1.0, 0)
        self.assertIsNone(binding_threshold(c, 0, thr))

    def test_binding_confidence(self):
        c = _cache(3)
        c["fp_sim"][:] = 90
        c["tl_sim"][:] = 90
        c["hist_wr"][:] = 90
        c["hist_pf"][:] = 3
        c["conf"][:] = 0.1
        c["rules_n"][:] = 2
        c["dna_pass"][:] = True
        thr = ThresholdSet(1, 0.5, 10, 20, 40, 1.0, 0)
        self.assertEqual(binding_threshold(c, 0, thr), "min_confidence")


class TestPareto(unittest.TestCase):
    def test_dominates(self):
        a = {"ev": 2, "wr": 70, "sharpe": 1, "f1": 0.8, "precision": 0.8, "max_dd": -1, "false_rejects": 10}
        b = {"ev": 1, "wr": 60, "sharpe": 0.5, "f1": 0.5, "precision": 0.5, "max_dd": -5, "false_rejects": 20}
        self.assertTrue(dominates(a, b))
        self.assertFalse(dominates(b, a))

    def test_frontier_nonempty(self):
        results = [
            evaluate_thresholds(_cache(40), thr)
            for thr in list(iter_threshold_grid())[:50]
        ]
        front = pareto_frontier(results, min_trades=1)
        self.assertTrue(front)

    def test_profiles(self):
        results = [evaluate_thresholds(_cache(40), thr) for thr in list(iter_threshold_grid())[:80]]
        front = pareto_frontier(results, min_trades=1)
        profiles = select_profiles(front, results)
        self.assertIn("Aggressive", profiles)
        self.assertIn("Balanced", profiles)
        self.assertIn("Conservative", profiles)
        self.assertIn("Very Conservative", profiles)
        best = best_overall(profiles, front)
        self.assertTrue(best.get("label"))


class TestFalseRejects(unittest.TestCase):
    def test_top_false_rejects(self):
        c = _cache(60)
        # make baseline tight so many false rejects
        thr = ThresholdSet(3, 0.7, 60, 70, 70, 2.0, 2)
        out = analyze_top_false_rejects(c, thr, top_n=20)
        self.assertIn("binding_counts", out)
        self.assertIn("ev_recovered_if_relaxed", out)
        self.assertIn("top", out)
        if out["n"] > 0:
            self.assertLessEqual(len(out["top"]), 20)
            self.assertIn("binding_threshold", out["top"][0])

    def test_empty_when_all_accepted(self):
        c = _cache(10)
        c["fp_sim"][:] = 99
        c["tl_sim"][:] = 99
        c["hist_wr"][:] = 99
        c["hist_pf"][:] = 5
        c["conf"][:] = 0.99
        c["rules_n"][:] = 2
        c["dna_pass"][:] = True
        c["pnl"][:] = 1.0
        thr = ThresholdSet(1, 0.1, 1, 1, 1, 0.1, 0)
        out = analyze_top_false_rejects(c, thr, top_n=10)
        self.assertEqual(out["n"], 0)


class TestReports(unittest.TestCase):
    def _result(self):
        before = evaluate_thresholds(_cache(30), BASELINE)
        best = evaluate_thresholds(_cache(30), ThresholdSet(1, 0.3, 20, 30, 50, 1.2, 0))
        profiles = {
            "Aggressive": best,
            "Balanced": best,
            "Conservative": before,
            "Very Conservative": before,
        }
        return {
            "ok": True,
            "grid_size": grid_size(),
            "n_evaluated": 10,
            "cache_n": 30,
            "before": before,
            "best": best,
            "profiles": profiles,
            "pareto": [best, before],
            "false_reject_analysis": {"n": 1, "top100_pnl": 5.0, "binding_counts": {"min_hist_wr": 1},
                                      "ev_recovered_if_relaxed": {"min_hist_wr": 0.1},
                                      "mean_pnl_if_relaxed": {"min_hist_wr": 5.0}},
            "all_results": [before, best],
            "elapsed_sec": 1.0,
        }

    def test_terminal(self):
        text = format_terminal(self._result())
        self.assertIn("DECISION THRESHOLD OPTIMIZER V1", text)
        self.assertIn("Aggressive", text)
        self.assertIn("BEST", text)

    def test_md_reports(self):
        r = self._result()
        self.assertIn("DECISION_THRESHOLD_REPORT", format_threshold_report(r))
        self.assertIn("PARETO_FRONTIER", format_pareto_md(r))
        self.assertIn("BEST_PROFILE", format_best_profile_md(r))
        self.assertIn("THRESHOLD_HEATMAP", format_heatmap_md(r))


class TestWriterLockSemantics(unittest.TestCase):
    def test_insert_default_no_commit_flag(self):
        import inspect
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.writer import (
            insert_journal_batch,
        )
        sig = inspect.signature(insert_journal_batch)
        self.assertEqual(sig.parameters["commit"].default, False)

    def test_replace_atomic_exists(self):
        from bot.research.market_events.signal_intelligence.paper_decision_books_v1.writer import (
            replace_journal_atomic,
        )
        self.assertTrue(callable(replace_journal_atomic))


if __name__ == "__main__":
    unittest.main()
