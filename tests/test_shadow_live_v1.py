"""Comprehensive tests for Shadow Live Evaluation V1 (45+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.shadow_live_v1.actions import (
    action_hit,
    pick_winner,
    production_action_from_trade,
    realized_ev_for_action,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.engine import (
    run_shadow_live_v1,
    shadow_decide_one,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.library import (
    LIBRARY_TABLE,
    ensure_shadow_library_schema,
    load_shadow_decisions,
    upsert_shadow_decisions,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.metrics import (
    CONFIDENCE_LEVELS,
    ROLLING_WINDOWS,
    calibration_summary,
    confidence_curve,
    evaluate_row,
    promotion_recommendation,
    rolling_statistics,
    slice_metrics,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.report import (
    format_promotion,
    format_scorecard,
    format_shadow_report,
    write_artifacts,
)


def _trade(i: int = 0, *, bullish: bool = True, gate: str = "PASS") -> dict:
    return {
        "trade_id": i + 1,
        "id": i + 1,
        "symbol": "BTC",
        "direction": "LONG" if bullish else "SHORT",
        "pnl": 1.5 if bullish else -1.2,
        "status": "CLOSED",
        "gate_decision": gate,
        "rsi": 28.0 if bullish else 72.0,
        "funding": -0.0003 if bullish else 0.0003,
        "atr_pct": 1.2,
        "oi_delta": 1.0,
        "fear_greed": 35.0,
        "regime": "RISK_OFF" if bullish else "RISK_ON",
        "closed_at": 1_700_000_000 + i,
        "alpha_labels": {},
    }


class TestActions(unittest.TestCase):
    def test_production_pass_long(self) -> None:
        self.assertEqual(production_action_from_trade(_trade(0, bullish=True)), "BUY")

    def test_production_pass_short(self) -> None:
        self.assertEqual(production_action_from_trade(_trade(0, bullish=False)), "SELL")

    def test_production_reject(self) -> None:
        t = _trade(0, gate="REJECT_LIQUIDITY")
        self.assertEqual(production_action_from_trade(t), "SKIP")

    def test_production_insufficient(self) -> None:
        t = _trade(0, gate="INSUFFICIENT_HISTORY")
        self.assertEqual(production_action_from_trade(t), "SKIP")

    def test_production_closed_no_gate(self) -> None:
        t = _trade(0)
        t["gate_decision"] = ""
        self.assertEqual(production_action_from_trade(t), "BUY")

    def test_action_hit_buy(self) -> None:
        self.assertTrue(action_hit("BUY", 1.0))
        self.assertFalse(action_hit("BUY", -1.0))

    def test_action_hit_sell(self) -> None:
        self.assertTrue(action_hit("SELL", -1.0))
        self.assertFalse(action_hit("SELL", 1.0))

    def test_action_hit_skip(self) -> None:
        self.assertTrue(action_hit("SKIP", -1.0))
        self.assertFalse(action_hit("SKIP", 1.0))

    def test_realized_ev(self) -> None:
        self.assertEqual(realized_ev_for_action("BUY", 2.0), 2.0)
        self.assertEqual(realized_ev_for_action("SELL", -2.0), 2.0)
        self.assertEqual(realized_ev_for_action("SKIP", 5.0), 0.0)

    def test_pick_winner_brain(self) -> None:
        # Production BUY on loss, brain SKIP → brain better EV
        w = pick_winner(production_action="BUY", brain_action="SKIP", pnl=-2.0)
        self.assertEqual(w, "BRAIN")

    def test_pick_winner_production(self) -> None:
        w = pick_winner(production_action="BUY", brain_action="SKIP", pnl=2.0)
        self.assertEqual(w, "PRODUCTION")

    def test_pick_winner_pending(self) -> None:
        self.assertEqual(
            pick_winner(production_action="BUY", brain_action="BUY", pnl=None),
            "PENDING",
        )


class TestMetrics(unittest.TestCase):
    def _rows(self, n: int = 60) -> list[dict]:
        rows = []
        for i in range(n):
            win = i % 2 == 0
            rows.append(evaluate_row({
                "trade_id": i + 1,
                "ts": 1_700_000_000 + i,
                "symbol": "BTC",
                "production_action": "BUY",
                "brain_action": "BUY" if win else "SKIP",
                "brain_confidence": 0.7 if win else 0.4,
                "brain_probability": 0.65,
                "expected_ev": 1.0,
                "expected_pf": 1.5,
                "pnl": 1.0 if win else -1.0,
            }))
        return rows

    def test_evaluate_row(self) -> None:
        r = evaluate_row({
            "production_action": "BUY",
            "brain_action": "SKIP",
            "pnl": -1.0,
        })
        self.assertTrue(r["evaluated"])
        self.assertEqual(r["winner"], "BRAIN")

    def test_slice_metrics(self) -> None:
        m = slice_metrics(self._rows(40))
        self.assertEqual(m["n"], 40)
        self.assertIsNotNone(m["brain_hit_rate"])
        self.assertIsNotNone(m["delta_ev"])

    def test_rolling_windows(self) -> None:
        r = rolling_statistics(self._rows(120))
        for w in ROLLING_WINDOWS:
            self.assertIn(f"last_{w}", r)
        self.assertIn("all", r)

    def test_confidence_levels(self) -> None:
        self.assertIn(0.55, CONFIDENCE_LEVELS)
        self.assertIn(0.95, CONFIDENCE_LEVELS)

    def test_confidence_curve(self) -> None:
        curve = confidence_curve(self._rows(80))
        self.assertEqual(len(curve), len(CONFIDENCE_LEVELS))
        self.assertIn("actual_wr", curve[0])

    def test_calibration_summary(self) -> None:
        cal = calibration_summary(confidence_curve(self._rows(80)))
        self.assertIn("reliability_score", cal)

    def test_promotion_not_ready_small_n(self) -> None:
        rolling = rolling_statistics(self._rows(10))
        cal = calibration_summary(confidence_curve(self._rows(10)))
        p = promotion_recommendation(rolling, cal)
        self.assertEqual(p["status"], "NOT_READY")
        self.assertFalse(p["auto_promotion"])
        self.assertFalse(p["ready_for_paper_ab"])

    def test_promotion_never_auto(self) -> None:
        rows = []
        for i in range(600):
            rows.append(evaluate_row({
                "ts": i,
                "production_action": "BUY",
                "brain_action": "BUY",
                "brain_confidence": 0.8,
                "pnl": 1.0,
            }))
        rolling = rolling_statistics(rows)
        curve = confidence_curve(rows)
        # Force high reliability
        for c in curve:
            c["reliable"] = True
            c["n"] = 50
            c["actual_wr"] = c["confidence"]
        cal = calibration_summary(curve)
        p = promotion_recommendation(rolling, cal)
        self.assertFalse(p["auto_promotion"])
        self.assertIn(p["status"], (
            "PROMISING", "BEATS_PRODUCTION", "READY_FOR_PAPER_AB", "NOT_READY"
        ))


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "s.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_name(self) -> None:
        self.assertEqual(LIBRARY_TABLE, "market_shadow_decisions_v1")

    def test_upsert_and_load(self) -> None:
        ensure_shadow_library_schema(self.conn)
        n = upsert_shadow_decisions(self.conn, [{
            "trade_id": 7,
            "candidate_id": 7,
            "ts": 123,
            "symbol": "BTC",
            "production_action": "BUY",
            "brain_action": "SELL",
            "brain_probability": 0.4,
            "brain_confidence": 0.6,
            "expected_ev": -0.5,
            "expected_pf": 0.8,
            "explanation": "conflict",
            "conflict": "HIGH",
            "conflict_score": 0.9,
            "pnl": -1.0,
            "evaluated": True,
            "production_hit": 0,
            "brain_hit": 1,
            "winner": "BRAIN",
        }])
        self.assertEqual(n, 1)
        rows = load_shadow_decisions(self.conn)
        self.assertEqual(rows[0]["brain_action"], "SELL")
        self.assertEqual(rows[0]["winner"], "BRAIN")

    def test_upsert_idempotent(self) -> None:
        row = {
            "trade_id": 3, "ts": 1, "symbol": "ETH",
            "production_action": "BUY", "brain_action": "BUY",
            "brain_probability": 0.7, "brain_confidence": 0.7,
            "expected_ev": 1, "expected_pf": 1.2, "explanation": "x",
            "conflict": "LOW", "pnl": 1.0, "evaluated": True,
            "production_hit": 1, "brain_hit": 1, "winner": "TIE",
        }
        upsert_shadow_decisions(self.conn, [row])
        row["brain_action"] = "SKIP"
        upsert_shadow_decisions(self.conn, [row])
        self.assertEqual(load_shadow_decisions(self.conn)[0]["brain_action"], "SKIP")


class TestReport(unittest.TestCase):
    def _result(self) -> dict:
        rows = []
        for i in range(30):
            rows.append(evaluate_row({
                "trade_id": i, "ts": i, "symbol": "BTC",
                "production_action": "BUY",
                "brain_action": "BUY" if i % 3 else "SKIP",
                "brain_confidence": 0.6, "pnl": 1.0 if i % 2 else -1.0,
            }))
        rolling = rolling_statistics(rows)
        curve = confidence_curve(rows)
        cal = calibration_summary(curve)
        promo = promotion_recommendation(rolling, cal)
        return {
            "n_candidates": 30, "n_decisions": 30, "n_evaluated": 30,
            "elapsed_sec": 0.5, "mean_decision_ms": 2.0, "p95_decision_ms": 5.0,
            "load_stats": {"source": "test"},
            "rolling": rolling, "confidence_curve": curve, "calibration": cal,
            "promotion": promo,
            "scorecard": {
                "production_vs_brain": rolling["all"],
                "rolling": {k: rolling[k] for k in rolling if k.startswith("last_")},
                "calibration": cal,
                "runtime": {"mean_decision_ms": 2.0, "within_budget": True},
            },
            "no_execution": True, "gate_unchanged": True, "strategy_unchanged": True,
            "paper_unchanged": True, "execution_unchanged": True,
            "optimizer_unchanged": True, "research_only": True,
        }

    def test_format_shadow(self) -> None:
        self.assertIn("SHADOW_LIVE_REPORT", format_shadow_report(self._result()))

    def test_format_scorecard(self) -> None:
        self.assertIn("BRAIN_SCORECARD", format_scorecard(self._result()))

    def test_format_promotion(self) -> None:
        md = format_promotion(self._result())
        self.assertIn("PROMOTION_STATUS", md)
        self.assertIn("No automatic promotion", md)

    def test_write_artifacts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.report.REPORT_MD",
            Path(tmp.name) / "SHADOW_LIVE_REPORT.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.report.SCORECARD_MD",
            Path(tmp.name) / "BRAIN_SCORECARD.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.report.PROMOTION_MD",
            Path(tmp.name) / "PROMOTION_STATUS.md",
        ):
            paths = write_artifacts(self._result())
        self.assertTrue(Path(paths["report_md"]).exists())
        self.assertTrue(Path(paths["scorecard_md"]).exists())
        self.assertTrue(Path(paths["promotion_md"]).exists())


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "e.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_shadow_decide_one_fast(self) -> None:
        row = shadow_decide_one(_trade(0), {
            "edges": [], "replays": {}, "causality": {},
            "optimizer_state": {}, "experiments": [],
        })
        self.assertIn(row["production_action"], ("BUY", "SELL", "SKIP", "HOLD"))
        self.assertIn(row["brain_action"], ("BUY", "SELL", "HOLD", "NO_TRADE"))
        self.assertLess(row["decision_ms"], 1000.0)
        self.assertTrue(row["no_execution"])

    def test_run_end_to_end(self) -> None:
        trades = [_trade(i, bullish=(i % 2 == 0)) for i in range(40)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._load_candidates",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._preload_brain_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine.write_artifacts",
            return_value={"report_md": "/tmp/s.md"},
        ):
            out = run_shadow_live_v1(self.conn, write_reports=True, persist_library=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_decisions"], 40)
        self.assertTrue(out["no_execution"])
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["optimizer_unchanged"])
        self.assertIn("promotion", out)
        self.assertIn("confidence_curve", out)
        self.assertLess(out["mean_decision_ms"], 1000.0)
        self.assertGreater(out["library_upserted"], 0)

    def test_research_flags(self) -> None:
        trades = [_trade(i) for i in range(12)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._load_candidates",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._preload_brain_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ):
            out = run_shadow_live_v1(self.conn, write_reports=False, persist_library=False)
        for k in (
            "research_only", "no_execution", "gate_unchanged", "strategy_unchanged",
            "paper_unchanged", "execution_unchanged", "optimizer_unchanged",
        ):
            self.assertTrue(out[k])

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"market-shadow-live"', src)
        self.assertIn("run_shadow_live_v1", src)

    def test_package_exports(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_live_v1 as pkg
        self.assertTrue(hasattr(pkg, "run_shadow_live_v1"))
        self.assertEqual(pkg.LIBRARY_TABLE, "market_shadow_decisions_v1")


class TestScale(unittest.TestCase):
    def test_200_decisions_under_budget(self) -> None:
        trades = [_trade(i, bullish=(i % 3 != 0)) for i in range(200)]
        conn = sqlite3.connect(":memory:")
        t0 = time.time()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._load_candidates",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._preload_brain_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ):
            out = run_shadow_live_v1(conn, write_reports=False, persist_library=False)
        elapsed = time.time() - t0
        conn.close()
        self.assertEqual(out["n_decisions"], 200)
        self.assertLess(out["mean_decision_ms"], 1000.0)
        self.assertLess(elapsed, 30.0)


class TestExtra(unittest.TestCase):
    def test_evaluate_pending(self) -> None:
        r = evaluate_row({"production_action": "BUY", "brain_action": "BUY", "pnl": None})
        self.assertEqual(r["winner"], "PENDING")
        self.assertFalse(r["evaluated"])

    def test_slice_empty(self) -> None:
        m = slice_metrics([])
        self.assertEqual(m["n"], 0)

    def test_allowed_gate(self) -> None:
        t = _trade(0, gate="ALLOWED")
        self.assertEqual(production_action_from_trade(t), "BUY")

    def test_candidate_rejected(self) -> None:
        t = {"direction": "LONG", "candidate_state": "REJECTED", "gate_decision": ""}
        self.assertEqual(production_action_from_trade(t), "SKIP")

    def test_candidate_accepted(self) -> None:
        t = {"direction": "SHORT", "candidate_state": "ACCEPTED", "gate_decision": ""}
        self.assertEqual(production_action_from_trade(t), "SELL")

    def test_false_positive_count(self) -> None:
        rows = [
            evaluate_row({"production_action": "BUY", "brain_action": "BUY", "pnl": -1.0, "ts": 1}),
            evaluate_row({"production_action": "BUY", "brain_action": "SKIP", "pnl": 1.0, "ts": 2}),
        ]
        m = slice_metrics(rows)
        self.assertGreaterEqual(m["false_positives"], 1)
        self.assertGreaterEqual(m["false_negatives"], 1)

    def test_tie_winner(self) -> None:
        w = pick_winner(production_action="BUY", brain_action="BUY", pnl=1.0)
        self.assertEqual(w, "TIE")

    def test_no_trade_brain_hit(self) -> None:
        self.assertTrue(action_hit("NO_TRADE", -0.5))

    def test_hold_brain_hit(self) -> None:
        self.assertTrue(action_hit("HOLD", 0.0))

    def test_confidence_curve_empty(self) -> None:
        self.assertEqual(len(confidence_curve([])), len(CONFIDENCE_LEVELS))

    def test_calibration_empty(self) -> None:
        cal = calibration_summary([])
        self.assertEqual(cal["reliability_score"], 0.0)

    def test_library_load_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_shadow_library_schema(conn)
        self.assertEqual(load_shadow_decisions(conn), [])
        conn.close()

    def test_scorecard_keys_in_engine(self) -> None:
        trades = [_trade(i) for i in range(8)]
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._load_candidates",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.shadow_live_v1.engine._preload_brain_context",
            return_value={"edges": [], "replays": {}, "causality": {}, "optimizer_state": {}, "experiments": []},
        ):
            out = run_shadow_live_v1(conn, write_reports=False, persist_library=False)
        conn.close()
        self.assertIn("runtime", out["scorecard"])
        self.assertIn("promotion", out["scorecard"])


if __name__ == "__main__":
    unittest.main()
