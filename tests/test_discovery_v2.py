"""Tests for Strategy Discovery v2 — causal features, archetypes, leakage safety."""

from __future__ import annotations

import unittest

from bot.research.strategy_simulator.archetype_context import ArchetypeMarketContext
from bot.research.strategy_simulator.archetype_simulator import simulate_archetype_on_context
from bot.research.strategy_simulator.archetypes import ArchetypeStrategy, matches_archetype
from bot.research.strategy_simulator.causal_features import build_causal_features
from bot.research.strategy_simulator.discovery_v2 import (
    gate_on_validation,
    select_diverse_candidates,
)
from bot.research.strategy_simulator.exit_models import ExitModel, ExitSpec, forward_exit
from bot.research.strategy_simulator.grid_v2 import generate_discovery_v2_grid, grid_summary
from bot.research.strategy_simulator.archetype_statistics import compute_archetype_stats
from bot.research.strategy_simulator.simulator import VirtualTrade


def _obs(
    ts: int,
    *,
    btc: float = 100_000.0,
    strike: float = 100_000.0,
    sl: int = 120,
    yes_bid: float = 0.45,
    yes_ask: float = 0.48,
    no_bid: float = 0.50,
    no_ask: float = 0.53,
) -> dict:
    return {
        "timestamp": ts,
        "btc_price": btc,
        "strike": strike,
        "seconds_left": sl,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
    }


def _rising_path(n: int = 12) -> list[dict]:
    base = 1_700_000_000
    path = []
    for i in range(n):
        path.append(_obs(
            base + i * 5,
            btc=100_000.0 + i * 5,
            strike=100_000.0,
            sl=300 - i * 20,
            yes_bid=0.40 + i * 0.01,
            yes_ask=0.42 + i * 0.01,
            no_bid=0.55 - i * 0.01,
            no_ask=0.57 - i * 0.01,
        ))
    return path


class CausalFeaturesTest(unittest.TestCase):
    def test_no_future_snapshot_leakage(self) -> None:
        path = _rising_path(10)
        feat = build_causal_features(path, 5, side="YES")
        assert feat is not None
        truncated = build_causal_features(path[:6], 5, side="YES")
        assert truncated is not None
        self.assertEqual(feat.btc_delta, truncated.btc_delta)
        self.assertEqual(feat.btc_velocity_5s, truncated.btc_velocity_5s)
        self.assertEqual(feat.token_mid, truncated.token_mid)

    def test_delta_velocity_computed(self) -> None:
        path = _rising_path(8)
        feat = build_causal_features(path, 6, side="YES")
        assert feat is not None
        self.assertIsNotNone(feat.btc_velocity_5s)
        self.assertGreater(feat.btc_velocity_5s, 0)

    def test_seconds_left_bucket(self) -> None:
        path = [_obs(100, sl=25)]
        feat = build_causal_features(path, 0, side="YES")
        assert feat is not None
        self.assertEqual(feat.seconds_left_bucket, "0-30")


class ExitModelTest(unittest.TestCase):
    def test_ask_entry_bid_exit_fixed_tp(self) -> None:
        path = [
            _obs(100, yes_bid=0.40, yes_ask=0.42),
            _obs(105, yes_bid=0.50, yes_ask=0.52),
            _obs(110, yes_bid=0.58, yes_ask=0.60),
        ]
        exit_price, exit_ts, won = forward_exit(
            path, 0,
            direction="YES",
            entry_price=0.42,
            spec=ExitSpec(model=ExitModel.FIXED_TP, tp=0.55),
        )
        self.assertTrue(won)
        self.assertEqual(exit_price, 0.55)
        self.assertEqual(exit_ts, 110)

    def test_settlement_exit(self) -> None:
        path = [
            _obs(100, btc=99_900, strike=100_000, yes_bid=0.40, yes_ask=0.42),
            _obs(300, btc=100_100, strike=100_000, yes_bid=0.90, yes_ask=0.92),
        ]
        exit_price, _, won = forward_exit(
            path, 0,
            direction="YES",
            entry_price=0.42,
            spec=ExitSpec(model=ExitModel.SETTLEMENT),
        )
        self.assertTrue(won)
        self.assertEqual(exit_price, 1.0)


class ArchetypeSymmetryTest(unittest.TestCase):
    def test_grid_includes_both_directions_per_archetype(self) -> None:
        grid = generate_discovery_v2_grid(archetypes=("momentum", "mean_reversion"))
        for arch in ("momentum", "mean_reversion"):
            dirs = {s.direction for s in grid if s.archetype == arch}
            self.assertIn("YES", dirs, arch)
            self.assertIn("NO", dirs, arch)

    def test_momentum_yes_requires_positive_delta(self) -> None:
        path = _rising_path(6)
        feat = build_causal_features(path, 5, side="YES")
        assert feat is not None
        strat = ArchetypeStrategy(
            archetype="momentum",
            direction="YES",
            exit_spec=ExitSpec(model=ExitModel.FIXED_TP, tp=0.60),
            max_entry=0.50,
            max_spread=0.05,
            min_seconds_left=45,
            min_velocity=0.1,
            velocity_window=5,
        )
        self.assertTrue(matches_archetype(feat, strat))

    def test_mean_reversion_no_requires_negative_velocity(self) -> None:
        path = [
            _obs(100, btc=100_050, strike=100_000, sl=120, no_ask=0.30),
            _obs(105, btc=100_040, strike=100_000, sl=115, no_ask=0.28),
            _obs(110, btc=100_020, strike=100_000, sl=110, no_ask=0.25),
        ]
        feat = build_causal_features(path, 2, side="NO")
        assert feat is not None
        strat = ArchetypeStrategy(
            archetype="mean_reversion",
            direction="NO",
            exit_spec=ExitSpec(model=ExitModel.FIXED_TP, tp=0.55),
            max_entry=0.35,
            max_spread=0.05,
            min_seconds_left=45,
            min_abs_delta=10,
            velocity_window=5,
        )
        self.assertTrue(matches_archetype(feat, strat))


class DiscoveryMethodologyTest(unittest.TestCase):
    def _stats(self, arch: str, direction: str, ev: float, trades: int) -> object:
        from bot.research.strategy_simulator.archetype_statistics import ArchetypeStats

        s = ArchetypeStrategy(
            archetype=arch,
            direction=direction,
            exit_spec=ExitSpec(model=ExitModel.FIXED_TP, tp=0.60),
            max_entry=0.30,
            max_spread=0.02,
            min_seconds_left=60,
        )
        return ArchetypeStats(
            strategy=s,
            trades=trades,
            expected_value=ev,
            profit_factor=1.5,
            win_rate=0.6,
        )

    def test_family_diversity_cap(self) -> None:
        ranked = [
            self._stats("momentum", "YES", 0.05, 40),
            self._stats("momentum", "YES", 0.04, 35),
            self._stats("momentum", "YES", 0.03, 32),
            self._stats("momentum", "YES", 0.02, 31),
            self._stats("mean_reversion", "NO", 0.04, 35),
        ]
        trades = {s.fingerprint: [] for s in ranked}
        selected = select_diverse_candidates(ranked, trades, top_n=10, max_per_family=2)
        momentum = [s for s in selected if s.strategy.archetype == "momentum"]
        self.assertLessEqual(len(momentum), 2)

    def test_train_only_ranking_preserved_after_val_gate(self) -> None:
        s_high = self._stats("momentum", "YES", 0.06, 40)
        s_low = self._stats("mean_reversion", "NO", 0.04, 40)
        candidates = [s_high, s_low]
        val_trades = {
            s_high.fingerprint: [],
            s_low.fingerprint: [
                VirtualTrade(
                    market_slug="m1", strategy_fp=s_low.fingerprint, direction="NO",
                    entry_ts=1, exit_ts=2, entry_price=0.2, exit_price=0.25,
                    pnl=0.05, won=True, holding_seconds=1,
                    btc_delta_at_entry=-10, seconds_left_at_entry=90, spread_at_entry=0.02,
                ),
            ],
        }
        passed, _ = gate_on_validation(candidates, val_trades, min_val_trades=1)
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0].fingerprint, s_low.fingerprint)


class ReproducibilityTest(unittest.TestCase):
    def test_grid_deterministic(self) -> None:
        a = [s.fingerprint() for s in generate_discovery_v2_grid()]
        b = [s.fingerprint() for s in generate_discovery_v2_grid()]
        self.assertEqual(a, b)

    def test_simulation_deterministic(self) -> None:
        path = _rising_path(10)
        specs = (ExitSpec(model=ExitModel.FIXED_TP, tp=0.55),)
        ctx = ArchetypeMarketContext.build("m1", path, exit_specs=specs)
        strat = ArchetypeStrategy(
            archetype="momentum",
            direction="YES",
            exit_spec=ExitSpec(model=ExitModel.FIXED_TP, tp=0.55),
            max_entry=0.50,
            max_spread=0.05,
            min_seconds_left=45,
            min_velocity=0.1,
            velocity_window=5,
        )
        t1 = simulate_archetype_on_context(ctx, strat, one_trade_per_market=True)
        t2 = simulate_archetype_on_context(ctx, strat, one_trade_per_market=True)
        self.assertEqual([(t.entry_ts, t.pnl) for t in t1], [(t.entry_ts, t.pnl) for t in t2])


class GridAuditTest(unittest.TestCase):
    def test_v2_broader_than_v1_hypothesis(self) -> None:
        gs = grid_summary()
        self.assertGreater(gs["total"], 11_200)
        self.assertGreaterEqual(len(gs["by_archetype"]), 4)


if __name__ == "__main__":
    unittest.main()
