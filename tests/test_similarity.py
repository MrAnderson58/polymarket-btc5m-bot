"""Tests for Similar Trades Engine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.ai_agent.features import build_signal_features, enrich_for_similarity
from bot.ai_agent.similarity import SimilarTradesEngine
from bot.database import connect, init_db
from tests.ai_agent_helpers import seed_trades


class SimilarityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_knn_returns_stats(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 5)
            conn.commit()
            trades = conn.execute(
                "SELECT * FROM early_reversion_v2_trades ORDER BY entry_ts ASC"
            ).fetchall()
            historical = []
            engine = SimilarTradesEngine(k=100)
            last = trades[-1]
            for t in trades[:-1]:
                row = enrich_for_similarity(build_signal_features(conn, t))
                historical.append({**row, "pnl": row["pnl"]})
            engine.fit(historical)
            query = enrich_for_similarity(build_signal_features(conn, last))
            stats = engine.query(query, k=100)
        self.assertGreater(stats["similar_count"], 0)
        self.assertIn("win_rate", stats)
        self.assertIn("profit_factor", stats)
