"""Adaptive Strategy Optimizer V1 regression tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import (
    configure_unit_test_db_isolation,
    reset_db_config_for_tests,
)
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import strategy_optimizer as opt
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55


class TestStrategyOptimizerV1(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live_path = Path(self.tmp.name) / "live.db"
        self.state_path = Path(self.tmp.name) / "opt_state.json"
        self.report_path = Path(self.tmp.name) / "OPTIMIZER_REPORT.md"
        configure_unit_test_db_isolation(self.live_path)
        os.environ["OPT_STATE_PATH"] = str(self.state_path)
        os.environ["OPT_REPORT_PATH"] = str(self.report_path)
        os.environ["OPT_AUTO_APPLY"] = "0"
        os.environ["OPT_MIN_SAMPLE"] = "10"
        opt.refresh_optimizer_config_from_env()
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        reset_db_config_for_tests()
        for key in (
            "OPT_STATE_PATH",
            "OPT_REPORT_PATH",
            "OPT_AUTO_APPLY",
            "OPT_MIN_SAMPLE",
            "OPT_AUTO_MIN_SAMPLE",
            "OPT_AUTO_MIN_CONFIDENCE",
            "OPT_AUTO_MIN_PF_IMPROVEMENT",
        ):
            os.environ.pop(key, None)
        opt.refresh_optimizer_config_from_env()
        self.tmp.cleanup()

    def _insert_closed(
        self,
        conn,
        *,
        sid: int,
        symbol: str,
        pnl_pct: float,
        pnl_usd: float | None = None,
        result: str | None = None,
        confidence: float = 0.7,
        exit_reason: str = "TRAILING",
        mfe: float = 2.0,
        mae: float = -1.0,
        hold: int = 600,
        gate: str = "ALLOWED",
        regime: str = "RANGE",
        direction: str = "LONG",
        pattern: str = "breakout",
        news: str = "macro",
    ) -> None:
        if result is None:
            result = "WIN" if pnl_pct > 0 else ("LOSS" if pnl_pct < 0 else "BE")
        if pnl_usd is None:
            pnl_usd = pnl_pct
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              id, s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, capital_usd, leverage, updated_at,
              result, pnl_pct, pnl_usd, closed_at, exit_reason,
              decision_confidence, pattern_json, news_category, holding_seconds
            ) VALUES (
              ?, 'opt', ?, ?, ?,
              100, 95, 105, 110, ?, 'CLOSED',
              ?, ?, 100, 20, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?
            )
            """,
            (
                sid, sid, symbol, direction,
                self.now - hold, mfe, mae, self.now,
                result, pnl_pct, pnl_usd, self.now, exit_reason,
                confidence, json.dumps({"name": pattern}), news, hold,
            ),
        )
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              hour, weekday, ai_score, gate_decision, market_regime,
              result, pnl_pct, pnl_usd, mfe_pct, mae_pct, duration_sec,
              exit_reason, created_at, closed_at
            ) VALUES (?, 'opt', ?, ?, ?, 12, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid, sid, symbol, direction, confidence, gate, regime,
                result, pnl_pct, pnl_usd, mfe, mae, hold, exit_reason,
                self.now - hold, self.now,
            ),
        )

    def test_symbol_disable(self) -> None:
        # BAD: 12 losses → PF < 1, negative expectancy, n>=10
        with market_events_connection() as conn:
            for i in range(12):
                self._insert_closed(
                    conn, sid=i + 1, symbol="BAD", pnl_pct=-2.0, confidence=0.6,
                )
            for i in range(12):
                self._insert_closed(
                    conn, sid=100 + i, symbol="GOOD", pnl_pct=2.0, confidence=0.8,
                )
            conn.commit()
            rows = opt.load_optimizer_trades(conn)
        segs = opt.compute_segment_report(rows)
        filt = opt.evaluate_symbol_filter(segs["by_symbol"], previous_disabled=set())
        self.assertIn("BAD", filt["disabled_symbols"])
        self.assertNotIn("GOOD", filt["disabled_symbols"])
        disable_actions = [d for d in filt["decisions"] if d["symbol"] == "BAD"]
        self.assertTrue(any(d["action"] == "DISABLE" for d in disable_actions))

    def test_symbol_recovery(self) -> None:
        with market_events_connection() as conn:
            # Strong recovery sample for RECOVER
            for i in range(15):
                self._insert_closed(
                    conn, sid=i + 1, symbol="RECOVER", pnl_pct=3.0, confidence=0.7,
                )
            conn.commit()
            rows = opt.load_optimizer_trades(conn)
        segs = opt.compute_segment_report(rows)
        filt = opt.evaluate_symbol_filter(
            segs["by_symbol"], previous_disabled={"RECOVER"},
        )
        self.assertNotIn("RECOVER", filt["disabled_symbols"])
        self.assertTrue(
            any(d["action"] == "RE_ENABLE" and d["symbol"] == "RECOVER" for d in filt["decisions"])
        )

    def test_minimum_sample_protection(self) -> None:
        with market_events_connection() as conn:
            for i in range(5):  # below OPT_MIN_SAMPLE=10
                self._insert_closed(
                    conn, sid=i + 1, symbol="TINY", pnl_pct=-5.0, confidence=0.5,
                )
            conn.commit()
            rows = opt.load_optimizer_trades(conn)
        segs = opt.compute_segment_report(rows)
        filt = opt.evaluate_symbol_filter(segs["by_symbol"], previous_disabled=set(), min_sample=10)
        self.assertNotIn("TINY", filt["disabled_symbols"])
        self.assertTrue(
            any(d["action"] == "INSUFFICIENT_SAMPLE" for d in filt["decisions"] if d["symbol"] == "TINY")
        )

    def test_confidence_optimization(self) -> None:
        rows = []
        # Low confidence mostly lose; high confidence mostly win
        for i in range(20):
            rows.append({
                "pnl_usd": -5.0, "pnl_pct": -5.0, "_confidence": 0.52,
                "symbol": "X", "direction": "LONG", "gate_decision": "ALLOWED",
                "pattern": "p", "news_category": "n", "market_regime": "RANGE",
                "exit_reason": "STOP", "mfe_pct": 0.5, "mae_pct": -5.0,
                "holding_seconds": 100,
            })
        for i in range(20):
            rows.append({
                "pnl_usd": 8.0, "pnl_pct": 8.0, "_confidence": 0.85,
                "symbol": "X", "direction": "LONG", "gate_decision": "ALLOWED",
                "pattern": "p", "news_category": "n", "market_regime": "RANGE",
                "exit_reason": "TP1", "mfe_pct": 8.0, "mae_pct": -1.0,
                "holding_seconds": 200,
            })
        out = opt.optimize_confidence_threshold(rows, min_sample=10)
        self.assertIsNotNone(out["recommended_threshold"])
        self.assertGreaterEqual(float(out["recommended_threshold"]), 0.55)
        self.assertGreater(
            float(out["recommended_metrics"]["expectancy"]),
            float(out["baseline"]["expectancy"]),
        )

    def test_exploration_adaptation(self) -> None:
        weak = [{"pnl_usd": -2.0, "pnl_pct": -2.0} for _ in range(30)]
        strong = [{"pnl_usd": 3.0, "pnl_pct": 3.0} for _ in range(30)]
        r_weak = opt.compute_exploration_rate(weak)
        r_strong = opt.compute_exploration_rate(strong)
        self.assertGreaterEqual(r_weak["exploration_rate"], opt.OPT_EXPLORE_MIN)
        self.assertLessEqual(r_weak["exploration_rate"], opt.OPT_EXPLORE_MAX)
        self.assertGreater(r_weak["exploration_rate"], r_strong["exploration_rate"])

    def test_optimizer_report(self) -> None:
        with market_events_connection() as conn:
            for i in range(20):
                self._insert_closed(
                    conn,
                    sid=i + 1,
                    symbol="BTC" if i % 2 == 0 else "ETH",
                    pnl_pct=1.5 if i % 3 else -1.0,
                    confidence=0.55 + (i % 5) * 0.05,
                    exit_reason="TRAILING" if i % 2 == 0 else "STOP",
                )
            conn.commit()
            result = opt.run_strategy_optimizer(
                conn,
                write_report=True,
                report_path=self.report_path,
                state_path=self.state_path,
            )
        self.assertTrue(result["ok"])
        self.assertTrue(self.report_path.exists())
        text = self.report_path.read_text(encoding="utf-8")
        self.assertIn("OPTIMIZER_REPORT", text)
        self.assertIn("Current parameters", text)
        self.assertIn("Recommended parameters", text)
        self.assertIn("Expected improvement", text)
        self.assertIn("Confidence", text)
        self.assertIn("Sample size", text)
        self.assertIn("RECOMMEND_ONLY", " ".join(result["apply_actions"]))

    def test_auto_apply_blocked_without_thresholds(self) -> None:
        self.assertFalse(
            opt.should_auto_apply(sample_n=100, confidence=0.9, pf_improvement=0.5),
        )  # OPT_AUTO_APPLY=0
        os.environ["OPT_AUTO_APPLY"] = "1"
        os.environ["OPT_AUTO_MIN_SAMPLE"] = "50"
        os.environ["OPT_AUTO_MIN_CONFIDENCE"] = "0.7"
        os.environ["OPT_AUTO_MIN_PF_IMPROVEMENT"] = "0.1"
        opt.refresh_optimizer_config_from_env()
        self.assertFalse(
            opt.should_auto_apply(sample_n=10, confidence=0.9, pf_improvement=0.5),
        )
        self.assertFalse(
            opt.should_auto_apply(sample_n=100, confidence=0.2, pf_improvement=0.5),
        )
        self.assertFalse(
            opt.should_auto_apply(sample_n=100, confidence=0.9, pf_improvement=0.01),
        )
        self.assertTrue(
            opt.should_auto_apply(sample_n=100, confidence=0.9, pf_improvement=0.5),
        )

    def test_gate_symbol_disabled_and_confidence(self) -> None:
        opt.save_optimizer_state({
            "applied": {
                "disabled_symbols": ["NEAR"],
                "confidence_threshold": 0.80,
            },
            "recommended": {},
            "history": [],
        })
        self.assertTrue(opt.is_symbol_disabled("NEAR"))
        self.assertFalse(opt.is_symbol_disabled("BTC"))

        with market_events_connection() as conn:
            allow, reason, _ = s55.should_open_trade(
                conn,
                features={"symbol": "NEAR", "direction": "LONG", "decision_confidence": 0.9},
                open_count=0,
                skip_max_open_check=True,
            )
            self.assertFalse(allow)
            self.assertEqual(reason, s55.GATE_SYMBOL_DISABLED)

            allow2, reason2, _ = s55.should_open_trade(
                conn,
                features={"symbol": "BTC", "direction": "LONG", "decision_confidence": 0.5},
                open_count=0,
                skip_max_open_check=True,
            )
            self.assertFalse(allow2)
            self.assertEqual(reason2, s55.GATE_CONFIDENCE_BLOCK)

    def test_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main

        with market_events_connection() as conn:
            for i in range(12):
                self._insert_closed(
                    conn, sid=i + 1, symbol="BTC", pnl_pct=1.0 if i % 2 else -0.5,
                )
            conn.commit()
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            rc = main(["optimize-strategy"])
        self.assertEqual(rc, 0)
        self.assertTrue(self.report_path.exists())
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            rc2 = main(["optimizer-report"])
        self.assertEqual(rc2, 0)


if __name__ == "__main__":
    unittest.main()
