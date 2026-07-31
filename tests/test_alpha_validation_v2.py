"""Alpha Validation Engine V2 — rules, WF/rolling/OOS/MC, store (20+)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_validation_v2.engine import (
    run_alpha_validation_report,
    run_alpha_validation_v2,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.report import (
    format_validation_report,
    write_validation_artifacts,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.rules import (
    load_candidates,
    parse_atomic_id,
    parse_rule_id,
    rule_from_candidate,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.schema import (
    ensure_alpha_validation_schema,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.store import (
    finish_history_run,
    load_history,
    load_validations,
    new_run_id,
    start_history_run,
    upsert_validation,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.validation import (
    match_rows,
    monte_carlo_null,
    oos_replay,
    rolling_windows_validate,
    sort_chrono,
    stability_over_time,
    validate_candidate,
    walk_forward_validate,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_alpha_validation_schema(c)
    return c


def _stable_edge_rows(n: int = 120, seed: int = 3) -> list[dict]:
    """Plant RSI-low edge that holds in every chronological quarter."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        # ~35% of trades in rsi_low with strong positive PnL in all eras
        in_edge = (i % 3 == 0)
        rsi = float(rng.uniform(30, 44.9) if in_edge else rng.uniform(50, 80))
        atr_pct = float(rng.uniform(0.05, 0.14) if in_edge else rng.uniform(0.2, 0.8))
        stoch_k = float(rng.uniform(5, 40) if in_edge else rng.uniform(45, 95))
        pnl = float(rng.normal(1.2 if in_edge else -0.4, 0.25))
        rows.append({
            "id": i,
            "pnl": pnl,
            "rsi": rsi,
            "atr_pct": atr_pct,
            "stoch_k": stoch_k,
            "ema20_distance": float(rng.normal(-2 if in_edge else 0.5, 0.3)),
            "symbol": "BTC",
            "direction": "LONG",
            "closed_at": 1_700_000_000 + i * 300,
            "created_at": 1_700_000_000 + i * 300,
        })
    return rows


def _unstable_rows(n: int = 80, seed: int = 9) -> list[dict]:
    """Edge only in first half — must be rejected by independent windows."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        early = i < n // 2
        in_sig = i % 4 == 0
        rsi = float(rng.uniform(30, 44) if in_sig else rng.uniform(55, 75))
        if in_sig and early:
            pnl = float(rng.normal(1.5, 0.2))
        elif in_sig and not early:
            pnl = float(rng.normal(-1.2, 0.2))
        else:
            pnl = float(rng.normal(-0.1, 0.3))
        rows.append({
            "id": i,
            "pnl": pnl,
            "rsi": rsi,
            "atr_pct": 0.1,
            "stoch_k": 20.0,
            "closed_at": 1_700_000_000 + i * 300,
            "created_at": 1_700_000_000 + i * 300,
        })
    return rows


class TestRuleParsing(unittest.TestCase):
    def test_rsi_bin(self) -> None:
        feat, pred = parse_atomic_id("rsi_low")
        self.assertEqual(feat, "rsi")
        self.assertTrue(pred({"rsi": 40}))
        self.assertFalse(pred({"rsi": 50}))

    def test_le_negative_threshold(self) -> None:
        feat, pred = parse_atomic_id("ema20_distance_le_-1.879199")
        self.assertEqual(feat, "ema20_distance")
        self.assertTrue(pred({"ema20_distance": -2.0}))
        self.assertFalse(pred({"ema20_distance": -1.0}))

    def test_gt_and_ge(self) -> None:
        _, p_gt = parse_atomic_id("adx_gt_30.5")
        _, p_ge = parse_atomic_id("adx_ge_30.5")
        self.assertTrue(p_gt({"adx": 31}))
        self.assertFalse(p_gt({"adx": 30.5}))
        self.assertTrue(p_ge({"adx": 30.5}))

    def test_eq_categorical(self) -> None:
        _, pred = parse_atomic_id("direction_eq_LONG")
        self.assertTrue(pred({"direction": "LONG"}))
        self.assertFalse(pred({"direction": "SHORT"}))

    def test_combo_2(self) -> None:
        rule = parse_rule_id("2|atr_pct_le_0.150585|stoch_k_le_41.6667")
        self.assertEqual(len(rule.features), 2)
        self.assertTrue(rule.pred({"atr_pct": 0.1, "stoch_k": 30}))
        self.assertFalse(rule.pred({"atr_pct": 0.1, "stoch_k": 50}))

    def test_combo_3(self) -> None:
        rule = parse_rule_id("3|rsi_low|atr_pct_le_0.2|stoch_k_le_40")
        self.assertEqual(len(rule.features), 3)
        self.assertTrue(rule.pred({"rsi": 35, "atr_pct": 0.1, "stoch_k": 20}))

    def test_rule_from_candidate(self) -> None:
        cand = {
            "id": "2|rsi_low|ema20_distance_le_-1.879199",
            "label": "(rsi in [30.0,45.0)) AND (ema20_distance<=q25(-1.879))",
            "features": ["rsi", "ema20_distance"],
        }
        rule = rule_from_candidate(cand)
        self.assertIn("rsi", rule.label)
        self.assertTrue(rule.pred({"rsi": 33, "ema20_distance": -2.0}))

    def test_load_candidates_limit(self) -> None:
        payload = {"candidates": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        self.assertEqual(len(load_candidates(payload, limit=2)), 2)


class TestValidationSuite(unittest.TestCase):
    def test_sort_chrono(self) -> None:
        rows = [{"closed_at": 3}, {"closed_at": 1}, {"closed_at": 2}]
        ordered = sort_chrono(rows)
        self.assertEqual([r["closed_at"] for r in ordered], [1, 2, 3])

    def test_walk_forward_stable_passes(self) -> None:
        rows = _stable_edge_rows(120)
        rule = parse_rule_id("rsi_low")
        wf = walk_forward_validate(rows, rule, min_n=5)
        self.assertTrue(wf["passed"], msg=wf)

    def test_walk_forward_unstable_rejects(self) -> None:
        rows = _unstable_rows(80)
        rule = parse_rule_id("rsi_low")
        wf = walk_forward_validate(rows, rule, min_n=4)
        self.assertFalse(wf["passed"])
        self.assertTrue(wf.get("independent_failures") or "lost_edge" in str(wf.get("reason")))

    def test_rolling_windows(self) -> None:
        rows = _stable_edge_rows(100)
        rule = parse_rule_id("rsi_low")
        out = rolling_windows_validate(rows, rule, min_n=3, n_windows=4)
        self.assertIn("windows", out)
        self.assertEqual(len(out["windows"]), 4)

    def test_oos_replay_stable(self) -> None:
        rows = _stable_edge_rows(100)
        rule = parse_rule_id("rsi_low")
        out = oos_replay(rows, rule, min_n=4)
        self.assertTrue(out["passed"], msg=out)

    def test_oos_replay_unstable(self) -> None:
        rows = _unstable_rows(80)
        rule = parse_rule_id("rsi_low")
        out = oos_replay(rows, rule, min_n=3)
        self.assertFalse(out["passed"])

    def test_monte_carlo_detects_edge(self) -> None:
        base = list(np.random.default_rng(0).normal(0, 1, 80))
        sel = list(np.random.default_rng(1).normal(1.4, 0.3, 20))
        out = monte_carlo_null(sel, base + sel, n_sims=100, seed=2)
        self.assertIsNotNone(out["p_value"])
        self.assertLess(out["p_value"], 0.15)

    def test_stability_buckets(self) -> None:
        rows = _stable_edge_rows(100)
        rule = parse_rule_id("rsi_low")
        out = stability_over_time(rows, rule, n_buckets=4, min_n=3)
        self.assertEqual(out["n_buckets"], 4)
        self.assertTrue(out["passed"], msg=out)

    def test_validate_candidate_passes_stable(self) -> None:
        rows = _stable_edge_rows(140)
        rule = parse_rule_id("rsi_low")
        with mock.patch.dict("os.environ", {
            "ALPHA_VALIDATE_N_MC": "80",
            "ALPHA_VALIDATE_N_BOOT": "60",
            "ALPHA_VALIDATE_N_PERM": "50",
        }):
            out = validate_candidate(rows, rule, min_n=5, seed=1)
        self.assertEqual(out["status"], "PASSED", msg=out.get("reject_reason"))

    def test_validate_candidate_rejects_unstable(self) -> None:
        rows = _unstable_rows(90)
        rule = parse_rule_id("rsi_low")
        with mock.patch.dict("os.environ", {
            "ALPHA_VALIDATE_N_MC": "40",
            "ALPHA_VALIDATE_N_BOOT": "40",
            "ALPHA_VALIDATE_N_PERM": "30",
        }):
            out = validate_candidate(rows, rule, min_n=4, seed=2)
        self.assertEqual(out["status"], "REJECTED")
        self.assertTrue(out.get("reject_reason"))

    def test_validate_insufficient(self) -> None:
        rows = _stable_edge_rows(40)
        rule = parse_rule_id("rsi_ob")  # rare on this synth
        out = validate_candidate(rows, rule, min_n=50, seed=0)
        self.assertEqual(out["status"], "INSUFFICIENT")

    def test_match_rows(self) -> None:
        rows = [{"rsi": 40, "pnl": 1}, {"rsi": 70, "pnl": -1}]
        m = match_rows(rows, parse_rule_id("rsi_low"))
        self.assertEqual(len(m), 1)


class TestStoreAndReport(unittest.TestCase):
    def test_schema_and_upsert(self) -> None:
        conn = _conn()
        rid = new_run_id()
        start_history_run(conn, run_id=rid, n_candidates=1, n_rows=10)
        upsert_validation(conn, run_id=rid, result={
            "rule_id": "rsi_low",
            "rule_label": "rsi in [30,45)",
            "features": ["rsi"],
            "status": "PASSED",
            "reject_reason": None,
            "n_total": 10,
            "n_matched": 5,
            "expectancy": 1.2,
            "pf": 2.0,
            "winrate": 60,
            "sharpe": 0.5,
            "ci_ev": (0.1, 2.0),
            "p_value": 0.01,
            "walk_forward": {"passed": True},
            "rolling": {"passed": True},
            "oos": {"passed": True},
            "monte_carlo": {"passed": True},
            "stability": {"passed": True},
            "metrics": {"n": 5},
        })
        finish_history_run(
            conn, run_id=rid, n_passed=1, n_rejected=0, n_insufficient=0,
            summary={"ok": True}, report_path="/tmp/x.md",
        )
        conn.commit()
        rows = load_validations(conn, run_id=rid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PASSED")
        hist = load_history(conn, limit=5)
        self.assertEqual(hist[0]["n_passed"], 1)

    def test_format_report(self) -> None:
        md = format_validation_report(
            run_id="av2-test",
            n_rows=50,
            results=[{
                "rule_id": "rsi_low",
                "rule_label": "rsi low",
                "status": "PASSED",
                "n_matched": 10,
                "winrate": 70,
                "expectancy": 1.0,
                "pf": 2.0,
                "ci_ev": (0.2, 1.5),
                "p_value": 0.02,
                "gates": {"walk_forward": True, "rolling": True, "oos": True, "stability": True},
            }],
        )
        self.assertIn("ALPHA_VALIDATION_REPORT", md)
        self.assertIn("PASSED", md)

    def test_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_validation_v2.report.OUT_DIR",
                td_path / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_validation_v2.report.REPORT_MD",
                td_path / "ALPHA_VALIDATION_REPORT.md",
            ):
                paths = write_validation_artifacts(
                    run_id="r1",
                    n_rows=10,
                    results=[{"status": "REJECTED", "rule_label": "x", "reject_reason": "lost_edge"}],
                )
            self.assertTrue(Path(paths["report_md"]).exists())
            self.assertTrue(Path(paths["validations_json"]).exists())


class TestEngine(unittest.TestCase):
    def test_run_validation_end_to_end(self) -> None:
        rows = _stable_edge_rows(130)
        payload = {
            "candidates": [
                {"id": "rsi_low", "label": "rsi in [30.0,45.0)", "features": ["rsi"]},
                {
                    "id": "2|rsi_low|atr_pct_le_0.15",
                    "label": "(rsi) AND (atr_pct)",
                    "features": ["rsi", "atr_pct"],
                },
            ]
        }
        conn = _conn()
        with tempfile.TemporaryDirectory() as td:
            cand_path = Path(td) / "cands.json"
            cand_path.write_text(json.dumps(payload), encoding="utf-8")
            out_dir = Path(td) / "out"
            with mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_validation_v2.engine.load_alpha_dataset",
                return_value=rows,
            ), mock.patch.dict("os.environ", {
                "ALPHA_VALIDATE_N_MC": "40",
                "ALPHA_VALIDATE_N_BOOT": "40",
                "ALPHA_VALIDATE_N_PERM": "30",
                "ALPHA_VALIDATE_LIMIT": "10",
            }), mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_validation_v2.report.OUT_DIR",
                out_dir,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.alpha_validation_v2.report.REPORT_MD",
                Path(td) / "ALPHA_VALIDATION_REPORT.md",
            ):
                out = run_alpha_validation_v2(
                    conn,
                    candidates_path=cand_path,
                    write_reports=True,
                    persist=True,
                    backfill_candles=False,
                )
            self.assertTrue(out["ok"])
            self.assertEqual(out["n_candidates"], 2)
            self.assertTrue(out["gate_strategy_paper_execution_unchanged"])
            self.assertGreaterEqual(out["n_passed"], 1)
            # DB persisted
            db_rows = load_validations(conn, run_id=out["run_id"])
            self.assertEqual(len(db_rows), 2)
            rep = run_alpha_validation_report(conn, run_id=out["run_id"], write_reports=False)
            self.assertTrue(rep["ok"])
            self.assertEqual(rep["run_id"], out["run_id"])

    def test_package_exports(self) -> None:
        from bot.research.market_events.signal_intelligence.alpha_validation_v2 import (
            run_alpha_validation_report as r1,
            run_alpha_validation_v2 as r2,
        )
        self.assertTrue(callable(r1) and callable(r2))


if __name__ == "__main__":
    unittest.main()
