"""Adaptive Strategy Validation V2 regression tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence import strategy_optimizer as opt
from bot.research.market_events.signal_intelligence import strategy_validation as val
from bot.research.market_events.signal_intelligence.strategy_validation import FilterParams


def _trade(
    i: int,
    *,
    symbol: str = "BTC",
    pnl: float = 1.0,
    conf: float = 0.7,
    closed_at: int | None = None,
) -> dict:
    return {
        "id": i,
        "symbol": symbol,
        "pnl_usd": pnl,
        "pnl_pct": pnl,
        "_confidence": conf,
        "closed_at": closed_at if closed_at is not None else i,
        "direction": "LONG",
    }


class TestStrategyValidationV2(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.report = Path(self.tmp.name) / "VALIDATION_REPORT.md"
        os.environ["VAL_REPORT_PATH"] = str(self.report)
        os.environ["VAL_STATE_PATH"] = str(Path(self.tmp.name) / "val_state.json")
        os.environ["OPT_STATE_PATH"] = str(Path(self.tmp.name) / "opt_state.json")
        os.environ["VAL_AUTO_APPLY"] = "0"
        os.environ["OPT_AUTO_APPLY"] = "0"
        os.environ["VAL_MIN_SAMPLE"] = "20"
        os.environ["VAL_MIN_CONFIDENCE"] = "0.5"
        os.environ["VAL_MIN_PF_INCREASE"] = "0.0"
        os.environ["VAL_MIN_EXPECTANCY_INCREASE"] = "0.0"
        os.environ["VAL_MAX_DRAWDOWN_INCREASE"] = "100.0"
        os.environ["VAL_P_VALUE_MAX"] = "0.5"
        os.environ["VAL_OVERFIT_EXP_GAP"] = "10.0"
        os.environ["VAL_BOOTSTRAP_N"] = "50"
        os.environ["VAL_WF_TRAIN"] = "20"
        os.environ["VAL_WF_TEST"] = "10"
        os.environ["VAL_RANDOM_SEED"] = "42"
        val.refresh_validation_config_from_env()
        opt.refresh_optimizer_config_from_env()

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("VAL_") or k in ("OPT_STATE_PATH", "OPT_AUTO_APPLY"):
                os.environ.pop(k, None)
        val.refresh_validation_config_from_env()
        opt.refresh_optimizer_config_from_env()
        self.tmp.cleanup()

    def test_ab_replay_identical_universe(self) -> None:
        rows = [_trade(i, pnl=2.0 if i % 2 == 0 else -1.0, conf=0.9) for i in range(40)]
        # Inject losers with low confidence
        for i in range(40, 60):
            rows.append(_trade(i, pnl=-3.0, conf=0.4))
        current = FilterParams(confidence_threshold=None, label="cur")
        candidate = FilterParams(confidence_threshold=0.6, label="cand")
        ab = val.ab_replay(rows, current, candidate)
        self.assertEqual(ab["universe_n"], 60)
        self.assertEqual(ab["current"]["metrics"]["n"], 60)
        self.assertLess(ab["candidate"]["metrics"]["n"], 60)
        self.assertGreater(
            float(ab["candidate"]["metrics"]["expectancy"]),
            float(ab["current"]["metrics"]["expectancy"]),
        )
        # Same input list identity — deltas derived from one universe
        self.assertIn("pf_delta", ab["delta"])

    def test_cross_validation_split(self) -> None:
        rows = [_trade(i, pnl=1.0) for i in range(100)]
        train, valid = val.train_validation_split(rows, train_frac=0.7)
        self.assertEqual(len(train) + len(valid), 100)
        self.assertEqual(len(train), 70)
        self.assertEqual(len(valid), 30)
        # Chronological: last train closed_at < first valid
        self.assertLessEqual(train[-1]["closed_at"], valid[0]["closed_at"])

    def test_overfitting_rejection(self) -> None:
        # Train: high-conf winners + low-conf losers → thr helps
        # Val: high-conf losers + low-conf winners → thr hurts (overfit)
        rows = []
        for i in range(70):
            if i % 2 == 0:
                rows.append(_trade(i, pnl=5.0, conf=0.9, closed_at=i))
            else:
                rows.append(_trade(i, pnl=-5.0, conf=0.3, closed_at=i))
        for i in range(70, 100):
            if i % 2 == 0:
                rows.append(_trade(i, pnl=-5.0, conf=0.9, closed_at=i))
            else:
                rows.append(_trade(i, pnl=5.0, conf=0.3, closed_at=i))
        current = FilterParams(confidence_threshold=None, label="cur")
        candidate = FilterParams(confidence_threshold=0.8, label="cand")
        os.environ["VAL_OVERFIT_EXP_GAP"] = "0.1"
        os.environ["VAL_MIN_EXPECTANCY_INCREASE"] = "0.1"
        val.refresh_validation_config_from_env()
        cv = val.cross_validate_recommendation(rows, current, candidate)
        self.assertTrue(cv["overfit"])
        out = val.validate_recommendation_pair(rows, current, candidate)
        self.assertFalse(out["accepted"])
        self.assertTrue(
            any("overfit" in str(r).lower() for r in out["rejection_reasons"]),
        )

    def test_walk_forward(self) -> None:
        rows = [_trade(i, pnl=1.0 if i % 3 else -0.5, conf=0.55 + (i % 10) * 0.04) for i in range(80)]
        current = FilterParams(confidence_threshold=None)
        candidate = FilterParams(confidence_threshold=0.7)
        wf = val.walk_forward(rows, current, candidate, train_size=20, test_size=10)
        self.assertGreaterEqual(wf["n_folds"], 2)
        self.assertIn("expectancy_delta_mean", wf["agg"])
        for fold in wf["folds"]:
            self.assertEqual(fold["test_n"], 10)
            self.assertIn("delta", fold)

    def test_confidence_rejection(self) -> None:
        ok, reasons = val.decision_gates(
            sample_n=100,
            confidence=0.1,
            pf_delta=1.0,
            expectancy_delta=1.0,
            drawdown_delta=0.0,
            p_value=0.01,
            overfit=False,
        )
        self.assertFalse(ok)
        self.assertTrue(any("confidence" in r for r in reasons))

    def test_auto_apply_safety(self) -> None:
        self.assertFalse(val.should_auto_apply_validated(True))  # flags off
        os.environ["VAL_AUTO_APPLY"] = "1"
        os.environ["OPT_AUTO_APPLY"] = "0"
        val.refresh_validation_config_from_env()
        opt.refresh_optimizer_config_from_env()
        self.assertFalse(val.should_auto_apply_validated(True))
        os.environ["OPT_AUTO_APPLY"] = "1"
        opt.refresh_optimizer_config_from_env()
        self.assertTrue(val.should_auto_apply_validated(True))
        self.assertFalse(val.should_auto_apply_validated(False))

    def test_full_validation_report(self) -> None:
        # Build a clear win: filter out low-conf losers
        rows = []
        for i in range(50):
            rows.append(_trade(i, symbol="GOOD", pnl=2.0, conf=0.85))
        for i in range(50, 80):
            rows.append(_trade(i, symbol="BAD", pnl=-4.0, conf=0.3))
        opt.save_optimizer_state({
            "applied": {"disabled_symbols": [], "confidence_threshold": None},
            "recommended": {
                "disabled_symbols": ["BAD"],
                "confidence_threshold": 0.6,
                "exploration_rate": 0.08,
            },
            "history": [],
        })
        # Lower bars so a strong signal can accept in unit test
        os.environ["VAL_MIN_SAMPLE"] = "20"
        os.environ["VAL_MIN_CONFIDENCE"] = "0.3"
        os.environ["VAL_MIN_PF_INCREASE"] = "-1.0"
        os.environ["VAL_P_VALUE_MAX"] = "0.99"
        os.environ["VAL_OVERFIT_EXP_GAP"] = "50.0"
        val.refresh_validation_config_from_env()

        out = val.run_optimizer_validation(
            conn=None,
            write_report=True,
            report_path=self.report,
            rows=rows,
            run_optimizer_if_needed=False,
        )
        self.assertTrue(out["ok"])
        self.assertTrue(self.report.exists())
        text = self.report.read_text(encoding="utf-8")
        self.assertIn("VALIDATION_REPORT", text)
        self.assertIn("Accepted recommendations", text)
        self.assertIn("Rejected recommendations", text)
        self.assertIn("RECOMMEND_ONLY", out["apply_note"])

    def test_symbol_filter_ab(self) -> None:
        rows = (
            [_trade(i, symbol="KEEP", pnl=2.0) for i in range(30)]
            + [_trade(30 + i, symbol="DROP", pnl=-5.0) for i in range(30)]
        )
        current = FilterParams(disabled_symbols=set())
        candidate = FilterParams(disabled_symbols={"DROP"})
        ab = val.ab_replay(rows, current, candidate)
        self.assertEqual(ab["candidate"]["metrics"]["n"], 30)
        self.assertGreater(ab["delta"]["expectancy_delta"], 0)

    def test_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main

        rows = [_trade(i, pnl=1.0 if i % 2 else -0.5, conf=0.7) for i in range(40)]
        opt.save_optimizer_state({
            "applied": {},
            "recommended": {"disabled_symbols": [], "confidence_threshold": 0.75},
            "history": [],
        })
        with mock.patch(
            "bot.research.market_events.signal_intelligence.strategy_validation.opt.load_optimizer_trades",
            return_value=rows,
        ), mock.patch(
            "bot.research.market_events.db.market_events_connection",
        ) as mconn, mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            # provide dummy connection context
            class _C:
                def __enter__(self):
                    return mock.MagicMock()

                def __exit__(self, *a):
                    return False

            mconn.return_value = _C()
            with mock.patch(
                "bot.research.market_events.event_schema.apply_migrations",
            ):
                rc = main(["validate-optimizer"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.report.exists())


if __name__ == "__main__":
    unittest.main()
