"""Tests for Bidirectional Momentum Strategy V1."""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import init_db, connect
from bot.research.features import (
    MovementFeatures,
    build_features_for_market,
    compute_btc_moves,
    compute_direction_consistency,
    classify_regime,
)
from bot.strategy.bidirectional_momentum import (
    DirectionDecision,
    EntryConfig,
    ExitConfig,
    evaluate_direction,
    get_exit_config,
)


class FeaturesTestCase(unittest.TestCase):
    def test_compute_btc_moves_basic(self):
        obs = [
            {"timestamp": 100, "btc_price": 60000.0, "market_slug": "test", "seconds_from_start": 0, "seconds_left": 300},
            {"timestamp": 110, "btc_price": 60010.0, "market_slug": "test", "seconds_from_start": 10, "seconds_left": 290},
            {"timestamp": 130, "btc_price": 60030.0, "market_slug": "test", "seconds_from_start": 30, "seconds_left": 270},
        ]
        moves = compute_btc_moves(obs, 2)
        self.assertAlmostEqual(moves["btc_move_30s"], 30.0, places=0)
        self.assertTrue(moves["btc_velocity_10s"] > 0)

    def test_direction_consistency_all_positive(self):
        moves = {"btc_move_10s": 5, "btc_move_30s": 15, "btc_move_60s": 30}
        self.assertEqual(compute_direction_consistency(moves), 1.0)

    def test_direction_consistency_mixed(self):
        moves = {"btc_move_10s": 5, "btc_move_30s": -10, "btc_move_60s": 20}
        self.assertLess(compute_direction_consistency(moves), 1.0)

    def test_classify_regime_normal(self):
        moves = {"btc_move_30s": 15, "btc_move_60s": 20, "btc_velocity_10s": 1.0, "btc_velocity_30s": 0.5, "btc_acceleration": 0, "direction_consistency": 0.5}
        self.assertEqual(classify_regime(moves), "NORMAL")

    def test_classify_regime_news_spike(self):
        moves = {"btc_move_30s": 100, "btc_move_60s": 100, "btc_velocity_10s": 5, "btc_velocity_30s": 3, "btc_acceleration": 0.1, "direction_consistency": 1.0}
        self.assertEqual(classify_regime(moves), "NEWS_SPIKE")

    def test_classify_regime_reversal(self):
        moves = {"btc_move_30s": 10, "btc_move_60s": 10, "btc_velocity_10s": 2, "btc_velocity_30s": -2, "btc_acceleration": 0, "direction_consistency": 0.3}
        self.assertEqual(classify_regime(moves), "REVERSAL")


class DirectionEngineTestCase(unittest.TestCase):
    def _make_feat(self, **kwargs) -> MovementFeatures:
        defaults = dict(
            timestamp=100, market_slug="test",
            seconds_from_start=60, seconds_left=240,
            btc_price=60000, strike=60000, delta=0,
            yes_bid=0.35, yes_ask=0.37, no_bid=0.35, no_ask=0.37, spread=0.02,
            btc_move_5s=0, btc_move_10s=0, btc_move_15s=0,
            btc_move_30s=0, btc_move_60s=0, btc_move_90s=0,
            btc_velocity_10s=0, btc_velocity_30s=0, btc_acceleration=0,
            distance_from_strike_usd=0, distance_from_strike_pct=0,
            direction_consistency=0, regime="NORMAL",
        )
        defaults.update(kwargs)
        return MovementFeatures(**defaults)

    def test_skip_too_early(self):
        feat = self._make_feat(seconds_from_start=5)
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "SKIP")
        self.assertEqual(d.reason, "too_early")

    def test_skip_too_late(self):
        feat = self._make_feat(seconds_from_start=260)
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "SKIP")
        self.assertEqual(d.reason, "too_late")

    def test_skip_chop(self):
        feat = self._make_feat(regime="CHOP")
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "SKIP")

    def test_yes_on_positive_movement(self):
        feat = self._make_feat(
            btc_move_30s=40, btc_velocity_10s=3.0,
            direction_consistency=0.8, distance_from_strike_pct=0.5,
        )
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "YES")
        self.assertGreater(d.probability_yes, d.probability_no)

    def test_no_on_negative_movement(self):
        feat = self._make_feat(
            btc_move_30s=-40, btc_velocity_10s=-3.0,
            direction_consistency=0.8, distance_from_strike_pct=-0.5,
        )
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "NO")
        self.assertGreater(d.probability_no, d.probability_yes)

    def test_skip_low_confidence(self):
        feat = self._make_feat(btc_move_30s=3)
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "SKIP")

    def test_skip_expensive_ask(self):
        feat = self._make_feat(
            btc_move_30s=40, btc_velocity_10s=3.0,
            direction_consistency=0.8, yes_ask=0.80,
        )
        d = evaluate_direction(feat)
        self.assertEqual(d.decision, "SKIP")

    def test_decision_is_symmetric(self):
        feat_pos = self._make_feat(btc_move_30s=30, btc_velocity_10s=2.0, direction_consistency=0.7, distance_from_strike_pct=0.3)
        feat_neg = self._make_feat(btc_move_30s=-30, btc_velocity_10s=-2.0, direction_consistency=0.7, distance_from_strike_pct=-0.3)
        d_pos = evaluate_direction(feat_pos)
        d_neg = evaluate_direction(feat_neg)
        if d_pos.decision != "SKIP":
            self.assertEqual(d_pos.decision, "YES")
        if d_neg.decision != "SKIP":
            self.assertEqual(d_neg.decision, "NO")


class ExitConfigTestCase(unittest.TestCase):
    def test_regime_exit_configs(self):
        normal = get_exit_config("NORMAL")
        momentum = get_exit_config("MOMENTUM")
        self.assertGreater(abs(momentum.stop_loss_pct), abs(normal.stop_loss_pct))
        self.assertGreater(momentum.trailing_activation_pct, normal.trailing_activation_pct)

    def test_unknown_regime_returns_normal(self):
        cfg = get_exit_config("UNKNOWN_REGIME")
        normal = get_exit_config("NORMAL")
        self.assertEqual(cfg.stop_loss_pct, normal.stop_loss_pct)


class ReplayTestCase(unittest.TestCase):
    def test_replay_runs_without_error(self):
        from bot.research.bidirectional_replay import run_replay

        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "test.db"
        init_db(db_path)

        with connect(db_path) as conn:
            result = run_replay(conn, max_markets=5)
            self.assertEqual(result.markets_processed, 0)

    def test_replay_with_real_data(self):
        from bot.research.bidirectional_replay import run_replay

        with connect() as conn:
            result = run_replay(conn, max_markets=10)
            self.assertGreater(result.markets_processed, 0)
            m = result.metrics()
            self.assertIn("trades", m)
            self.assertIn("pf", m)


class ShadowStateTestCase(unittest.TestCase):
    def test_shadow_state_empty_db(self):
        from bot.strategy.bidirectional_shadow import shadow_state

        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "test.db"
        init_db(db_path)

        with connect(db_path) as conn:
            state = shadow_state(conn)
            self.assertEqual(state["status"], "COLLECTING")
            self.assertEqual(state["trades_closed"], 0)


class SafetyTestCase(unittest.TestCase):
    """Prove shadow code has no import path to execution/order placement."""

    def test_no_execution_imports_in_momentum(self):
        import importlib
        import inspect
        mod = importlib.import_module("bot.strategy.bidirectional_momentum")
        source = inspect.getsource(mod)
        forbidden = ["place_order", "market_order", "limit_order", "attempt_entry",
                     "from bot.execution", "import bot.execution"]
        for word in forbidden:
            self.assertNotIn(word, source, f"Forbidden import/call found: {word}")

    def test_no_execution_imports_in_shadow(self):
        import importlib
        import inspect
        mod = importlib.import_module("bot.strategy.bidirectional_shadow")
        source = inspect.getsource(mod)
        forbidden = ["place_order", "market_order", "limit_order", "attempt_entry",
                     "from bot.execution", "import bot.execution"]
        for word in forbidden:
            self.assertNotIn(word, source, f"Forbidden import/call found: {word}")

    def test_no_execution_imports_in_observe(self):
        import importlib
        import inspect
        mod = importlib.import_module("bot.strategy.bidirectional_observe")
        source = inspect.getsource(mod)
        forbidden = ["place_order", "market_order", "limit_order", "attempt_entry",
                     "from bot.execution", "import bot.execution"]
        for word in forbidden:
            self.assertNotIn(word, source, f"Forbidden import/call found: {word}")

    def test_no_execution_imports_in_features(self):
        import importlib
        import inspect
        mod = importlib.import_module("bot.research.features")
        source = inspect.getsource(mod)
        forbidden = ["place_order", "market_order", "limit_order", "attempt_entry",
                     "from bot.execution", "import bot.execution"]
        for word in forbidden:
            self.assertNotIn(word, source, f"Forbidden import/call found: {word}")

    def test_shadow_writes_only_to_shadow_tables(self):
        """Verify shadow only writes to bidirectional_shadow_* tables."""
        import importlib
        import inspect
        mod = importlib.import_module("bot.strategy.bidirectional_shadow")
        source = inspect.getsource(mod)
        # Find all SQL INSERT/UPDATE statements
        import re
        inserts = re.findall(r"(?:INSERT INTO|UPDATE)\s+(\w+)", source)
        for table in inserts:
            self.assertTrue(
                table.startswith("bidirectional_shadow"),
                f"Shadow writes to non-shadow table: {table}",
            )


class ShadowOneTradePerMarketTestCase(unittest.TestCase):
    """Strict one trade per market_slug — never re-enter after any prior trade."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_has_shadow_trade_blocks_second_open(self) -> None:
        from bot.strategy.bidirectional_momentum import DirectionDecision
        from bot.strategy.bidirectional_shadow import (
            close_shadow_trade,
            ensure_tables,
            has_shadow_trade,
            open_shadow_trade,
        )

        decision = DirectionDecision(
            decision="YES",
            confidence=0.7,
            probability_yes=0.7,
            probability_no=0.3,
            regime="NORMAL",
            reason="test",
            features={"btc_move_30s": 10.0},
        )
        slug = "btc-updown-5m-test"

        with connect(self.db_path) as conn:
            ensure_tables(conn)
            first_id = open_shadow_trade(conn, decision, slug, 1000, 0.40, 1100)
            self.assertIsNotNone(first_id)
            self.assertTrue(has_shadow_trade(conn, slug))

            close_shadow_trade(conn, first_id, 0.44, "TRAILING_STOP", 10.0, 30.0)
            self.assertFalse(
                conn.execute(
                    "SELECT 1 FROM bidirectional_shadow_trades WHERE market_slug=? AND status='open'",
                    (slug,),
                ).fetchone()
            )

            second_id = open_shadow_trade(conn, decision, slug, 1000, 0.41, 1200)
            self.assertIsNone(second_id)
            count = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_trades WHERE market_slug=?",
                (slug,),
            ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_unique_index_prevents_duplicate_insert(self) -> None:
        from bot.strategy.bidirectional_momentum import DirectionDecision
        from bot.strategy.bidirectional_shadow import ensure_tables, open_shadow_trade

        decision = DirectionDecision(
            decision="NO",
            confidence=0.7,
            probability_yes=0.3,
            probability_no=0.7,
            regime="MOMENTUM",
            reason="test",
            features={"btc_move_30s": 12.0},
        )
        slug = "btc-updown-5m-unique"

        with connect(self.db_path) as conn:
            ensure_tables(conn)
            open_shadow_trade(conn, decision, slug, 2000, 0.42, 2100)
            conn.execute(
                """
                UPDATE bidirectional_shadow_trades
                SET status='closed', exit_price=0.38, exit_reason='STOP_LOSS',
                    pnl_pct=-9.5, holding_time_seconds=20
                WHERE market_slug=?
                """,
                (slug,),
            )
            conn.commit()
            second = open_shadow_trade(conn, decision, slug, 2000, 0.43, 2200)
            self.assertIsNone(second)


class ImportRegressionTestCase(unittest.TestCase):
    """Ensure legacy strategy and bidirectional modules coexist."""

    def test_legacy_strategy_import(self):
        from bot.strategy import SignalSide, StrategySignal, evaluate
        self.assertEqual(SignalSide.YES.value, "BUY_YES")
        self.assertTrue(callable(evaluate))

    def test_bidirectional_momentum_import(self):
        from bot.strategy.bidirectional_momentum import EntryConfig, evaluate_direction
        self.assertTrue(callable(evaluate_direction))

    def test_bidirectional_shadow_import(self):
        from bot.strategy.bidirectional_shadow import shadow_state, SHADOW_ENTRY_CONFIG
        self.assertIsNotNone(SHADOW_ENTRY_CONFIG)

    def test_bidirectional_observe_import(self):
        from bot.strategy.bidirectional_observe import observe_market
        self.assertTrue(callable(observe_market))


class HealthCheckTestCase(unittest.TestCase):
    def test_health_check_runs(self):
        from bot.bidirectional_check import run_check
        # Should not crash on empty DB
        result = run_check()
        self.assertIn(result, (0, 1))


if __name__ == "__main__":
    unittest.main()
