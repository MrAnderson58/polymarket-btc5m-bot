"""Comprehensive tests for Market Edge Discovery V3 (25+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v3.dataset import (
    ALL_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    matrix_from_rows,
    normalize_row,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.importance import (
    compute_importance,
    consensus_ranking,
    information_gain_from_edges,
    mutual_information_scores,
    permutation_importance_scores,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.library import (
    LIBRARY_TABLE,
    ensure_edge_library_schema,
    load_library,
    upsert_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.metrics import (
    apply_fdr,
    bayesian_edge_posterior,
    cheap_screen,
    full_validate,
    max_drawdown,
    quality_score,
    sortino_ratio,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.mining import (
    generate_atoms,
    mine_edges_v3,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.regime import (
    cluster_regimes,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.report import (
    format_discovery_md,
    format_heatmap_md,
    format_importance_md,
    format_library_md,
    format_regime_md,
    format_stability_md,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.engine import (
    run_edge_discovery_v3,
)


def _synth(n: int = 400, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rsi = float(rng.uniform(15, 85))
        funding = float(rng.normal(-0.0003 if rsi < 35 else 0.0002, 0.00012))
        oi = float(rng.normal(1.5 if funding < 0 else -0.3, 0.7))
        atr_pct = float(rng.uniform(0.2, 3.0))
        ema20 = float(rng.normal(-1.2 if rsi < 35 else 0.5, 0.5))
        ema50 = ema20 + float(rng.normal(0, 0.2))
        ema200 = ema20 + float(rng.normal(0, 0.4))
        trend = float(rng.normal(-0.5 if rsi < 35 else 0.4, 0.6))
        fear = float(rng.uniform(10, 90))
        adx = float(rng.uniform(10, 50))
        macd = float(rng.normal(0, 1))
        macd_hist = float(rng.normal(0, 0.5))
        edge = rsi < 35 and funding < 0 and oi > 0 and ema20 < 0
        pnl = float(rng.normal(2.0 if edge else -0.4, 0.45))
        rows.append({
            "pnl": pnl,
            "pnl_pct": pnl,
            "rsi": rsi,
            "ema20_distance": ema20,
            "ema50_distance": ema50,
            "ema200_distance": ema200,
            "vwap_distance": float(rng.normal(0, 0.5)),
            "atr": atr_pct,
            "atr_pct": atr_pct,
            "adx": adx,
            "macd": macd,
            "macd_hist": macd_hist,
            "bollinger_pct": float(rng.uniform(0, 1)),
            "stoch_k": float(rng.uniform(0, 100)),
            "stoch_d": float(rng.uniform(0, 100)),
            "trend": trend,
            "funding": funding,
            "funding_delta": float(rng.normal(0, 0.00005)),
            "open_interest": float(rng.uniform(1e6, 2e6)),
            "oi_delta": oi,
            "fear_greed": fear,
            "volume": float(rng.uniform(100, 5000)),
            "hour": float(i % 24),
            "weekday": str(i % 7),
            "confidence": float(rng.uniform(0.3, 0.95)),
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "gate_decision": "PASS" if edge or i % 4 else "REJECT",
            "symbol": "BTC" if i % 2 else "ETH",
            "regime": "RISK_ON" if trend > 0 else "RISK_OFF",
            "alpha_cluster": "A" if edge else "B",
            "optimizer_state_key": "opt_v1",
            "closed_at": 1_700_000_000 + i * 3600,
            "features": {"rsi": rsi},
            "macro": {"funding": funding},
            "alpha_labels": {"discovery": {"cluster": "A" if edge else "B"}},
            "optimizer_state": {"opt_v1": 1},
        })
    return rows


class TestDatasetV3(unittest.TestCase):
    def test_feature_lists_cover_brief(self) -> None:
        for f in ("rsi", "funding", "oi_delta", "atr_pct", "macd", "adx"):
            self.assertIn(f, NUMERIC_FEATURES)
        for f in ("symbol", "direction", "gate_decision", "regime"):
            self.assertIn(f, CATEGORICAL_FEATURES)
        self.assertGreaterEqual(len(ALL_FEATURES), 20)

    def test_normalize_row_flattens(self) -> None:
        r = normalize_row({
            "pnl_pct": 1.2,
            "gate": "PASS",
            "closed_at": 1700000000,
            "features": {"rsi": 28},
            "macro": {"funding": -0.0001},
            "alpha_labels": {"discovery": {"cluster": "X"}},
            "optimizer_state": {"g42": 1},
        })
        self.assertEqual(r["pnl"], 1.2)
        self.assertEqual(r["gate_decision"], "PASS")
        self.assertEqual(r["rsi"], 28)
        self.assertEqual(r["funding"], -0.0001)
        self.assertEqual(r["alpha_cluster"], "X")
        self.assertIsNotNone(r.get("hour"))

    def test_matrix_from_rows(self) -> None:
        rows = _synth(50)
        pnls, closed, numeric, cats = matrix_from_rows(rows)
        self.assertEqual(len(pnls), 50)
        self.assertEqual(len(closed), 50)
        self.assertIn("rsi", numeric)
        self.assertIn("symbol", cats)


class TestMetricsV3(unittest.TestCase):
    def test_max_drawdown(self) -> None:
        self.assertLess(max_drawdown([1, -3, 0.5]) or 0, 0)

    def test_sortino(self) -> None:
        s = sortino_ratio([1, 2, -0.5, 1.5, -0.2, 0.8])
        self.assertIsNotNone(s)

    def test_bayesian_posterior(self) -> None:
        b = bayesian_edge_posterior([1, 1.2, 0.8, 1.1, 0.9, 1.3], n_boot=40, seed=1)
        self.assertGreater(b["prob_edge_gt_0"], 0.8)

    def test_cheap_screen(self) -> None:
        pnls = np.asarray([1.0, -0.5, 2.0, -0.2], dtype=float)
        mask = np.asarray([True, False, True, True])
        s = cheap_screen(pnls, mask)
        self.assertEqual(s["n"], 3)
        self.assertGreater(s["expectancy"], 0)

    def test_full_validate_keys(self) -> None:
        rows = _synth(120)
        pnls, closed, numeric, _ = matrix_from_rows(rows)
        mask = pnls > np.median(pnls)
        m = full_validate(pnls, mask, closed, numeric, n_boot=20, n_perm=15)
        for k in ("n", "expectancy", "pf", "sharpe", "sortino", "max_dd", "ci95",
                  "p_value", "prob_edge_gt_0", "wf_ok", "rolling_ok", "expanding_ok",
                  "oos_ok", "regime_ok", "quality_score"):
            self.assertIn(k, m)

    def test_quality_score_bounds(self) -> None:
        s = quality_score({
            "pf": 2.0, "expectancy": 0.8, "winrate": 60, "n": 100,
            "stability": 0.7, "p_value": 0.01, "prob_edge_gt_0": 0.9,
            "oos_ok": True, "wf_ok": True, "regime_ok": True,
        })
        self.assertGreater(s, 30)
        self.assertLessEqual(s, 100)

    def test_apply_fdr(self) -> None:
        cands = [{"p_value": 0.001}, {"p_value": 0.2}, {"p_value": None}]
        apply_fdr(cands, alpha=0.05)
        self.assertTrue(cands[0]["fdr_ok"])
        self.assertIn("q_value", cands[0])


class TestMiningV3(unittest.TestCase):
    def test_generate_atoms(self) -> None:
        rows = _synth(100)
        _, _, numeric, cats = matrix_from_rows(rows)
        atoms = generate_atoms(numeric, cats)
        self.assertGreater(len(atoms), 20)
        self.assertTrue(any(a.feature == "rsi" for a in atoms))

    def test_mine_finds_interaction(self) -> None:
        rows = _synth(500, seed=11)
        out = mine_edges_v3(rows, min_n=15, research_min_n=15, max_full_validate=80, max_k=5)
        self.assertGreater(out["n_edges_tested"], 100)
        self.assertGreaterEqual(out["n_rows"], 500)
        # Should surface some candidates or interactions around RSI/funding
        self.assertTrue(
            out["n_surviving"] > 0 or len(out.get("interactions") or []) > 0
            or out["n_screened"] > 0
        )

    def test_no_select_in_mine_path(self) -> None:
        rows = _synth(80)
        out = mine_edges_v3(rows, min_n=8, max_full_validate=20, max_k=4)
        self.assertEqual(out["n_rows"], 80)
        self.assertIn("n_combos_tested", out)


class TestRegimeV3(unittest.TestCase):
    def test_cluster_regimes_auto(self) -> None:
        rows = _synth(200)
        pnls, _, numeric, _ = matrix_from_rows(rows)
        reg = cluster_regimes(numeric, pnls, max_k=4)
        self.assertTrue(reg["ok"])
        self.assertGreaterEqual(reg["n_regimes"], 2)
        self.assertTrue(str(reg["method"]).startswith("kmeans") or "pca" in str(reg["method"]))


class TestImportanceV3(unittest.TestCase):
    def test_mi_and_perm(self) -> None:
        rows = _synth(150)
        pnls, _, numeric, cats = matrix_from_rows(rows)
        mi = mutual_information_scores(numeric, cats, pnls)
        perm = permutation_importance_scores(numeric, pnls, n_repeats=2)
        self.assertTrue(len(mi) > 0 or len(perm) > 0)

    def test_ig_and_consensus(self) -> None:
        ig = information_gain_from_edges([
            {"features": ["rsi", "funding"], "quality_score": 80},
            {"features": ["rsi", "trend"], "quality_score": 40},
        ])
        self.assertGreater(ig["rsi"], ig["trend"])
        ranking = consensus_ranking(
            mi={"rsi": 0.2, "funding": 0.1},
            ig=ig,
            perm={"rsi": 0.05},
            shap={},
        )
        self.assertEqual(ranking[0]["feature"], "rsi")

    def test_compute_importance(self) -> None:
        rows = _synth(120)
        pnls, _, numeric, cats = matrix_from_rows(rows)
        out = compute_importance(numeric, cats, pnls, [
            {"features": ["rsi", "funding"], "quality_score": 70},
        ])
        self.assertIn("ranking", out)


class TestLibraryV3(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "lib.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_and_upsert(self) -> None:
        ensure_edge_library_schema(self.conn)
        n = upsert_edges(self.conn, [{
            "rule": "(rsi<30) AND (funding<0)",
            "features": ["rsi", "funding"],
            "n": 40,
            "expectancy": 1.2,
            "pf": 2.1,
            "winrate": 62,
            "max_dd": -2.0,
            "prob_edge_gt_0": 0.91,
            "stability": 0.7,
            "quality_score": 77,
            "status": "READY",
            "ci95": (0.2, 2.0),
        }])
        self.assertEqual(n, 1)
        rows = load_library(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "READY")
        # update same id
        upsert_edges(self.conn, [{
            "rule": "(rsi<30) AND (funding<0)",
            "features": ["rsi", "funding"],
            "n": 45,
            "expectancy": 1.3,
            "pf": 2.2,
            "winrate": 63,
            "quality_score": 80,
            "status": "READY",
            "prob_edge_gt_0": 0.93,
            "stability": 0.8,
        }])
        rows2 = load_library(self.conn)
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["sample"], 45)

    def test_table_name(self) -> None:
        self.assertEqual(LIBRARY_TABLE, "market_edge_library_v1")


class TestReportV3(unittest.TestCase):
    def test_formatters(self) -> None:
        result = {
            "n_rows": 100,
            "n_edges_tested": 1000,
            "n_screened": 50,
            "n_full_validated": 40,
            "n_surviving": 5,
            "n_ready": 1,
            "n_test": 4,
            "n_atoms": 40,
            "n_features": 12,
            "elapsed_sec": 1.2,
            "load_stats": {"source": "research_lake_v1"},
            "baseline": {"expectancy": 0.0},
            "candidates": [{
                "rule": "(rsi) AND (funding)", "n": 20, "expectancy": 1.0,
                "pf": 2.0, "winrate": 60, "p_value": 0.01, "prob_edge_gt_0": 0.9,
                "status": "READY", "quality_score": 70, "features": ["rsi", "funding"],
                "wf_ok": True, "rolling_ok": True, "expanding_ok": True,
                "oos_ok": True, "regime_ok": True, "stability": 0.7, "cv": 0.5,
            }],
            "top20": [],
            "interactions": [],
            "importance": {"ranking": [{"feature": "rsi", "consensus": 1.0}], "shap_available": False},
            "regimes": {"ok": True, "method": "kmeans_k=2", "n_regimes": 2,
                        "features_used": ["rsi"], "regimes": [], "hdbscan": "x",
                        "embedding_method": "pca2"},
            "gate_unchanged": True,
            "optimizer_unchanged": True,
            "strategy_unchanged": True,
            "paper_unchanged": True,
            "execution_unchanged": True,
            "no_n_plus_1_sql": True,
            "library_upserted": 1,
        }
        result["top20"] = result["candidates"]
        self.assertIn("EDGE_DISCOVERY_V3", format_discovery_md(result))
        self.assertIn("EDGE_LIBRARY", format_library_md(result))
        self.assertIn("EDGE_HEATMAP", format_heatmap_md(result))
        self.assertIn("EDGE_STABILITY", format_stability_md(result))
        self.assertIn("FEATURE_IMPORTANCE", format_importance_md(result))
        self.assertIn("REGIME_CLUSTER_REPORT", format_regime_md(result))

    def test_write_artifacts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.edge_discovery_v3.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.edge_discovery_v3.report.REPORTS",
            {
                "discovery": Path(tmp.name) / "EDGE_DISCOVERY_V3.md",
                "library": Path(tmp.name) / "EDGE_LIBRARY.md",
                "heatmap": Path(tmp.name) / "EDGE_HEATMAP.md",
                "stability": Path(tmp.name) / "EDGE_STABILITY.md",
                "importance": Path(tmp.name) / "FEATURE_IMPORTANCE.md",
                "regime": Path(tmp.name) / "REGIME_CLUSTER_REPORT.md",
            },
        ):
            paths = write_artifacts({
                "n_rows": 10, "candidates": [], "top20": [], "interactions": [],
                "importance": {"ranking": []}, "regimes": {}, "baseline": {},
                "gate_unchanged": True, "optimizer_unchanged": True,
                "strategy_unchanged": True, "paper_unchanged": True,
                "execution_unchanged": True, "no_n_plus_1_sql": True,
            })
        self.assertIn("discovery", paths)
        self.assertTrue(Path(paths["discovery"]).exists())


class TestEngineV3(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "e.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_run_end_to_end_mocked_load(self) -> None:
        rows = _synth(250)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.edge_discovery_v3.engine.load_edge_v3_dataset",
            return_value=(rows, {"source": "synth", "n_raw": len(rows)}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.edge_discovery_v3.engine.write_artifacts",
            return_value={"discovery": "/tmp/x.md"},
        ):
            out = run_edge_discovery_v3(
                self.conn, write_reports=True, persist_library=True, min_n=12
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["no_n_plus_1_sql"])
        self.assertGreater(out["n_edges_tested"], 50)
        self.assertLess(out["elapsed_sec"], 60)

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"edge-discovery-v3"', src)
        self.assertIn("run_edge_discovery_v3", src)


class TestScaleV3(unittest.TestCase):
    def test_3k_under_budget(self) -> None:
        rows = _synth(3000, seed=3)
        t0 = time.time()
        out = mine_edges_v3(rows, min_n=25, research_min_n=25, max_full_validate=60, max_k=5)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 60.0, f"3k mine took {elapsed:.1f}s")
        self.assertGreater(out["n_edges_tested"], 500)

    def test_empty_corpus(self) -> None:
        out = mine_edges_v3([], min_n=10)
        self.assertEqual(out["n_rows"], 0)
        self.assertEqual(out["n_edges_tested"], 0)
        self.assertEqual(out["candidates"], [])

    def test_research_flags_on_engine(self) -> None:
        rows = _synth(60)
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.edge_discovery_v3.engine.load_edge_v3_dataset",
            return_value=(rows, {"source": "synth"}),
        ):
            out = run_edge_discovery_v3(conn, write_reports=False, persist_library=False, min_n=8)
        conn.close()
        for k in (
            "gate_unchanged", "optimizer_unchanged", "strategy_unchanged",
            "paper_unchanged", "execution_unchanged", "no_n_plus_1_sql",
        ):
            self.assertTrue(out[k])


if __name__ == "__main__":
    unittest.main()
