"""Tests for V1.2 counterfactual research and parallel shadow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.bidirectional_live_audit import LiveTrade, dedupe_first_trade_per_market
from bot.research.bidirectional_v12_counterfactual import (
    analyze_quote_violations,
    evaluate_candidate,
    filter_trades,
    generate_candidates,
    pick_v12_winner,
    run_counterfactual,
    robustness_score,
)
from bot.strategy.bidirectional_v12_config import (
    CandidateSpec,
    V12_CANDIDATE_SPEC,
    passes_v12_filters,
)


def _make_trades(n: int = 60) -> list[LiveTrade]:
    trades: list[LiveTrade] = []
    base = 1_700_000_000
    for i in range(n):
        ws = base + i * 300
        entry_ts = ws + 60 + (i % 5) * 10
        side = "YES" if i % 2 == 0 else "NO"
        entry = 0.42 if side == "YES" else 0.40
        pnl = 8.0 if i % 3 else -6.0
        trades.append(
            LiveTrade(
                i + 1, f"btc-updown-5m-{1000 + i}", ws, side,
                entry, entry_ts, "NORMAL", 0.65,
                entry * 1.05, "TRAILING_STOP", pnl, 40, entry * 1.05,
                20.0 if side == "YES" else -15.0, 200,
            )
        )
    return trades


class CounterfactualTestCase(unittest.TestCase):
    def test_generate_candidates_count(self) -> None:
        cands = generate_candidates()
        self.assertGreaterEqual(len(cands), 20)
        names = {c.name for c in cands}
        self.assertIn("V1.1_baseline", names)
        self.assertIn("combo_robust_core", names)

    def test_filter_reduces_trades(self) -> None:
        trades = _make_trades(50)
        # Mix in YES entries inside excluded bucket
        trades[0] = LiveTrade(
            1, trades[0].market_slug, trades[0].window_start_ts, "YES",
            0.37, trades[0].entry_ts, "NORMAL", 0.65,
            0.40, "TRAIL", 5.0, 30, 0.40, 12.0, 200,
        )
        spec = CandidateSpec(name="excl035", yes_exclude_035_040=True)
        filtered = filter_trades(trades, spec)
        self.assertLess(len(filtered), len(trades))

    def test_robustness_prefers_stress_over_raw_pf(self) -> None:
        good_stress = evaluate_candidate(_make_trades(40))
        good_stress["stress_b_pf"] = 1.5
        good_stress["pf"] = 1.6
        good_stress["bootstrap_pp_gt1"] = 0.9
        high_pf = evaluate_candidate(_make_trades(40))
        high_pf["stress_b_pf"] = 0.9
        high_pf["pf"] = 2.5
        high_pf["bootstrap_pp_gt1"] = 0.5
        self.assertGreater(robustness_score(good_stress), robustness_score(high_pf))

    def test_run_counterfactual_returns_sorted(self) -> None:
        results = run_counterfactual(_make_trades(80))
        self.assertTrue(results)
        scores = [r.robustness for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_passes_v12_filters(self) -> None:
        self.assertTrue(passes_v12_filters(
            side="YES", entry_price=0.42, btc_move_30s=12.0,
            seconds_from_start=100, regime="NORMAL",
        ))
        self.assertFalse(passes_v12_filters(
            side="YES", entry_price=0.37, btc_move_30s=12.0,
            seconds_from_start=100, regime="NORMAL",
        ))


class V12ShadowParallelTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_v12_tables_and_one_trade_per_market(self) -> None:
        from bot.strategy.bidirectional_momentum import DirectionDecision
        from bot.strategy.bidirectional_shadow_v12 import (
            close_shadow_trade,
            ensure_tables,
            has_shadow_trade,
            open_shadow_trade,
        )

        decision = DirectionDecision(
            decision="YES", confidence=0.7, probability_yes=0.7, probability_no=0.3,
            regime="NORMAL", reason="test", features={"btc_move_30s": 12.0},
        )
        slug = "btc-updown-5m-v12"

        with connect(self.db_path) as conn:
            ensure_tables(conn)
            tid = open_shadow_trade(conn, decision, slug, 1000, 0.42, 1100)
            self.assertIsNotNone(tid)
            close_shadow_trade(conn, tid, 0.45, "TRAILING_STOP", 7.0, 30.0)
            second = open_shadow_trade(conn, decision, slug, 1000, 0.43, 1200)
            self.assertIsNone(second)
            self.assertTrue(has_shadow_trade(conn, slug))

    def test_v12_observe_does_not_touch_v11_tables(self) -> None:
        from bot.strategy.bidirectional_observe_v12 import observe_market_v12
        from bot.strategy.bidirectional_shadow import ensure_tables as ensure_v11

        with connect(self.db_path) as conn:
            ensure_v11(conn)
            v11_before = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_trades"
            ).fetchone()[0]
            observe_market_v12(
                conn,
                market_slug="btc-updown-5m-observe",
                window_start_ts=1_700_000_000,
                btc_price=60100,
                strike=60000,
                yes_bid=0.40,
                yes_ask=0.42,
                no_bid=0.58,
                no_ask=0.60,
                seconds_from_start=120,
                seconds_left=180,
            )
            conn.commit()
            v11_after = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_trades"
            ).fetchone()[0]
            v12_obs = conn.execute(
                "SELECT COUNT(*) FROM bidirectional_shadow_v12_observations"
            ).fetchone()[0]
        self.assertEqual(v11_before, v11_after)
        self.assertGreaterEqual(v12_obs, 0)


if __name__ == "__main__":
    unittest.main()
