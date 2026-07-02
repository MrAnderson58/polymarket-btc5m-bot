"""Trading Intelligence cache and batch context tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.analytics.intelligence import compute_trading_intelligence, load_trading_intelligence
from bot.analytics.intelligence_cache import save_intelligence_cache
from bot.database import connect, init_db
from tests.ai_agent_helpers import seed_trades


class IntelligenceCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.cache_dir = Path(self._tmpdir.name) / "intelligence_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_compute_and_load_intelligence(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 5)
            conn.commit()
            closed = conn.execute(
                "SELECT * FROM early_reversion_v2_trades WHERE status = 'closed'"
            ).fetchall()
            report = {"entry_price_analysis": {"rows": []}, "drift_detector": {}}
            sections = compute_trading_intelligence(conn, closed, report)
            self.assertIn("false_stop_detector", sections)
            self.assertIn("regime_engine", sections)

        with mock.patch("bot.analytics.intelligence_cache.CACHE_DIR", self.cache_dir):
            with mock.patch(
                "bot.analytics.intelligence_cache.RESULTS_PATH",
                self.cache_dir / "intelligence_results.json",
            ):
                save_intelligence_cache(sections)
                loaded = load_trading_intelligence()
                self.assertTrue(loaded.get("intelligence_cache_available"))
                self.assertIn("execution_audit", loaded)


if __name__ == "__main__":
    unittest.main()
