"""BTC filter analysis batch performance tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.optimizer.dataset import load_trade_features
from bot.perf.market_cache import MarketDataCache
from bot.report.analytics import build_btc_filter_analysis
from tests.ai_agent_helpers import seed_trades


class BtcFilterAnalysisTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_build_btc_filter_analysis_uses_cache(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 8)
            conn.commit()
            closed = conn.execute(
                "SELECT * FROM early_reversion_v2_trades WHERE status = 'closed'"
            ).fetchall()
            cache = MarketDataCache.build(conn, closed)
            features = {int(r["trade_id"]): dict(r) for r in load_trade_features(conn)}

            t0 = time.perf_counter()
            result = build_btc_filter_analysis(
                closed, cache=cache, feature_by_trade_id=features
            )
            elapsed = time.perf_counter() - t0

        self.assertIn("rows", result)
        self.assertIn("current_thresholds", result)
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
