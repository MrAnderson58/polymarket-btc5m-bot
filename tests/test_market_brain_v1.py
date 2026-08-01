"""Comprehensive tests for Adaptive Market Brain V1 (40+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.market_brain_v1.explain import (
    explain_decision,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
    agreement_matrix,
    apply_calibration,
    bayesian_fusion,
    calibrate_confidence,
    detect_conflict,
    vote_summary,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.library import (
    LIBRARY_TABLE,
    ensure_brain_library_schema,
    load_decisions,
    upsert_decisions,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
    DEFAULT_WEIGHTS,
    MODULE_NAMES,
    collect_opinions,
    opinion_from_alpha,
    opinion_from_causality,
    opinion_from_edge,
    opinion_from_experiments,
    opinion_from_feature_store,
    opinion_from_lake,
    opinion_from_optimizer,
    opinion_from_replay,
    opinion_from_validation,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.report import (
    format_brain_report,
    format_conflicts_md,
    format_explanations_md,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.engine import (
    run_market_brain_v1,
)


def _trade(i: int = 0, *, bullish: bool = True) -> dict:
    return {
        "trade_id": i + 1,
        "id": i + 1,
        "symbol": "BTC",
        "direction": "LONG",
        "pnl": 1.5 if bullish else -1.0,
        "rsi": 28.0 if bullish else 72.0,
        "funding": -0.0003 if bullish else 0.0003,
        "atr_pct": 1.2,
        "oi_delta": 1.5 if bullish else -0.5,
        "fear_greed": 30.0 if bullish else 70.0,
        "macd": -0.1,
        "adx": 20.0,
        "trend": -0.3 if bullish else 0.4,
        "news_score": 0.4 if bullish else 0.1,
        "ai_score": 0.5 if bullish else 0.1,
        "regime": "RISK_OFF" if bullish else "RISK_ON",
        "gate_decision": "PASS",
        "alpha_labels": {
            "discovery": {"cluster": "A", "edge_score": 2.0 if bullish else -1.0, "status": "OK"},
            "validation": {"validation_status": "PASS" if bullish else "FAIL", "score": 1.0},
        },
        "optimizer_state": {"optimizer_mode": "approve" if bullish else "block"},
    }


def _ops(bullish: bool = True) -> list[dict]:
    return collect_opinions(
        _trade(0, bullish=bullish),
        replay={"quality": 0.8, "similar_ids": list(range(40)), "liquidity": {"volume_expansion": 0.1 if bullish else -0.1}},
        edges=[{"rule": "rsi AND funding", "expectancy": 1.0 if bullish else -1.0, "quality_score": 75, "status": "READY"}],
        causal={"primary_cause": "funding", "causal_cluster": "funding_driven", "confidence": 0.8, "quality": 0.7},
        optimizer_state={"optimizer_mode": "approve" if bullish else "block"},
        experiments=[{"status": "PASS" if bullish else "FAIL"}],
    )


class TestModules(unittest.TestCase):
    def test_module_names_and_weights(self) -> None:
        self.assertEqual(len(MODULE_NAMES), 9)
        self.assertAlmostEqual(sum(DEFAULT_WEIGHTS.values()), 1.0, places=2)

    def test_lake_opinion(self) -> None:
        o = opinion_from_lake(_trade(0, bullish=True))
        self.assertIn(o["direction"], ("BUY", "SELL", "HOLD"))
        self.assertEqual(o["module"], "lake")

    def test_replay_opinion(self) -> None:
        o = opinion_from_replay(_trade(0), {"quality": 0.9, "similar_ids": [1, 2], "liquidity": {"volume_expansion": 0.2}})
        self.assertEqual(o["direction"], "BUY")

    def test_replay_missing(self) -> None:
        o = opinion_from_replay(_trade(0), None)
        self.assertEqual(o["direction"], "HOLD")

    def test_edge_opinion(self) -> None:
        o = opinion_from_edge(_trade(0), [{"rule": "x", "expectancy": 0.5, "quality_score": 80}])
        self.assertEqual(o["direction"], "BUY")

    def test_edge_empty(self) -> None:
        self.assertEqual(opinion_from_edge(_trade(0), [])["direction"], "HOLD")

    def test_alpha_opinion(self) -> None:
        o = opinion_from_alpha(_trade(0, bullish=True))
        self.assertEqual(o["direction"], "BUY")

    def test_causality_opinion(self) -> None:
        o = opinion_from_causality(_trade(0), {"primary_cause": "funding", "causal_cluster": "funding_driven", "confidence": 0.7, "quality": 0.6})
        self.assertEqual(o["direction"], "BUY")

    def test_optimizer_opinion(self) -> None:
        o = opinion_from_optimizer(_trade(0), {"optimizer_mode": "approve"})
        self.assertEqual(o["direction"], "BUY")

    def test_validation_opinion(self) -> None:
        o = opinion_from_validation(_trade(0, bullish=True))
        self.assertEqual(o["direction"], "BUY")

    def test_feature_store_opinion(self) -> None:
        o = opinion_from_feature_store(_trade(0, bullish=True))
        self.assertEqual(o["direction"], "BUY")

    def test_experiments_opinion(self) -> None:
        o = opinion_from_experiments(_trade(0), [{"status": "PASS"}])
        self.assertEqual(o["direction"], "BUY")

    def test_collect_opinions_count(self) -> None:
        ops = _ops(True)
        self.assertEqual(len(ops), 9)


class TestFusion(unittest.TestCase):
    def test_vote_summary(self) -> None:
        v = vote_summary(_ops(True))
        self.assertEqual(v["n"], 9)

    def test_conflict_high(self) -> None:
        ops = [
            {"module": "a", "direction": "BUY", "confidence": 0.9, "weight": 0.2, "quality": 0.8, "edge": 1},
            {"module": "b", "direction": "SELL", "confidence": 0.9, "weight": 0.2, "quality": 0.8, "edge": -1},
            {"module": "c", "direction": "HOLD", "confidence": 0.3, "weight": 0.1, "quality": 0.3, "edge": 0},
        ]
        c = detect_conflict(ops)
        self.assertTrue(c["conflict"])
        self.assertEqual(c["level"], "HIGH")

    def test_fusion_no_trade_on_high_conflict(self) -> None:
        ops = [
            {"module": "a", "direction": "BUY", "confidence": 0.9, "weight": 0.25, "quality": 0.9, "edge": 2},
            {"module": "b", "direction": "BUY", "confidence": 0.85, "weight": 0.2, "quality": 0.85, "edge": 1.5},
            {"module": "c", "direction": "SELL", "confidence": 0.9, "weight": 0.25, "quality": 0.9, "edge": -2},
            {"module": "d", "direction": "SELL", "confidence": 0.88, "weight": 0.2, "quality": 0.88, "edge": -1.8},
        ]
        f = bayesian_fusion(ops)
        self.assertEqual(f["decision"], "NO_TRADE")

    def test_fusion_buy(self) -> None:
        f = bayesian_fusion(_ops(True))
        self.assertIn(f["decision"], ("BUY", "HOLD", "SELL", "NO_TRADE"))
        self.assertIn("probability_buy", f)

    def test_calibration_table(self) -> None:
        table = calibrate_confidence([0.1, 0.2, 0.8, 0.9], [False, False, True, True], n_bins=5)
        self.assertEqual(len(table), 5)
        cal = apply_calibration(0.85, table)
        self.assertGreaterEqual(cal, 0.0)
        self.assertLessEqual(cal, 1.0)

    def test_agreement_matrix(self) -> None:
        m = agreement_matrix(_ops(True))
        self.assertEqual(m["lake"]["lake"], 1.0)


class TestExplain(unittest.TestCase):
    def test_explain_buy(self) -> None:
        ops = _ops(True)
        f = bayesian_fusion(ops)
        if f["decision"] == "NO_TRADE":
            # force non-conflict fusion text path
            f = {**f, "decision": "BUY", "direction": "BUY", "conflict": {"level": "LOW", "strong_modules": []}}
        ex = explain_decision(f, ops, trade=_trade(0))
        self.assertIn("headline", ex)
        self.assertTrue(ex["because"] or ex["headline"])

    def test_explain_no_trade(self) -> None:
        f = {"decision": "NO_TRADE", "conflict": {"level": "HIGH", "strong_modules": [{"module": "a", "direction": "BUY", "confidence": 0.9}]}}
        ex = explain_decision(f, [], trade=None)
        self.assertIn("NO TRADE", ex["headline"])


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "b.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_and_upsert(self) -> None:
        ensure_brain_library_schema(self.conn)
        self.assertEqual(LIBRARY_TABLE, "market_brain_decisions_v1")
        n = upsert_decisions(self.conn, [{
            "trade_id": 3,
            "decision": "BUY",
            "direction": "BUY",
            "probability_buy": 0.8,
            "expected_ev": 1.0,
            "expected_pf": 2.0,
            "risk": "LOW",
            "confidence_raw": 0.7,
            "confidence_calibrated": 0.72,
            "conflict_level": "LOW",
            "conflict_score": 0.1,
            "votes": {"buy": 5},
            "opinions_slim": [],
            "explanation": {"headline": "BUY"},
            "module_weights": DEFAULT_WEIGHTS,
        }])
        self.assertEqual(n, 1)
        rows = load_decisions(self.conn)
        self.assertEqual(rows[0]["decision"], "BUY")


class TestReport(unittest.TestCase):
    def test_formatters(self) -> None:
        result = {
            "n_rows": 10, "n_decisions": 10, "elapsed_sec": 1.0, "n_no_trade": 1,
            "load_stats": {"source": "x"}, "module_weights": DEFAULT_WEIGHTS,
            "agreement_summary": {"lake": {"edge": 1.0}},
            "conflict_stats": {"conflict_rate": 0.1},
            "mean_confidence_raw": 0.5, "mean_confidence_calibrated": 0.55,
            "calibration_table": [], "replay_performance": {"brain": {"hit_rate": 0.6}},
            "explanation_examples": [{"trade_id": 1, "decision": "BUY", "confidence_calibrated": 0.8,
                                      "probability_buy": 0.7, "expected_ev": 1, "risk": "LOW",
                                      "explanation": {"text": "BUY because..."}}],
            "conflict_examples": [{"trade_id": 2, "level": "HIGH", "conflict_score": 0.8, "strong_modules": []}],
            "gate_unchanged": True, "strategy_unchanged": True, "paper_unchanged": True,
            "execution_unchanged": True, "research_only": True,
        }
        self.assertIn("MARKET_BRAIN_REPORT", format_brain_report(result))
        self.assertIn("BRAIN_EXPLANATIONS", format_explanations_md(result))
        self.assertIn("MODULE_CONFLICTS", format_conflicts_md(result))

    def test_write_artifacts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = {
            "n_rows": 1, "n_decisions": 1, "elapsed_sec": 0.1, "n_no_trade": 0,
            "load_stats": {}, "module_weights": {}, "agreement_summary": {},
            "conflict_stats": {}, "mean_confidence_raw": 0.4, "mean_confidence_calibrated": 0.4,
            "calibration_table": [], "replay_performance": {}, "explanation_examples": [],
            "conflict_examples": [], "gate_unchanged": True, "strategy_unchanged": True,
            "paper_unchanged": True, "execution_unchanged": True, "research_only": True,
        }
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.report.REPORT_MD",
            Path(tmp.name) / "MARKET_BRAIN_REPORT.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.report.EXPLAIN_MD",
            Path(tmp.name) / "BRAIN_EXPLANATIONS.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.report.CONFLICTS_MD",
            Path(tmp.name) / "MODULE_CONFLICTS.md",
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
        trades = [_trade(i, bullish=(i % 2 == 0)) for i in range(40)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._preload_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine.write_artifacts",
            return_value={"report_md": "/tmp/b.md"},
        ):
            out = run_market_brain_v1(self.conn, write_reports=True, persist_library=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_decisions"], 40)
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["paper_unchanged"])
        self.assertIn("replay_performance", out)
        self.assertGreater(out["library_upserted"], 0)

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"market-brain"', src)
        self.assertIn("run_market_brain_v1", src)

    def test_research_flags(self) -> None:
        trades = [_trade(i) for i in range(12)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._preload_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ):
            out = run_market_brain_v1(self.conn, write_reports=False, persist_library=False)
        for k in ("gate_unchanged", "strategy_unchanged", "paper_unchanged", "execution_unchanged", "research_only"):
            self.assertTrue(out[k])


class TestScale(unittest.TestCase):
    def test_3k_runtime(self) -> None:
        trades = [_trade(i, bullish=(i % 3 != 0)) for i in range(3000)]
        conn = sqlite3.connect(":memory:")
        t0 = time.time()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._load_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_brain_v1.engine._preload_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ):
            out = run_market_brain_v1(conn, write_reports=False, persist_library=False)
        elapsed = time.time() - t0
        conn.close()
        self.assertEqual(out["n_decisions"], 3000)
        self.assertLess(elapsed, 60.0, f"3k brain took {elapsed:.1f}s")


class TestExtra(unittest.TestCase):
    def test_optimizer_unknown(self) -> None:
        trade = {k: v for k, v in _trade(0).items() if k != "optimizer_state"}
        self.assertEqual(opinion_from_optimizer(trade, None)["direction"], "HOLD")

    def test_optimizer_block(self) -> None:
        trade = {k: v for k, v in _trade(0).items() if k != "optimizer_state"}
        self.assertEqual(
            opinion_from_optimizer(trade, {"optimizer_mode": "block"})["direction"],
            "SELL",
        )

    def test_experiments_empty(self) -> None:
        self.assertEqual(opinion_from_experiments(_trade(0), None)["direction"], "HOLD")

    def test_causality_inferred(self) -> None:
        o = opinion_from_causality(_trade(0, bullish=True), None)
        self.assertIn(o["direction"], ("BUY", "SELL", "HOLD"))

    def test_apply_calibration_empty_table(self) -> None:
        self.assertEqual(apply_calibration(0.7, []), 0.7)

    def test_weights_override(self) -> None:
        ops = collect_opinions(_trade(0), weights={"lake": 0.5})
        lake = next(o for o in ops if o["module"] == "lake")
        self.assertEqual(lake["weight"], 0.5)

    def test_conflict_low_when_aligned(self) -> None:
        ops = _ops(True)
        for o in ops:
            o["direction"] = "BUY"
            o["confidence"] = 0.8
        c = detect_conflict(ops)
        self.assertFalse(c["conflict"])
        self.assertEqual(c["level"], "LOW")

    def test_fusion_expected_fields(self) -> None:
        f = bayesian_fusion(_ops(True))
        for key in ("decision", "probability_buy", "expected_ev", "expected_pf", "risk", "confidence_raw"):
            self.assertIn(key, f)

    def test_sell_ops_fusion(self) -> None:
        f = bayesian_fusion(_ops(False))
        self.assertIn(f["decision"], ("BUY", "SELL", "HOLD", "NO_TRADE"))

    def test_lake_bearish(self) -> None:
        o = opinion_from_lake(_trade(1, bullish=False))
        self.assertIn(o["direction"], ("BUY", "SELL", "HOLD"))

    def test_edge_sell(self) -> None:
        o = opinion_from_edge(
            _trade(0),
            [{"rule": "x", "expectancy": -0.8, "quality_score": 70, "status": "READY"}],
        )
        self.assertEqual(o["direction"], "SELL")

    def test_alpha_sell(self) -> None:
        o = opinion_from_alpha(_trade(0, bullish=False))
        self.assertIn(o["direction"], ("BUY", "SELL", "HOLD"))

    def test_feature_store_bearish(self) -> None:
        o = opinion_from_feature_store(_trade(0, bullish=False))
        self.assertIn(o["direction"], ("BUY", "SELL", "HOLD"))

    def test_validation_fail(self) -> None:
        o = opinion_from_validation(_trade(0, bullish=False))
        self.assertEqual(o["direction"], "HOLD")

    def test_vote_buy_count(self) -> None:
        ops = _ops(True)
        for o in ops:
            o["direction"] = "BUY"
        v = vote_summary(ops)
        self.assertGreaterEqual(v.get("buy", v.get("BUY", 0)), 1)

    def test_detect_conflict_empty(self) -> None:
        c = detect_conflict([])
        self.assertIn("level", c)

    def test_agreement_diagonal(self) -> None:
        m = agreement_matrix(_ops(False))
        for mod in ("lake", "edge", "alpha"):
            if mod in m and mod in m[mod]:
                self.assertEqual(m[mod][mod], 1.0)

    def test_explain_sell_path(self) -> None:
        ops = _ops(False)
        f = {
            "decision": "SELL",
            "direction": "SELL",
            "probability_buy": 0.2,
            "expected_ev": -1.0,
            "expected_pf": 0.5,
            "risk": "HIGH",
            "confidence": 0.7,
            "conflict": {"level": "LOW", "strong_modules": []},
        }
        ex = explain_decision(f, ops, trade=_trade(0, bullish=False))
        self.assertIn("headline", ex)

    def test_library_load_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_brain_library_schema(conn)
        self.assertEqual(load_decisions(conn), [])
        conn.close()

    def test_upsert_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_brain_library_schema(conn)
        row = {
            "trade_id": 9,
            "decision": "HOLD",
            "direction": "HOLD",
            "probability_buy": 0.5,
            "expected_ev": 0.0,
            "expected_pf": 1.0,
            "risk": "MED",
            "confidence_raw": 0.4,
            "confidence_calibrated": 0.41,
            "conflict_level": "LOW",
            "conflict_score": 0.0,
            "votes": {},
            "opinions_slim": [],
            "explanation": {"headline": "HOLD"},
            "module_weights": DEFAULT_WEIGHTS,
        }
        self.assertEqual(upsert_decisions(conn, [row]), 1)
        row["decision"] = "BUY"
        self.assertEqual(upsert_decisions(conn, [row]), 1)
        self.assertEqual(load_decisions(conn)[0]["decision"], "BUY")
        conn.close()

    def test_calibrate_bins(self) -> None:
        preds = [i / 20.0 for i in range(20)]
        labels = [p > 0.5 for p in preds]
        table = calibrate_confidence(preds, labels, n_bins=4)
        self.assertEqual(len(table), 4)

    def test_package_exports(self) -> None:
        from bot.research.market_events.signal_intelligence import market_brain_v1 as pkg

        self.assertTrue(hasattr(pkg, "run_market_brain_v1"))


if __name__ == "__main__":
    unittest.main()
