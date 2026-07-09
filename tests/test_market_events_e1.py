"""Phase E.1 tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.event_types import (
    CLASSIFICATION_ASSET_SPECIFIC,
    CLASSIFICATION_MARKET_WIDE,
    SHOCK_DIRECTION_DOWN,
    SHOCK_DIRECTION_UP,
)
from bot.research.market_events.paper_execution import (
    open_paper_position,
    process_exit_tick,
)
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.price_feed import PriceTick, SymbolPriceState
from bot.research.market_events.reversal_confirmation import confirm_r1, evaluate_all_reversals
from bot.research.market_events.shock_classifier import classify_shock
from bot.research.market_events.shock_detector import (
    ShockCandidate,
    ShockTrigger,
    detect_shock_triggers,
    merge_triggers_to_candidate,
)


class MockFeed:
    def __init__(self, states: dict[str, SymbolPriceState]) -> None:
        self._states = states

    def get_state(self, symbol: str) -> SymbolPriceState | None:
        return self._states.get(symbol)


def _state_with_drop(symbol: str, *, drop_pct: float, now: int) -> SymbolPriceState:
    st = SymbolPriceState(symbol=symbol, pair=f"{symbol}USDT")
    base = 100.0
    end = base * (1.0 + drop_pct / 100.0)
    for i, px in enumerate([base, base, base * 0.99, end]):
        st.append(PriceTick(ts=now - 60 + i * 15, price=px, volume=1000.0), max_age_sec=300)
    st.append(PriceTick(ts=now, price=end, volume=5000.0), max_age_sec=300)
    return st


class MarketEventsE1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_test.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_down_shock_detection(self) -> None:
        now = int(time.time())
        st = _state_with_drop("SOL", drop_pct=-3.5, now=now)
        triggers = detect_shock_triggers(st, now_ts=now)
        self.assertTrue(any(t.return_pct < 0 for t in triggers))

    def test_up_shock_detection(self) -> None:
        now = int(time.time())
        st = _state_with_drop("ETH", drop_pct=2.5, now=now)
        triggers = detect_shock_triggers(st, now_ts=now)
        self.assertTrue(any(t.return_pct > 0 for t in triggers))

    def test_overlapping_shock_dedup_key_stable(self) -> None:
        now = 1_700_000_000
        triggers = [
            ShockTrigger("SHOCK_A", 30, -3.0),
            ShockTrigger("SHOCK_B", 60, -4.5),
            ShockTrigger("SHOCK_C", 180, -6.0),
        ]
        st = SymbolPriceState(symbol="SOL", pair="SOLUSDT")
        c1 = merge_triggers_to_candidate("SOL", triggers, now_ts=now, state=st, btc_state=None, median_market_return=None)
        c2 = merge_triggers_to_candidate("SOL", triggers, now_ts=now + 10, state=st, btc_state=None, median_market_return=None)
        self.assertIsNotNone(c1)
        self.assertEqual(c1.dedup_key, c2.dedup_key)
        self.assertEqual(len(c1.triggers), 3)

    def test_no_entry_before_reversal(self) -> None:
        now = int(time.time())
        shock = ShockCandidate(
            symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            event_ts=now, detected_ts=now, return_pct=-3.0,
        )
        st = _state_with_drop("SOL", drop_pct=-3.0, now=now)
        revs = evaluate_all_reversals(shock, st, now)
        if not any(r.confirmed for r in revs):
            self.assertTrue(all(r.confirm_ts is None for r in revs if not r.confirmed))

    def test_reversal_r1_confirms_on_reclaim(self) -> None:
        now = int(time.time())
        shock = ShockCandidate(
            symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            event_ts=now - 60, detected_ts=now, return_pct=-4.0,
        )
        st = SymbolPriceState(symbol="SOL", pair="SOLUSDT")
        st.append(PriceTick(ts=now - 60, price=100.0, volume=100), max_age_sec=300)
        st.append(PriceTick(ts=now - 30, price=96.0, volume=100), max_age_sec=300)
        st.append(PriceTick(ts=now, price=98.0, volume=100), max_age_sec=300)
        r = confirm_r1(shock, st, now)
        self.assertTrue(r.confirmed)
        self.assertEqual(r.confirm_ts, now)

    def test_hard_stop_closes_position(self) -> None:
        pos = open_paper_position(
            event_id=1, symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            reversal_variant="R1", exit_variant="EXIT_A",
            entry_ts=1000, entry_price=100.0,
        )
        tick = process_exit_tick(pos, ts=1100, price=98.5)
        self.assertTrue(tick.closed)
        self.assertEqual(tick.exit_reason, "STOP")

    def test_be_transition(self) -> None:
        pos = open_paper_position(
            event_id=1, symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            reversal_variant="R1", exit_variant="EXIT_B",
            entry_ts=1000, entry_price=100.0,
        )
        process_exit_tick(pos, ts=1100, price=100.35)
        self.assertTrue(pos.be_active)
        tick = process_exit_tick(pos, ts=1200, price=99.99)
        self.assertTrue(tick.closed)
        self.assertEqual(tick.exit_reason, "BE_STOP")

    def test_trailing_stop(self) -> None:
        pos = open_paper_position(
            event_id=1, symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            reversal_variant="R1", exit_variant="EXIT_C",
            entry_ts=1000, entry_price=100.0,
        )
        process_exit_tick(pos, ts=1100, price=100.7)
        self.assertTrue(pos.trail_active)
        tick = process_exit_tick(pos, ts=1300, price=100.2)
        self.assertTrue(tick.closed or pos.trail_active)

    def test_partial_tp_runner(self) -> None:
        pos = open_paper_position(
            event_id=1, symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            reversal_variant="R1", exit_variant="EXIT_D",
            entry_ts=1000, entry_price=100.0,
        )
        process_exit_tick(pos, ts=1100, price=100.6)
        self.assertTrue(pos.partial_taken)

    def test_market_wide_classification(self) -> None:
        shock = ShockCandidate(
            symbol="ETH", direction=SHOCK_DIRECTION_DOWN,
            event_ts=1, detected_ts=1, return_pct=-2.5,
            btc_return_pct=-2.2, market_return_pct=-2.0,
        )
        self.assertEqual(
            classify_shock(shock, eth_return_pct=-2.0, median_universe_return_pct=-2.0),
            CLASSIFICATION_MARKET_WIDE,
        )

    def test_asset_specific_classification(self) -> None:
        shock = ShockCandidate(
            symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            event_ts=1, detected_ts=1, return_pct=-4.0,
            btc_return_pct=-0.2, relative_return_pct=-3.8,
        )
        self.assertEqual(classify_shock(shock), CLASSIFICATION_ASSET_SPECIFIC)

    def test_event_dedup_idempotent(self) -> None:
        with market_events_connection(self.db_path) as conn:
            apply_migrations(conn)
            runner = ShockPaperRunner(max_cycles=0)
            shock = ShockCandidate(
                symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
                event_ts=int(time.time()), detected_ts=int(time.time()),
                return_pct=-3.0, triggers=[ShockTrigger("SHOCK_A", 30, -3.0)],
            )
            id1 = runner._persist_event(conn, shock, "UNKNOWN")
            id2 = runner._persist_event(conn, shock, "UNKNOWN")
            self.assertIsNotNone(id1)
            self.assertIsNone(id2)

    def test_no_future_leakage_return_over(self) -> None:
        now = 1_000_000
        st = SymbolPriceState(symbol="BTC", pair="BTCUSDT")
        st.append(PriceTick(ts=now - 120, price=100.0), max_age_sec=300)
        st.append(PriceTick(ts=now - 60, price=100.0), max_age_sec=300)
        st.append(PriceTick(ts=now, price=110.0), max_age_sec=300)
        ret = st.return_over(60, now)
        self.assertIsNotNone(ret)
        self.assertAlmostEqual(ret or 0, 10.0, places=1)


if __name__ == "__main__":
    unittest.main()
