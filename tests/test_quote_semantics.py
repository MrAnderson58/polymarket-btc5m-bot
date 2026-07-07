"""Regression tests for bid/ask semantics in research paths."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import init_db, insert_v4_shadow_observation
from bot.research.features import load_market_observations
from bot.research.quote_semantics import (
    normalize_observation_quotes,
    normalize_quote_pair,
    observation_quotes_reversed,
)
from bot.research.strategy_simulator.simulator import (
    VirtualTrade,
    simulate_strategy_on_market,
)
from bot.research.strategy_simulator.strategies import Strategy


def _synthetic_book(*, yes_bid: float, yes_ask: float, no_bid: float, no_ask: float) -> list[dict]:
    """Canonical order book: bid < ask, spread >= 0."""
    ws = 1_900_000_000
    slug = f"btc-updown-5m-{ws}"
    rows: list[dict] = []
    for i in range(20):
        ts = ws + i * 3
        rows.append({
            "market_slug": slug,
            "window_start_ts": ws,
            "timestamp": ts,
            "seconds_from_start": i * 3,
            "seconds_left": 300 - i * 3,
            "btc_price": 100_050.0,
            "strike": 100_000.0,
            "delta": 50.0,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "trend_score": None,
            "trend_side": None,
            "spread": yes_ask - yes_bid,
        })
    # TP hit on tick 10
    rows[10]["yes_bid"] = 0.62
    return rows


def _scanner_reversed_book(yes_bid: float, yes_ask: float) -> list[dict]:
    """Simulate market_scanner mis-labeling (SELL→bid, BUY→ask)."""
    raw = _synthetic_book(
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=0.38,
        no_ask=0.39,
    )
    for row in raw:
        row["yes_bid"], row["yes_ask"] = row["yes_ask"], row["yes_bid"]
        row["spread"] = row["yes_ask"] - row["yes_bid"]
    return raw


class QuoteSemanticsTestCase(unittest.TestCase):
    def test_normalize_quote_pair_swaps_when_reversed(self) -> None:
        pair = normalize_quote_pair(0.33, 0.32)
        self.assertEqual(pair.bid, 0.32)
        self.assertEqual(pair.ask, 0.33)
        self.assertAlmostEqual(pair.spread, 0.01)

    def test_canonical_book_bid_lt_ask(self) -> None:
        pair = normalize_quote_pair(0.32, 0.33)
        self.assertEqual(pair.bid, 0.32)
        self.assertEqual(pair.ask, 0.33)
        self.assertFalse(pair.is_reversed)

    def test_observation_reversed_detection(self) -> None:
        self.assertTrue(observation_quotes_reversed({"yes_bid": 0.33, "yes_ask": 0.32}))
        self.assertFalse(observation_quotes_reversed({"yes_bid": 0.32, "yes_ask": 0.33}))

    def test_normalize_observation_fixes_spread(self) -> None:
        raw = {"yes_bid": 0.33, "yes_ask": 0.32, "no_bid": 0.68, "no_ask": 0.67}
        fixed = normalize_observation_quotes(raw)
        self.assertEqual(fixed["yes_bid"], 0.32)
        self.assertEqual(fixed["yes_ask"], 0.33)
        self.assertAlmostEqual(fixed["spread"], 0.01)

    def test_simulator_entry_ask_exit_bid_canonical(self) -> None:
        path = _synthetic_book(yes_bid=0.32, yes_ask=0.33, no_bid=0.66, no_ask=0.67)
        strategy = Strategy(
            direction="YES",
            max_entry=0.35,
            min_delta=25.0,
            max_delta=100.0,
            max_spread=0.02,
            min_seconds_left=60,
            tp=0.60,
        )
        trades = simulate_strategy_on_market(
            "btc-updown-5m-1900000000", path, strategy, one_trade_per_market=True,
        )
        self.assertEqual(len(trades), 1)
        t: VirtualTrade = trades[0]
        self.assertEqual(t.entry_price, 0.33)
        self.assertEqual(t.exit_price, 0.60)
        self.assertTrue(t.won)
        self.assertGreaterEqual(t.spread_at_entry or 0, 0.0)

    def test_simulator_correct_after_normalize_reversed_storage(self) -> None:
        raw = _scanner_reversed_book(0.32, 0.33)
        path = [normalize_observation_quotes(r) for r in raw]
        path[10]["yes_bid"] = 0.62
        strategy = Strategy(
            direction="YES",
            max_entry=0.35,
            min_delta=25.0,
            max_delta=100.0,
            max_spread=0.02,
            min_seconds_left=60,
            tp=0.60,
        )
        trades = simulate_strategy_on_market(
            "btc-updown-5m-1900000000", path, strategy, one_trade_per_market=True,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_price, 0.33)
        self.assertEqual(trades[0].exit_price, 0.60)

    def test_load_market_observations_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            init_db(db_path)
            ws = 1_900_000_100
            slug = f"btc-updown-5m-{ws}"
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                insert_v4_shadow_observation(
                    conn,
                    market_slug=slug,
                    window_start_ts=ws,
                    timestamp=ws + 1,
                    seconds_from_start=1,
                    seconds_left=299,
                    btc_price=100_000.0,
                    strike=100_000.0,
                    delta=0.0,
                    yes_bid=0.33,
                    yes_ask=0.32,
                    no_bid=0.68,
                    no_ask=0.67,
                    trend_score=None,
                    trend_side=None,
                    spread=-0.01,
                )
                conn.commit()
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                loaded = load_market_observations(conn, slug)
            self.assertEqual(loaded[0]["yes_bid"], 0.32)
            self.assertEqual(loaded[0]["yes_ask"], 0.33)
            self.assertAlmostEqual(loaded[0]["spread"], 0.01)


if __name__ == "__main__":
    unittest.main()
