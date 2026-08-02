"""Comprehensive tests for Edge Reality Audit V1 (55+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.edge_reality_v1.ablation import (
    brain_without_analysis,
    incremental_value,
    remove_one_analysis,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.attribution import (
    attribute_modules,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.engine import (
    run_edge_reality_audit_v1,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.library import (
    LIBRARY_TABLE,
    ensure_attribution_schema,
    load_attribution,
    upsert_attribution,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.metrics import (
    delta_metrics,
    information_gain,
    mutual_information_binary,
    precision_recall_f1,
    score_actions,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.ranking import (
    complexity_audit,
    grade_modules,
    pareto_analysis,
    simplification_gain,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.redundancy import (
    redundancy_matrix,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.report import (
    format_attribution_md,
    format_edge_reality_report,
    format_pareto_md,
    format_redundancy_md,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    AUDIT_MODULES,
    INCREMENTAL_ORDER,
    collect_module_signals,
    evolution_signal,
    production_signal,
    realized_ev,
    signal_correct,
)


def _actions_book(n: int = 40) -> tuple[dict[str, list[str]], list[float]]:
    pnls = [1.0 if i % 3 else -0.5 for i in range(n)]
    prod = ["BUY" if p > 0 else "SELL" for p in pnls]
    # Flip every 4th for noise modules
    noisy = [("SELL" if a == "BUY" else "BUY") if i % 4 == 0 else a for i, a in enumerate(prod)]
    skippy = ["SKIP" if i % 2 == 0 else prod[i] for i in range(n)]
    actions = {
        "production": prod,
        "replay": list(prod),
        "edge": noisy,
        "alpha": skippy,
        "optimizer": ["SKIP"] * n,
        "brain": list(prod),
        "causality": noisy,
        "evolution": skippy,
        "features": ["BUY"] * n,
        "validation": list(prod),
    }
    return actions, pnls


class TestSignals(unittest.TestCase):
    def test_audit_modules_count(self) -> None:
        self.assertEqual(len(AUDIT_MODULES), 9)

    def test_incremental_order(self) -> None:
        self.assertEqual(INCREMENTAL_ORDER[0], "replay")
        self.assertIn("brain", INCREMENTAL_ORDER)

    def test_production_pass_buy(self) -> None:
        s = production_signal({"gate_decision": "PASS", "direction": "LONG"})
        self.assertEqual(s["action"], "BUY")

    def test_production_reject(self) -> None:
        s = production_signal({"gate_decision": "REJECT", "direction": "LONG"})
        self.assertEqual(s["action"], "SKIP")

    def test_production_sell(self) -> None:
        s = production_signal({"gate_decision": "PASS", "direction": "SHORT"})
        self.assertEqual(s["action"], "SELL")

    def test_signal_correct_buy(self) -> None:
        self.assertTrue(signal_correct("BUY", 1.0))
        self.assertFalse(signal_correct("BUY", -1.0))

    def test_signal_correct_sell(self) -> None:
        self.assertTrue(signal_correct("SELL", -1.0))
        self.assertFalse(signal_correct("SELL", 1.0))

    def test_signal_correct_skip(self) -> None:
        self.assertTrue(signal_correct("SKIP", -1.0))
        self.assertTrue(signal_correct("SKIP", 0.0))

    def test_realized_ev(self) -> None:
        self.assertEqual(realized_ev("BUY", 2.0), 2.0)
        self.assertEqual(realized_ev("SELL", 2.0), -2.0)
        self.assertEqual(realized_ev("SKIP", 2.0), 0.0)

    def test_evolution_peak(self) -> None:
        evo = {"lake:BTC|LONG|RANGE": {"status": "PEAK", "score": 80, "confidence": 0.9}}
        s = evolution_signal(
            {"symbol": "BTC", "direction": "LONG", "regime": "RANGE"}, evo
        )
        self.assertEqual(s["action"], "BUY")

    def test_evolution_decay(self) -> None:
        evo = {"lake:BTC|LONG|RANGE": {"status": "DEAD", "score": 10, "confidence": 0.2}}
        s = evolution_signal(
            {"symbol": "BTC", "direction": "LONG", "regime": "RANGE"}, evo
        )
        self.assertEqual(s["action"], "SKIP")

    def test_evolution_miss(self) -> None:
        s = evolution_signal({"symbol": "ETH", "direction": "LONG"}, {})
        self.assertEqual(s["action"], "SKIP")

    def test_collect_module_signals_keys(self) -> None:
        trade = {
            "trade_id": 1,
            "gate_decision": "PASS",
            "direction": "LONG",
            "pnl": 1.0,
            "symbol": "BTC",
            "regime": "RANGE",
        }
        sigs = collect_module_signals(trade, {})
        for m in ("production", *AUDIT_MODULES):
            self.assertIn(m, sigs)
            self.assertIn(sigs[m]["action"], ("BUY", "SELL", "SKIP", "HOLD"))


class TestMetrics(unittest.TestCase):
    def test_score_actions_fields(self) -> None:
        m = score_actions(["BUY", "SELL", "SKIP"], [1.0, -1.0, -0.5])
        for k in ("ev", "pf", "wr", "mutual_information", "information_gain", "precision", "recall", "f1"):
            self.assertIn(k, m)

    def test_score_empty(self) -> None:
        m = score_actions([], [])
        self.assertEqual(m["n"], 0)

    def test_delta_metrics(self) -> None:
        a = score_actions(["BUY"] * 10, [1.0] * 10)
        b = score_actions(["SKIP"] * 10, [1.0] * 10)
        d = delta_metrics(a, b)
        self.assertIn("delta_ev", d)
        self.assertLess(d["delta_ev"] or 0, 0)

    def test_mi_perfect(self) -> None:
        truth = [True, False, True, False]
        pred = list(truth)
        self.assertGreater(mutual_information_binary(pred, truth), 0.5)

    def test_mi_independent(self) -> None:
        mi = mutual_information_binary([True, True, False, False], [True, False, True, False])
        self.assertLessEqual(mi, 0.1)

    def test_mi_empty(self) -> None:
        self.assertEqual(mutual_information_binary([], []), 0.0)

    def test_ig_positive(self) -> None:
        truth = [True, False, True, False, True, False]
        pred = list(truth)
        self.assertGreater(information_gain(pred, truth), 0.0)

    def test_ig_empty(self) -> None:
        self.assertEqual(information_gain([], []), 0.0)

    def test_prf1(self) -> None:
        r = precision_recall_f1([True, True, False, False], [True, False, True, False])
        self.assertIsNotNone(r["precision"])
        self.assertIsNotNone(r["f1"])

    def test_prf1_none(self) -> None:
        r = precision_recall_f1([False, False], [False, False])
        self.assertIsNone(r["precision"])


class TestAttribution(unittest.TestCase):
    def test_attribute_includes_production(self) -> None:
        actions, pnls = _actions_book(30)
        rows = attribute_modules(actions, pnls)
        mods = [r["module"] for r in rows]
        self.assertIn("production", mods)
        self.assertIn("replay", mods)

    def test_production_delta_zero(self) -> None:
        actions, pnls = _actions_book(20)
        rows = attribute_modules(actions, pnls)
        prod = next(r for r in rows if r["module"] == "production")
        self.assertEqual(prod["delta_ev"], 0.0)

    def test_vs_production_present(self) -> None:
        actions, pnls = _actions_book(20)
        rows = attribute_modules(actions, pnls)
        for r in rows:
            self.assertIn("vs_production", r)


class TestAblation(unittest.TestCase):
    def test_incremental_starts_production(self) -> None:
        actions, pnls = _actions_book(24)
        steps = incremental_value(actions, pnls)
        self.assertEqual(steps[0]["added"], None)
        self.assertEqual(steps[0]["modules"], ["production"])

    def test_incremental_adds_order(self) -> None:
        actions, pnls = _actions_book(24)
        steps = incremental_value(actions, pnls)
        added = [s["added"] for s in steps[1:]]
        self.assertEqual(added[0], "replay")

    def test_remove_one_sorted(self) -> None:
        actions, pnls = _actions_book(30)
        rows = remove_one_analysis(actions, pnls)
        self.assertTrue(len(rows) >= 5)
        hurts = [r["hurt_score"] for r in rows]
        self.assertEqual(hurts, sorted(hurts, reverse=True))

    def test_remove_one_no_production(self) -> None:
        actions, pnls = _actions_book(20)
        rows = remove_one_analysis(actions, pnls)
        self.assertNotIn("production", [r["removed"] for r in rows])

    def test_brain_without_runs(self) -> None:
        trades = [
            {
                "trade_id": i + 1,
                "gate_decision": "PASS",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "pnl": 1.0 if i % 3 else -0.5,
                "rsi": 40.0,
                "funding": -0.0001,
            }
            for i in range(12)
        ]
        pnls = [float(t["pnl"]) for t in trades]
        rows = brain_without_analysis(trades, {}, pnls)
        self.assertTrue(len(rows) >= 3)
        self.assertIn("label", rows[0])


class TestRedundancy(unittest.TestCase):
    def test_matrix_keys(self) -> None:
        actions, _ = _actions_book(20)
        m = redundancy_matrix(actions)
        for k in ("correlation", "mutual_information", "overlap", "redundancy", "top_redundant_pairs"):
            self.assertIn(k, m)

    def test_self_corr_one(self) -> None:
        actions, _ = _actions_book(16)
        m = redundancy_matrix(actions)
        self.assertEqual(m["correlation"]["replay"]["replay"], 1.0)

    def test_top_pairs(self) -> None:
        actions, _ = _actions_book(16)
        m = redundancy_matrix(actions)
        self.assertTrue(len(m["top_redundant_pairs"]) >= 1)


class TestRanking(unittest.TestCase):
    def test_complexity_rows(self) -> None:
        actions, pnls = _actions_book(20)
        attr = attribute_modules(actions, pnls)
        cx = complexity_audit(attr, module_runtime_ms={"replay": 100.0})
        self.assertEqual(len(cx), len(AUDIT_MODULES))
        self.assertIn("edge_per_1000_loc", cx[0])

    def test_grades(self) -> None:
        actions, pnls = _actions_book(40)
        attr = attribute_modules(actions, pnls)
        rem = remove_one_analysis(actions, pnls)
        inc = incremental_value(actions, pnls)
        cx = complexity_audit(attr)
        red = redundancy_matrix(actions)
        ranked = grade_modules(attr, rem, cx, incremental=inc, redundancy=red)
        self.assertTrue(all(r["grade"] in ("A+", "A", "B", "C", "D", "REMOVE") for r in ranked))

    def test_pareto(self) -> None:
        actions, pnls = _actions_book(40)
        attr = attribute_modules(actions, pnls)
        rem = remove_one_analysis(actions, pnls)
        inc = incremental_value(actions, pnls)
        cx = complexity_audit(attr)
        ranked = grade_modules(attr, rem, cx, incremental=inc)
        p = pareto_analysis(ranked)
        self.assertIn("pareto_modules", p)
        self.assertLessEqual(p["power_captured"], 1.0)

    def test_simplification(self) -> None:
        actions, pnls = _actions_book(40)
        attr = attribute_modules(actions, pnls)
        rem = remove_one_analysis(actions, pnls)
        inc = incremental_value(actions, pnls)
        cx = complexity_audit(attr)
        ranked = grade_modules(attr, rem, cx, incremental=inc)
        s = simplification_gain(ranked, rem)
        self.assertIn("modules_to_keep", s)
        self.assertIn("modules_to_remove", s)


class TestLibrary(unittest.TestCase):
    def test_schema_and_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            ensure_attribution_schema(conn)
            n = upsert_attribution(
                conn,
                [{
                    "module": "replay",
                    "n": 10,
                    "ev": 1.0,
                    "delta_ev": 0.5,
                    "mutual_information": 0.1,
                    "f1": 0.4,
                    "grade": "A",
                    "score": 12.0,
                    "keep": True,
                }],
                run_id="test-run",
            )
            self.assertEqual(n, 1)
            rows = load_attribution(conn, run_id="test-run")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["module"], "replay")
            # table exists
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            self.assertIn(LIBRARY_TABLE, names)
            conn.close()

    def test_upsert_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            conn = sqlite3.connect(str(db))
            upsert_attribution(conn, [{"module": "edge", "delta_ev": 1.0, "keep": True}], run_id="r1")
            upsert_attribution(conn, [{"module": "edge", "delta_ev": 2.0, "keep": False}], run_id="r1")
            rows = load_attribution(conn, run_id="r1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["delta_ev"], 2.0)
            conn.close()

    def test_load_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "e.db"))
            self.assertEqual(load_attribution(conn), [])
            conn.close()


class TestReport(unittest.TestCase):
    def _mini_result(self) -> dict:
        actions, pnls = _actions_book(20)
        attr = attribute_modules(actions, pnls)
        rem = remove_one_analysis(actions, pnls)
        cx = complexity_audit(attr)
        ranked = grade_modules(attr, rem, cx, incremental=incremental_value(actions, pnls), redundancy=redundancy_matrix(actions))
        return {
            "ok": True,
            "run_id": "t",
            "n_trades": 20,
            "elapsed_sec": 0.1,
            "load_stats": {"source": "test"},
            "attribution": attr,
            "incremental": incremental_value(actions, pnls),
            "remove_one": rem,
            "brain_without": [],
            "redundancy": redundancy_matrix(actions),
            "complexity": cx,
            "ranked": ranked,
            "pareto": pareto_analysis(ranked),
            "simplification": simplification_gain(ranked, rem),
            "research_only": True,
            "paper_unchanged": True,
            "execution_unchanged": True,
            "strategy_unchanged": True,
            "gate_unchanged": True,
            "optimizer_unchanged": True,
            "brain_unchanged": True,
        }

    def test_format_edge(self) -> None:
        md = format_edge_reality_report(self._mini_result())
        self.assertIn("EDGE_REALITY_REPORT", md)
        self.assertIn("Module attribution", md)

    def test_format_attr(self) -> None:
        self.assertIn("MODULE_ATTRIBUTION", format_attribution_md(self._mini_result()))

    def test_format_red(self) -> None:
        self.assertIn("MODULE_REDUNDANCY", format_redundancy_md(self._mini_result()))

    def test_format_pareto(self) -> None:
        self.assertIn("PARETO_REPORT", format_pareto_md(self._mini_result()))

    def test_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.BASE_DIR",
                base,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.OUT_DIR",
                base / "reports" / "research" / "edge_reality_v1",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.EDGE_REALITY_MD",
                base / "EDGE_REALITY_REPORT.md",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.ATTRIBUTION_MD",
                base / "MODULE_ATTRIBUTION.md",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.REDUNDANCY_MD",
                base / "MODULE_REDUNDANCY.md",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.report.PARETO_MD",
                base / "PARETO_REPORT.md",
            ):
                paths = write_artifacts(self._mini_result())
                self.assertTrue((base / "EDGE_REALITY_REPORT.md").exists())
                self.assertTrue((base / "MODULE_ATTRIBUTION.md").exists())
                self.assertTrue((base / "MODULE_REDUNDANCY.md").exists())
                self.assertTrue((base / "PARETO_REPORT.md").exists())
                self.assertIn("EDGE_REALITY_REPORT.md", paths)


class TestEngine(unittest.TestCase):
    def test_run_empty_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "empty.db"))
            out = run_edge_reality_audit_v1(
                conn, write_reports=False, persist_library=True, limit=10
            )
            self.assertFalse(out["ok"])
            self.assertEqual(out["n_trades"], 0)
            self.assertTrue(out["brain_unchanged"])
            conn.close()

    def test_run_with_mocked_trades(self) -> None:
        trades = [
            {
                "trade_id": i + 1,
                "gate_decision": "PASS",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "pnl": 1.0 if i % 3 else -0.8,
                "symbol": "BTC",
                "regime": "RANGE",
                "rsi": 35.0 + i,
                "funding": -0.0001,
            }
            for i in range(25)
        ]
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "m.db"))
            with mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.engine._load_trades",
                return_value=(trades, {"source": "mock"}),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.engine._preload_context",
                return_value={},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_reality_v1.engine.write_artifacts",
                return_value={},
            ):
                out = run_edge_reality_audit_v1(
                    conn, write_reports=True, persist_library=True, limit=25
                )
            self.assertTrue(out["ok"])
            self.assertEqual(out["n_trades"], 25)
            self.assertTrue(len(out["attribution"]) >= 5)
            self.assertTrue(len(out["incremental"]) >= 2)
            self.assertTrue(len(out["remove_one"]) >= 1)
            self.assertIn("pareto_modules", out["pareto"])
            rows = load_attribution(conn, run_id=out["run_id"])
            self.assertGreaterEqual(len(rows), 1)
            conn.close()

    def test_integrity_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "i.db"))
            out = run_edge_reality_audit_v1(conn, write_reports=False, persist_library=False)
            for k in (
                "research_only", "paper_unchanged", "execution_unchanged",
                "strategy_unchanged", "gate_unchanged", "optimizer_unchanged",
                "brain_unchanged",
            ):
                self.assertTrue(out[k])
            conn.close()


class TestCliRegistration(unittest.TestCase):
    def test_help_lists_command(self) -> None:
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("edge-reality-audit", r.stdout + r.stderr)


class TestExtraCoverage(unittest.TestCase):
    def test_score_sell_book(self) -> None:
        m = score_actions(["SELL", "SELL", "BUY"], [-1.0, -0.5, 1.0])
        self.assertGreater(m["ev"] or 0, 0)

    def test_delta_none_safe(self) -> None:
        d = delta_metrics({"ev": None, "pf": None}, {"ev": 1.0, "pf": 2.0})
        self.assertIsNone(d["delta_ev"])

    def test_redundant_identical(self) -> None:
        actions = {
            "replay": ["BUY"] * 10,
            "edge": ["BUY"] * 10,
            "alpha": ["SELL"] * 10,
        }
        m = redundancy_matrix(actions, modules=("replay", "edge", "alpha"))
        self.assertGreaterEqual(m["overlap"]["replay"]["edge"], 0.99)

    def test_pareto_empty_positive(self) -> None:
        ranked = [
            {"module": "a", "delta_ev": -1.0, "score": -2.0},
            {"module": "b", "delta_ev": -0.5, "score": -1.0},
        ]
        p = pareto_analysis(ranked)
        self.assertIn("pareto_modules", p)

    def test_grade_remove_path(self) -> None:
        attr = [
            {"module": "production", "ev": 0, "f1": 0.1, "mutual_information": 0, "vs_production": {"delta_ev": 0}},
            {
                "module": "optimizer",
                "ev": 0,
                "f1": None,
                "precision": None,
                "recall": 0.0,
                "mutual_information": 0.0,
                "vs_production": {"delta_ev": 5.0},
            },
        ]
        rem = [{"removed": "optimizer", "hurt_score": 0.0}]
        cx = [{"module": "optimizer", "edge_per_1000_loc": 0.0, "loc": 0}]
        # never-takes + zero marginal → REMOVE
        ranked = grade_modules(attr, rem, cx, incremental=[{"added": "optimizer", "delta_vs_prev": {"delta_ev": 0.0}}])
        self.assertEqual(ranked[0]["grade"], "REMOVE")

    def test_incremental_skips_missing(self) -> None:
        actions = {"production": ["BUY"] * 5, "replay": ["BUY"] * 5}
        steps = incremental_value(actions, [1.0] * 5)
        self.assertEqual(steps[-1]["added"], "replay")

    def test_evolution_growth_sell(self) -> None:
        evo = {"lake:BTC|SHORT|BEAR": {"status": "GROWTH", "score": 70, "confidence": 0.8}}
        s = evolution_signal(
            {"symbol": "BTC", "direction": "SHORT", "regime": "BEAR"}, evo
        )
        self.assertEqual(s["action"], "SELL")


if __name__ == "__main__":
    unittest.main()
