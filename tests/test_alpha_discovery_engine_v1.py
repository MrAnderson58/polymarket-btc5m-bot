"""Alpha Discovery Engine V1 — metrics, FDR, mining, reports (20+)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.mining import (
    combine_rules,
    evaluate_rule,
    generate_atomic_rules,
    mine_alphas,
)
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report import (
    build_clusters,
    build_heatmaps,
    format_report,
    rank_candidates,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    benjamini_hochberg,
    bonferroni,
    bootstrap_expectancy_ci,
    permutation_pvalue,
    trade_metrics,
)


def _synth_rows(n: int = 80, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rsi = float(rng.uniform(20, 80))
        adx = float(rng.uniform(10, 50))
        # Plant edge: high RSI + high ADX → positive expectancy
        edge = (rsi > 60 and adx > 30)
        pnl = float(rng.normal(0.8 if edge else -0.15, 0.35))
        rows.append({
            "pnl": pnl,
            "rsi": rsi,
            "adx": adx,
            "ema20_distance": float(rng.normal(0, 0.01)),
            "ema50_distance": float(rng.normal(0, 0.015)),
            "ema200_distance": float(rng.normal(0, 0.02)),
            "vwap_distance": float(rng.normal(0, 0.01)),
            "atr": float(rng.uniform(20, 100)),
            "atr_pct": float(rng.uniform(0.1, 1.5)),
            "macd": float(rng.normal(0, 5)),
            "macd_hist": float(rng.normal(0, 2)),
            "stoch_k": float(rng.uniform(0, 100)),
            "stoch_d": float(rng.uniform(0, 100)),
            "bb_pct_b": float(rng.uniform(0, 1)),
            "slope": float(rng.normal(0, 0.1)),
            "trend": float(rng.normal(0, 1)),
            "funding": float(rng.normal(0, 0.0001)),
            "funding_delta": float(rng.normal(0, 0.00005)),
            "oi_delta": float(rng.normal(0, 1)),
            "fear_greed": float(rng.uniform(10, 90)),
            "volume": float(rng.uniform(100, 5000)),
            "volatility": float(rng.uniform(0.1, 2)),
            "confidence": float(rng.uniform(0.3, 0.9)),
            "hour": int(i % 24),
            "weekday": int(i % 7),
            "symbol": "BTC" if i % 2 == 0 else "ETH",
            "direction": "LONG" if i % 3 else "SHORT",
            "gate_decision": "ACCEPT",
            "market_regime": "TREND" if edge else "RANGE",
            "closed_at": 1_700_000_000 + i * 300,
            "created_at": 1_700_000_000 + i * 300,
        })
    return rows


class TestTradeMetrics(unittest.TestCase):
    def test_empty(self) -> None:
        m = trade_metrics([])
        self.assertEqual(m["n"], 0)
        self.assertIsNone(m["expectancy"])

    def test_winrate_expectancy(self) -> None:
        m = trade_metrics([1.0, 1.0, -0.5, 0.5])
        self.assertEqual(m["n"], 4)
        self.assertEqual(m["winrate"], 75.0)
        self.assertAlmostEqual(m["expectancy"], 0.5, places=3)

    def test_pf(self) -> None:
        m = trade_metrics([2.0, -1.0])
        self.assertEqual(m["pf"], 2.0)

    def test_pf_inf(self) -> None:
        m = trade_metrics([1.0, 2.0])
        self.assertTrue(m.get("pf_inf"))

    def test_sharpe_zero_var(self) -> None:
        m = trade_metrics([0.0, 0.0, 0.0])
        self.assertEqual(m["sharpe"], 0.0)

    def test_sharpe_positive(self) -> None:
        m = trade_metrics([1.0, 1.1, 0.9, 1.05])
        self.assertIsNotNone(m["sharpe"])
        assert m["sharpe"] is not None
        self.assertGreater(m["sharpe"], 0)


class TestBootstrapAndPValue(unittest.TestCase):
    def test_bootstrap_ci_order(self) -> None:
        lo, hi = bootstrap_expectancy_ci([1.0] * 20 + [0.5] * 5, n_boot=80, seed=1)
        self.assertIsNotNone(lo)
        self.assertIsNotNone(hi)
        assert lo is not None and hi is not None
        self.assertLessEqual(lo, hi)

    def test_bootstrap_small(self) -> None:
        self.assertEqual(bootstrap_expectancy_ci([1.0, 2.0], n_boot=10), (None, None))

    def test_permutation_detects_edge(self) -> None:
        base = list(np.random.default_rng(0).normal(0, 1, size=60))
        sel = list(np.random.default_rng(1).normal(1.5, 0.4, size=15))
        p = permutation_pvalue(sel, base + sel, n_perm=120, seed=2)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertLess(p, 0.15)

    def test_permutation_nullish(self) -> None:
        base = list(np.random.default_rng(3).normal(0, 1, size=50))
        sel = base[:10]
        p = permutation_pvalue(sel, base, n_perm=80, seed=4)
        self.assertIsNotNone(p)
        assert p is not None
        self.assertGreater(p, 0.05)


class TestMultipleTesting(unittest.TestCase):
    def test_bh_qvalues(self) -> None:
        qs = benjamini_hochberg([0.001, 0.02, 0.5, None], alpha=0.1)
        self.assertIsNotNone(qs[0])
        self.assertIsNone(qs[3])
        assert qs[0] is not None
        self.assertLessEqual(qs[0], 0.1)

    def test_bonferroni(self) -> None:
        bs = bonferroni([0.01, 0.04, None])
        self.assertEqual(bs[0], 0.02)
        self.assertIsNone(bs[2])


class TestMining(unittest.TestCase):
    def test_atomic_rules_nonempty(self) -> None:
        rules = generate_atomic_rules(_synth_rows(60))
        self.assertGreater(len(rules), 10)

    def test_combine_caps(self) -> None:
        atoms = generate_atomic_rules(_synth_rows(60))
        combined = combine_rules(atoms, max_combo=2, max_total=200)
        self.assertLessEqual(len(combined), 200)

    def test_evaluate_filters_min_n(self) -> None:
        rows = _synth_rows(40)
        atoms = generate_atomic_rules(rows)
        from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
            pnl_list,
        )
        out = evaluate_rule(
            rows, atoms[0], baseline_pnls=pnl_list(rows), min_n=10_000,
        )
        self.assertIsNone(out)

    def test_mine_finds_candidates(self) -> None:
        rows = _synth_rows(100, seed=11)
        with mock.patch.dict("os.environ", {
            "ALPHA_ENGINE_MAX_RULES": "400",
            "ALPHA_ENGINE_N_BOOT": "40",
            "ALPHA_ENGINE_N_PERM": "30",
            "ALPHA_ENGINE_MIN_N": "8",
        }):
            out = mine_alphas(rows, min_n=8, max_rules=400)
        self.assertGreater(out["n_rules_tested"], 50)
        self.assertGreater(len(out["candidates"]), 0)
        self.assertIn("n", out["baseline"])


class TestRankingAndReports(unittest.TestCase):
    def test_rank_adds_fdr(self) -> None:
        cands = [
            {"label": "a", "n": 20, "expectancy": 0.5, "expectancy_delta": 0.4,
             "pf_delta": 0.5, "p_value": 0.01, "features": ["rsi"], "pf": 1.5, "winrate": 60},
            {"label": "b", "n": 15, "expectancy": 0.1, "expectancy_delta": 0.05,
             "pf_delta": 0.1, "p_value": 0.4, "features": ["adx"], "pf": 1.1, "winrate": 52},
        ]
        ranked = rank_candidates(cands, fdr_alpha=0.1)
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertIn("q_value_fdr", ranked[0])
        self.assertIn("alpha_score", ranked[0])

    def test_heatmaps_and_clusters(self) -> None:
        ranked = [
            {"features": ["rsi", "adx"], "alpha_score": 0.5, "label": "x", "expectancy": 0.2,
             "pf": 1.4, "n": 12, "significant_fdr": True, "id": "1"},
            {"features": ["rsi"], "alpha_score": 0.3, "label": "y", "expectancy": 0.1,
             "pf": 1.2, "n": 20, "significant_fdr": False, "id": "2"},
        ]
        heat = build_heatmaps(ranked)
        self.assertIn("rsi", heat["labels"])
        clusters = build_clusters(ranked)
        self.assertGreaterEqual(len(clusters), 1)

    def test_format_report_contains_title(self) -> None:
        result = {"n_rows": 10, "n_atoms": 5, "n_rules_tested": 20, "min_n": 5,
                  "baseline": {"n": 10, "winrate": 50, "expectancy": 0, "pf": 1, "sharpe": 0}}
        ranked = [{"rank": 1, "alpha_score": 0.1, "label": "rsi>60", "n": 5, "winrate": 60,
                   "expectancy": 0.2, "pf": 1.5, "sharpe": 0.5, "ci_ev": (0, 0.4),
                   "p_value": 0.1, "q_value_fdr": 0.2, "features": ["rsi"]}]
        md = format_report(
            result=result,
            ranked=ranked,
            heatmaps={"labels": ["rsi"]},
            clusters=[{"cluster_id": "rsi", "size": 1, "best_expectancy": 0.2,
                       "best_pf": 1.5, "best_score": 0.1, "n_significant_fdr": 0,
                       "best_label": "rsi>60"}],
        )
        self.assertIn("ALPHA_DISCOVERY_REPORT", md)
        self.assertIn("rsi>60", md)

    def test_write_artifacts(self) -> None:
        result = {"n_rows": 10, "n_atoms": 5, "n_rules_tested": 20, "min_n": 5,
                  "baseline": {"n": 10, "winrate": 50, "expectancy": 0, "pf": 1, "sharpe": 0}}
        ranked = [{"rank": 1, "alpha_score": 0.2, "label": "adx high", "n": 8, "winrate": 62,
                   "expectancy": 0.3, "pf": 1.6, "sharpe": 0.4, "ci_ev": (0.1, 0.5),
                   "p_value": 0.05, "q_value_fdr": 0.08, "significant_fdr": True,
                   "features": ["adx"], "id": "adx_gt"}]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report.OUT_DIR",
                td_path / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report.REPORT_MD",
                td_path / "ALPHA_DISCOVERY_REPORT.md",
            ):
                paths = write_artifacts(result=result, ranked=ranked)
            self.assertTrue(Path(paths["report_md"]).exists())
            self.assertTrue(Path(paths["candidates_json"]).exists())
            self.assertTrue(Path(paths["heatmaps_json"]).exists())
            self.assertTrue(Path(paths["clusters_json"]).exists())
            payload = json.loads(Path(paths["candidates_json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["candidates"][0]["label"], "adx high")


class TestEngineImport(unittest.TestCase):
    def test_package_export(self) -> None:
        from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1 import (
            run_alpha_engine_v1,
        )
        self.assertTrue(callable(run_alpha_engine_v1))

    def test_engine_on_synth(self) -> None:
        from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.engine import (
            run_alpha_engine_v1,
        )
        rows = _synth_rows(90, seed=19)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.engine.load_alpha_dataset",
            return_value=rows,
        ), mock.patch.dict("os.environ", {
            "ALPHA_ENGINE_MAX_RULES": "300",
            "ALPHA_ENGINE_N_BOOT": "30",
            "ALPHA_ENGINE_N_PERM": "25",
        }), tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report.OUT_DIR",
                td_path / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report.REPORT_MD",
                td_path / "ALPHA_DISCOVERY_REPORT.md",
            ):
                out = run_alpha_engine_v1(conn=None, write_reports=True, min_n=8)
            self.assertTrue(out["ok"])
            self.assertGreater(out["n_candidates"], 0)
            self.assertTrue(out["gate_trading_unchanged"])


if __name__ == "__main__":
    unittest.main()
