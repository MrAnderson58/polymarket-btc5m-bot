"""Experiment Framework V1 regression tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence import strategy_optimizer as opt
from bot.research.market_events.signal_intelligence import strategy_validation as val
from bot.research.market_events.signal_intelligence.experiment_registry import (
    STATUS_ACTIVE,
    ExperimentRegistry,
    build_default_registry,
    make_experiment,
)
from bot.research.market_events.signal_intelligence import experiment_runner as er
from bot.research.market_events.signal_intelligence.strategy_validation import FilterParams


def _trade(
    i: int,
    *,
    symbol: str = "BTC",
    pnl: float = 1.0,
    conf: float = 0.7,
    closed_at: int | None = None,
    mfe: float = 2.0,
    mae: float = -1.0,
    hold: float = 120.0,
) -> dict:
    return {
        "id": i,
        "symbol": symbol,
        "pnl_usd": pnl,
        "pnl_pct": pnl,
        "_confidence": conf,
        "closed_at": closed_at if closed_at is not None else i,
        "direction": "LONG",
        "mfe_pct": mfe,
        "mae_pct": mae,
        "holding_seconds": hold,
    }


def _profitable_filter_universe(n: int = 80) -> list[dict]:
    """High-conf winners + low-conf losers → confidence gate beats baseline."""
    rows = []
    for i in range(n):
        if i % 2 == 0:
            rows.append(_trade(i, pnl=3.0, conf=0.9, symbol="BTC"))
        else:
            rows.append(_trade(i, pnl=-2.0, conf=0.3, symbol="ETH"))
    return rows


class TestExperimentFrameworkV1(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["EXP_LEADERBOARD_PATH"] = str(root / "EXPERIMENT_LEADERBOARD.md")
        os.environ["EXP_STATE_PATH"] = str(root / "exp_state.json")
        os.environ["OPT_STATE_PATH"] = str(root / "opt_state.json")
        os.environ["OPT_AUTO_APPLY"] = "0"
        os.environ["VAL_AUTO_APPLY"] = "0"
        os.environ["VAL_MIN_SAMPLE"] = "20"
        os.environ["VAL_MIN_CONFIDENCE"] = "0.4"
        os.environ["VAL_MIN_PF_INCREASE"] = "0.0"
        os.environ["VAL_MIN_EXPECTANCY_INCREASE"] = "0.0"
        os.environ["VAL_MAX_DRAWDOWN_INCREASE"] = "100.0"
        os.environ["VAL_P_VALUE_MAX"] = "0.99"
        os.environ["VAL_OVERFIT_EXP_GAP"] = "10.0"
        os.environ["VAL_BOOTSTRAP_N"] = "40"
        os.environ["VAL_WF_TRAIN"] = "20"
        os.environ["VAL_WF_TEST"] = "10"
        os.environ["VAL_RANDOM_SEED"] = "42"
        os.environ["EXP_MIN_SAMPLE"] = "20"
        os.environ["EXP_MIN_CONFIDENCE"] = "0.4"
        os.environ["EXP_MIN_PF_INCREASE"] = "0.0"
        os.environ["EXP_MIN_EXPECTANCY_INCREASE"] = "0.0"
        os.environ["EXP_MAX_DRAWDOWN_INCREASE"] = "100.0"
        os.environ["EXP_P_VALUE_MAX"] = "0.99"
        os.environ["EXP_BOOTSTRAP_N"] = "40"
        # Empty production applied filters
        Path(os.environ["OPT_STATE_PATH"]).write_text(
            '{"applied": {}, "recommended": {}, "history": []}',
            encoding="utf-8",
        )
        val.refresh_validation_config_from_env()
        opt.refresh_optimizer_config_from_env()
        er.refresh_experiment_config_from_env()

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith(("EXP_", "VAL_")) or k in ("OPT_STATE_PATH", "OPT_AUTO_APPLY"):
                os.environ.pop(k, None)
        val.refresh_validation_config_from_env()
        opt.refresh_optimizer_config_from_env()
        er.refresh_experiment_config_from_env()
        self.tmp.cleanup()

    def test_experiment_registration(self) -> None:
        reg = ExperimentRegistry()
        exp = make_experiment(
            id="x1",
            name="Test",
            description="d",
            parameters={"confidence_threshold": 0.6},
        )
        reg.register(exp)
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.get("x1").name, "Test")
        with self.assertRaises(ValueError):
            reg.register(exp)
        reg.register(exp, replace=True)
        self.assertEqual(len(build_default_registry()), 6)

    def test_runner_identical_inputs(self) -> None:
        rows = _profitable_filter_universe(60)
        reg = ExperimentRegistry()
        reg.register(
            make_experiment(
                id="a",
                name="A",
                description="a",
                parameters={"confidence_threshold": 0.6},
            )
        )
        reg.register(
            make_experiment(
                id="b",
                name="B",
                description="b",
                parameters={"confidence_threshold": 0.7},
            )
        )
        seen_n: list[int] = []

        real_eval = er.evaluate_experiment_vs_baseline

        def wrap(rows_arg, baseline, experiment):
            seen_n.append(len(rows_arg))
            return real_eval(rows_arg, baseline, experiment)

        with mock.patch.object(er, "evaluate_experiment_vs_baseline", side_effect=wrap):
            out = er.run_experiments(None, registry=reg, rows=rows, write_leaderboard=True)
        self.assertEqual(seen_n, [60, 60])
        self.assertEqual(out["universe_n"], 60)
        self.assertEqual(out["n_experiments"], 2)

    def test_metrics_fields(self) -> None:
        rows = [_trade(i, pnl=1.0 if i % 2 else -0.5) for i in range(20)]
        m = er.compute_experiment_metrics(rows)
        for key in (
            "n", "pf", "expectancy", "winrate", "avg_pnl",
            "max_drawdown", "sharpe", "mfe", "mae", "holding_time",
        ):
            self.assertIn(key, m)
        self.assertEqual(m["n"], 20)
        self.assertIsNotNone(m["mfe"])
        self.assertIsNotNone(m["holding_time"])

    def test_baseline_comparison_only(self) -> None:
        rows = _profitable_filter_universe(60)
        baseline = FilterParams(label="production_baseline")
        exp = make_experiment(
            id="conf",
            name="Conf",
            description="c",
            parameters={"confidence_threshold": 0.6},
        )
        result = er.evaluate_experiment_vs_baseline(rows, baseline, exp)
        self.assertEqual(result["compared_to"], er.PRODUCTION_BASELINE_ID)
        self.assertFalse(result["compared_to_experiments"])
        self.assertIn("delta_vs_baseline", result)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["auto_deploy"])

    def test_promotion_logic_reject(self) -> None:
        ok, reasons = er.promotion_decision(
            sample_n=5,
            confidence=0.1,
            pf_delta=-0.5,
            expectancy_delta=-1.0,
            drawdown_delta=50.0,
            p_value=0.9,
            validation_passed=False,
        )
        self.assertFalse(ok)
        self.assertTrue(any("validation_failed" in r for r in reasons))
        self.assertTrue(any("sample_n" in r for r in reasons))

    def test_promotion_logic_accept(self) -> None:
        ok, reasons = er.promotion_decision(
            sample_n=40,
            confidence=0.8,
            pf_delta=0.2,
            expectancy_delta=0.1,
            drawdown_delta=0.0,
            p_value=0.01,
            validation_passed=True,
        )
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_leaderboard_roles(self) -> None:
        def fake(eid: str, promote: bool, score: float) -> dict:
            return {
                "experiment": {"id": eid, "name": eid},
                "promotion_recommended": promote,
                "validation_score": score,
                "confidence": 0.8,
                "metrics": {"pf": 1.5, "expectancy": 0.2, "n": 40},
            }

        ranked = er.assign_leaderboard_roles([
            fake("low", True, 10),
            fake("mid", True, 20),
            fake("hi", True, 30),
            fake("bad", False, 99),
        ])
        roles = {r["experiment"]["id"]: r["role"] for r in ranked}
        self.assertEqual(roles["hi"], er.ROLE_CHAMPION)
        self.assertEqual(roles["mid"], er.ROLE_RUNNER_UP)
        self.assertEqual(roles["low"], er.ROLE_QUALIFIED)
        self.assertEqual(roles["bad"], er.ROLE_REJECTED)
        # Sort order: champion first by score among all
        self.assertEqual(ranked[0]["experiment"]["id"], "hi")

    def test_multiple_experiments_end_to_end(self) -> None:
        rows = _profitable_filter_universe(80)
        reg = build_default_registry()
        # Keep only two for speed
        for eid in list(reg._by_id):
            if eid not in ("conf_gate_065", "disable_eth"):
                reg.unregister(eid)
        out = er.run_experiments(None, registry=reg, rows=rows, write_leaderboard=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_experiments"], 2)
        self.assertFalse(out["paper_trading_affected"])
        self.assertFalse(out["may_auto_deploy"])
        path = Path(os.environ["EXP_LEADERBOARD_PATH"])
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("Champion", text)
        self.assertIn("Rejected", text)
        self.assertIn(er.PRODUCTION_BASELINE_ID, text)
        # Safety: optimizer state untouched beyond empty applied
        state = opt.load_optimizer_state()
        self.assertEqual(state.get("applied"), {})

    def test_may_auto_deploy_always_false(self) -> None:
        os.environ["EXP_AUTO_DEPLOY"] = "1"
        er.refresh_experiment_config_from_env()
        self.assertFalse(er.may_auto_deploy())
        self.assertFalse(er.EXP_AUTO_DEPLOY)

    def test_no_production_write(self) -> None:
        rows = _profitable_filter_universe(40)
        reg = ExperimentRegistry()
        reg.register(
            make_experiment(
                id="c",
                name="C",
                description="c",
                parameters={"confidence_threshold": 0.55},
            )
        )
        with mock.patch.object(opt, "save_optimizer_state") as save:
            er.run_experiments(None, registry=reg, rows=rows, write_leaderboard=True)
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
