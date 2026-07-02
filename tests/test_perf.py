"""Performance optimization tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.perf.feature_store import load_enriched_features, sync_trade_features_incremental
from bot.perf.market_cache import MarketDataCache
from bot.trading_brain.learning import run_brain_learning
from tests.ai_agent_helpers import seed_trades


class PerfOptimizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_incremental_feature_cache(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 6)
            conn.commit()
            n1, _ = sync_trade_features_incremental(conn)
            conn.commit()
            n2, _ = sync_trade_features_incremental(conn)
            rows = load_enriched_features(conn, sync=False)
        self.assertEqual(n1, 6)
        self.assertEqual(n2, 0)
        self.assertEqual(len(rows), 6)

    def test_market_cache_batch_load(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 4)
            conn.commit()
            trades = conn.execute(
                "SELECT * FROM early_reversion_v2_trades WHERE status = 'closed'"
            ).fetchall()
            cache = MarketDataCache.build(conn, trades)
            for trade in trades:
                price = cache.btc_price_at(int(trade["entry_ts"]))
                self.assertIsNotNone(price)

    def test_brain_incremental_second_run_faster(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 8)
            conn.commit()
            run_brain_learning(conn)
            conn.commit()
            t0 = time.perf_counter()
            summary = run_brain_learning(conn)
            elapsed = time.perf_counter() - t0
        self.assertEqual(summary["new_trades_since_last"], 0)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
