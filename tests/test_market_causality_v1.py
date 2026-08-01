"""Comprehensive tests for Market Causality Engine V1 (35+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.market_causality_v1.attribution import (
    attribute_trade,
    normalize_contributions,
    portfolio_mean_contributions,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.clusters import (
    CLUSTERS,
    assign_causal_cluster,
    cluster_trades,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.counterfactual import (
    counterfactual_ablation,
    portfolio_counterfactuals,
    trade_counterfactuals,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    CAUSE_FEATURES,
    CAUSAL_TEMPLATE_EDGES,
    extract_cause_vector,
    merge_cause_signal,
    pre_entry_deltas_from_frames,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.graph import (
    build_causal_graph,
    graph_markdown,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.library import (
    LIBRARY_TABLE,
    ensure_causality_library_schema,
    load_causality,
    upsert_causality,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.stability import (
    bootstrap_stability,
    evaluate_stability,
    filter_stable_edges,
    permutation_stability,
    regime_stability,
    walk_forward_stability,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.report import (
    format_causality_report,
    format_clusters_md,
    format_counterfactual_md,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.engine import (
    run_market_causality_v1,
)


def _trade(i: int = 0, *, funding_driven: bool = False) -> dict:
    base = 1_700_000_000 + i * 3600
    return {
        "trade_id": i + 1,
        "id": i + 1,
        "symbol": "BTC" if i % 2 == 0 else "ETH",
        "direction": "LONG",
        "pnl": 1.2 if funding_driven or i % 3 == 0 else -0.5,
        "entry_ts": base,
        "opened_at": base,
        "closed_at": base + 600,
        "rsi": 28.0 if not funding_driven else 50.0,
        "ema20_distance": -1.0,
        "ema50_distance": -0.4,
        "macd": -0.2,
        "adx": 25.0,
        "atr_pct": 1.5 if funding_driven else 0.8,
        "volume": 2000.0,
        "oi_delta": 2.0 if funding_driven else 0.1,
        "funding": -0.001 if funding_driven else 0.00005,
        "funding_delta": -0.0002 if funding_driven else 0.0,
        "fear_greed": 25.0 if funding_driven else 55.0,
        "news_score": 0.8 if i % 7 == 0 else 0.1,
        "ai_score": 0.6 if i % 7 == 0 else 0.05,
        "pattern": "sweep" if funding_driven else "",
        "regime": "RISK_OFF" if funding_driven else "RANGE",
        "hour": float(i % 24),
        "gate_decision": "PASS",
    }


def _causal_row(i: int, **kwargs) -> dict:
    t = _trade(i, **kwargs)
    vec = extract_cause_vector(t)
    attr = attribute_trade(vec, pnl=float(t["pnl"]))
    return {
        **attr,
        "trade_id": t["trade_id"],
        "pnl": t["pnl"],
        "regime": t["regime"],
        "entry_ts": t["entry_ts"],
        "closed_at": t["closed_at"],
        "cause_vec": vec,
        "confidence": 0.7,
        "quality": 0.6,
    }


class TestFeatures(unittest.TestCase):
    def test_cause_features_cover_brief(self) -> None:
        for f in ("rsi", "ema", "macd", "adx", "atr", "volume", "oi", "funding",
                  "fear", "news", "ai", "pattern", "regime", "time_of_day", "symbol"):
            self.assertIn(f, CAUSE_FEATURES)

    def test_template_edges_directed(self) -> None:
        self.assertTrue(any(e[0] == "funding" and e[1] == "oi" for e in CAUSAL_TEMPLATE_EDGES))
        self.assertTrue(any(e[1] == "outcome" for e in CAUSAL_TEMPLATE_EDGES))

    def test_extract_cause_vector(self) -> None:
        v = extract_cause_vector(_trade(0, funding_driven=True))
        self.assertGreater(v["funding"], 0)
        self.assertEqual(set(v.keys()), set(CAUSE_FEATURES))

    def test_pre_entry_deltas_reject_future(self) -> None:
        frames = [
            {"offset_min": -10, "funding": 0.0, "oi": 1, "volume": 1, "atr": 1, "fear_greed": 40, "rsi": 40, "macd": 0, "adx": 10, "ema20": 1},
            {"offset_min": -1, "funding": 0.001, "oi": 2, "volume": 2, "atr": 2, "fear_greed": 50, "rsi": 45, "macd": 0.1, "adx": 12, "ema20": 1.1},
            {"offset_min": 5, "funding": 9.0, "oi": 99, "volume": 99, "atr": 99, "fear_greed": 99, "rsi": 99, "macd": 9, "adx": 99, "ema20": 9},
        ]
        d = pre_entry_deltas_from_frames(frames)
        self.assertIn("funding", d)
        # Future frame must not dominate — funding delta from 0->0.001 not 0->9
        self.assertLess(d["funding"], 5.0)

    def test_merge_cause_signal(self) -> None:
        base = extract_cause_vector(_trade(1))
        merged = merge_cause_signal(base, {"funding": 1.0})
        self.assertIn("funding", merged)


class TestAttribution(unittest.TestCase):
    def test_normalize_sums_100(self) -> None:
        c = normalize_contributions({"rsi": 2, "funding": 3, "oi": 5})
        self.assertAlmostEqual(sum(c.values()), 100.0, places=2)

    def test_normalize_empty_uniform(self) -> None:
        c = normalize_contributions({})
        self.assertAlmostEqual(sum(c.values()), 100.0, places=1)

    def test_attribute_trade_primary(self) -> None:
        vec = {k: 0.0 for k in CAUSE_FEATURES}
        vec["funding"] = 5.0
        vec["oi"] = 1.0
        a = attribute_trade(vec, pnl=1.0)
        self.assertEqual(a["primary_cause"], "funding")
        self.assertAlmostEqual(sum(a["contributions"].values()), 100.0, places=2)

    def test_portfolio_mean(self) -> None:
        rows = [_causal_row(i, funding_driven=True) for i in range(10)]
        m = portfolio_mean_contributions(rows)
        self.assertAlmostEqual(sum(m.values()), 100.0, places=0)


class TestGraph(unittest.TestCase):
    def test_build_graph_edges(self) -> None:
        rows = [_causal_row(i, funding_driven=(i % 2 == 0)) for i in range(40)]
        g = build_causal_graph(rows)
        self.assertGreater(g["n_edges"], 0)
        self.assertTrue(all("weight" in e for e in g["edges"]))

    def test_graph_markdown(self) -> None:
        g = build_causal_graph([_causal_row(i) for i in range(20)])
        md = "\n".join(graph_markdown(g))
        self.assertIn("Causal graph", md)


class TestCounterfactual(unittest.TestCase):
    def test_ablation(self) -> None:
        rows = [_causal_row(i, funding_driven=True) for i in range(30)]
        cf = counterfactual_ablation(rows, feature="funding", threshold_pct=5.0)
        self.assertIn("delta_ev", cf)
        self.assertEqual(cf["feature"], "funding")

    def test_trade_counterfactuals(self) -> None:
        row = _causal_row(0, funding_driven=True)
        cf = trade_counterfactuals(row)
        self.assertIn("funding", cf)
        self.assertIn("without_feature_pnl", cf["funding"])

    def test_portfolio_counterfactuals(self) -> None:
        rows = [_causal_row(i) for i in range(25)]
        out = portfolio_counterfactuals(rows)
        self.assertGreaterEqual(len(out), 5)


class TestStability(unittest.TestCase):
    def test_walk_forward(self) -> None:
        rows = [_causal_row(i, funding_driven=(i % 2 == 0)) for i in range(60)]
        wf = walk_forward_stability(rows, n_folds=3)
        self.assertIn("stability", wf)

    def test_bootstrap(self) -> None:
        rows = [_causal_row(i) for i in range(40)]
        b = bootstrap_stability(rows, n_boot=10, seed=1)
        self.assertIn("ci95", b)

    def test_permutation(self) -> None:
        rows = [_causal_row(i, funding_driven=True) for i in range(40)]
        p = permutation_stability(rows, n_perm=15, seed=2)
        self.assertIn("p_value", p)

    def test_regime(self) -> None:
        rows = [_causal_row(i, funding_driven=(i < 20)) for i in range(40)]
        r = regime_stability(rows)
        self.assertIn("stability", r)

    def test_evaluate_and_filter(self) -> None:
        rows = [_causal_row(i) for i in range(50)]
        stab = evaluate_stability(rows)
        g = build_causal_graph(rows)
        edges = filter_stable_edges(g, stab)
        self.assertIsInstance(edges, list)
        self.assertIn("stable", stab)


class TestClusters(unittest.TestCase):
    def test_cluster_names(self) -> None:
        self.assertIn("funding_driven", CLUSTERS)
        self.assertIn("noise", CLUSTERS)

    def test_assign_funding(self) -> None:
        row = _causal_row(0, funding_driven=True)
        row["primary_cause"] = "funding"
        row["contributions"] = {k: 1.0 for k in CAUSE_FEATURES}
        row["contributions"]["funding"] = 40.0
        self.assertEqual(assign_causal_cluster(row), "funding_driven")

    def test_cluster_trades(self) -> None:
        rows = [_causal_row(i, funding_driven=(i % 2 == 0)) for i in range(30)]
        out = cluster_trades(rows)
        self.assertGreater(sum(c["n"] for c in out["top_clusters"]), 0)


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "c.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_upsert(self) -> None:
        ensure_causality_library_schema(self.conn)
        self.assertEqual(LIBRARY_TABLE, "market_trade_causality_v1")
        n = upsert_causality(self.conn, [{
            "trade_id": 9,
            "primary_cause": "funding",
            "secondary_cause": "oi",
            "contributions": {"funding": 40},
            "counterfactual": {"funding": {"delta_pnl": -0.1}},
            "causal_cluster": "funding_driven",
            "confidence": 0.8,
            "quality": 0.7,
        }])
        self.assertEqual(n, 1)
        rows = load_causality(self.conn)
        self.assertEqual(rows[0]["primary_cause"], "funding")

    def test_skip_zero_id(self) -> None:
        self.assertEqual(upsert_causality(self.conn, [{"trade_id": 0}]), 0)


class TestReport(unittest.TestCase):
    def test_formatters(self) -> None:
        result = {
            "n_rows": 10, "n_causal": 10, "coverage_pct": 100, "elapsed_sec": 1.0,
            "load_stats": {"source": "x"}, "temporal_leakage_rejected": True,
            "dominant_causes": {"funding": 20.0},
            "graph": {"n_edges": 5, "top_edges": [{"source": "a", "target": "b", "weight": 0.2}]},
            "stability": {"stable": True, "n_checks_passed": 3, "walk_forward": {},
                          "bootstrap": {}, "permutation": {}, "regimes": {}},
            "top_clusters": [{"cluster": "noise", "n": 3, "share": 0.3, "mean_pnl": 0, "primary_mode": "rsi"}],
            "portfolio_counterfactuals": [{"feature": "funding", "delta_ev": -0.1, "delta_wr": -1, "delta_pf": None, "n_ablated": 2}],
            "counterfactual_examples": [{"trade_id": 1, "primary_cause": "funding", "counterfactual": {}}],
            "gate_unchanged": True, "paper_unchanged": True, "optimizer_unchanged": True,
            "execution_unchanged": True, "no_n_plus_1_sql": True,
        }
        self.assertIn("CAUSALITY_REPORT", format_causality_report(result))
        self.assertIn("CAUSE_CLUSTERS", format_clusters_md(result))
        self.assertIn("COUNTERFACTUAL", format_counterfactual_md(result))

    def test_write_artifacts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = {
            "n_rows": 1, "n_causal": 1, "coverage_pct": 100, "elapsed_sec": 0.1,
            "load_stats": {}, "temporal_leakage_rejected": True, "dominant_causes": {},
            "graph": {"n_edges": 0, "top_edges": []}, "stability": {"stable": False, "n_checks_passed": 0},
            "top_clusters": [], "portfolio_counterfactuals": [], "counterfactual_examples": [],
            "clusters": {}, "gate_unchanged": True, "paper_unchanged": True,
            "optimizer_unchanged": True, "execution_unchanged": True, "no_n_plus_1_sql": True,
        }
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.report.REPORT_MD",
            Path(tmp.name) / "CAUSALITY_REPORT.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.report.CLUSTERS_MD",
            Path(tmp.name) / "CAUSE_CLUSTERS.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.report.COUNTERFACTUAL_MD",
            Path(tmp.name) / "COUNTERFACTUAL_REPORT.md",
        ):
            paths = write_artifacts(result)
        self.assertTrue(Path(paths["report_md"]).exists())


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "e.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_run_end_to_end(self) -> None:
        trades = [_trade(i, funding_driven=(i % 3 == 0)) for i in range(80)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.engine.write_artifacts",
            return_value={"report_md": "/tmp/c.md"},
        ):
            out = run_market_causality_v1(self.conn, write_reports=True, persist_library=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_causal"], 80)
        self.assertTrue(out["temporal_leakage_rejected"])
        self.assertTrue(out["gate_unchanged"])
        self.assertGreater(out["n_causal_relations"] or 0, 0)
        self.assertGreater(out["library_upserted"], 0)

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"market-causality"', src)
        self.assertIn("run_market_causality_v1", src)

    def test_research_flags(self) -> None:
        trades = [_trade(i) for i in range(20)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ):
            out = run_market_causality_v1(self.conn, write_reports=False, persist_library=False)
        for k in ("gate_unchanged", "paper_unchanged", "optimizer_unchanged",
                  "strategy_unchanged", "execution_unchanged", "no_n_plus_1_sql"):
            self.assertTrue(out[k])


class TestScale(unittest.TestCase):
    def test_3k_under_budget(self) -> None:
        trades = [_trade(i, funding_driven=(i % 4 == 0)) for i in range(3000)]
        conn = sqlite3.connect(":memory:")
        t0 = time.time()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_causality_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ):
            # Speed up stability for scale test
            with mock.patch(
                "bot.research.market_events.signal_intelligence.market_causality_v1.engine.evaluate_stability",
                return_value={"stable": True, "n_checks_passed": 4, "walk_forward": {},
                              "bootstrap": {}, "permutation": {}, "regimes": {}},
            ):
                out = run_market_causality_v1(conn, write_reports=False, persist_library=False)
        elapsed = time.time() - t0
        conn.close()
        self.assertEqual(out["n_causal"], 3000)
        self.assertLess(elapsed, 60.0, f"3k causality took {elapsed:.1f}s")


class TestExtra(unittest.TestCase):
    def test_pre_entry_empty(self) -> None:
        self.assertEqual(pre_entry_deltas_from_frames([]), {})

    def test_ablation_unknown_feature(self) -> None:
        cf = counterfactual_ablation([_causal_row(0)], feature="not_a_feature")
        self.assertEqual(cf["delta_ev"], 0.0)

    def test_noise_cluster(self) -> None:
        row = {"primary_cause": "noise", "secondary_cause": "symbol",
               "contributions": {k: 100.0 / len(CAUSE_FEATURES) for k in CAUSE_FEATURES}}
        self.assertEqual(assign_causal_cluster(row), "noise")

    def test_contributions_keys(self) -> None:
        a = attribute_trade(extract_cause_vector(_trade(2)), pnl=0.5)
        self.assertEqual(set(a["contributions"].keys()), set(CAUSE_FEATURES))

    def test_outcome_edges_present_when_signal(self) -> None:
        rows = [_causal_row(i, funding_driven=True) for i in range(40)]
        g = build_causal_graph(rows)
        targets = {e["target"] for e in g["edges"]}
        self.assertTrue(len(targets) >= 1)


if __name__ == "__main__":
    unittest.main()
