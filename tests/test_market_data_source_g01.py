"""Phase G.0.1 — Data source repair tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.candidate_g31 import _volume_score
from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    SymbolMarketDataG01,
    upsert_candles_g01,
    _volume_score_preview,
)
from bot.research.market_events.signal_intelligence.recorder_g3 import collect_snapshot_g3


class DataSourceG01Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g01.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_upsert_candles_stores_volume(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            bars = [
                CandleBar(open_ts=now - 300 * i, open=100, high=101, low=99, close=100, volume=10.0 + i)
                for i in range(10, 0, -1)
            ]
            n = upsert_candles_g01(conn, symbol="BTC", bars=bars, source="test")
            conn.commit()
            self.assertEqual(n, 10)
            row = conn.execute(
                """
                SELECT volume FROM market_events_historical_candles
                WHERE symbol = 'BTC' ORDER BY open_ts DESC LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertGreater(float(row["volume"]), 0)

    def test_volume_scores_differ_by_symbol(self) -> None:
        high = [
            CandleBar(open_ts=i, open=1, high=1, low=1, close=1, volume=100 if i >= 5 else 20)
            for i in range(10)
        ]
        low = [
            CandleBar(open_ts=i, open=1, high=1, low=1, close=1, volume=10 if i >= 5 else 50)
            for i in range(10)
        ]
        self.assertNotEqual(_volume_score(high), _volume_score(low))

    def test_volume_preview(self) -> None:
        bars = [
            CandleBar(open_ts=i, open=1, high=1, low=1, close=1, volume=50 if i >= 5 else 10)
            for i in range(10)
        ]
        raw, ratio, score = _volume_score_preview(bars)
        self.assertGreater(raw, 0)
        self.assertGreater(ratio, 0)
        self.assertGreater(score, 0)

    def test_collect_snapshot_has_funding_oi(self) -> None:
        mock_btc = SymbolMarketDataG01(
            symbol="BTC",
            price=65000.0,
            funding=0.0001,
            open_interest=100000.0,
            bars=[CandleBar(open_ts=int(time.time()), open=1, high=1, low=1, close=65000, volume=50)] * 20,
            source="binance_futures",
        )
        with patch(
            "bot.research.market_events.signal_intelligence.market_data_source_g01.fetch_symbol_market_data_g01",
            return_value=mock_btc,
        ):
            with patch(
                "bot.research.market_events.signal_intelligence.market_data_source_g01.fetch_btc_global_metrics_g01",
                return_value=mock_btc,
            ):
                with self._conn() as conn:
                    apply_migrations(conn)
                    payload = collect_snapshot_g3(conn)
                    self.assertIsNotNone(payload.funding)
                    self.assertIsNotNone(payload.open_interest)


if __name__ == "__main__":
    unittest.main()
