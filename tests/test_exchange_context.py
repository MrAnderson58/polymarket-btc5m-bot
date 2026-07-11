"""F.0 exchange context capture tests."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.exchange_context import (
    capture_exchange_context,
    exchange_context_report,
    fetch_exchange_metrics,
)
from bot.research.market_events.signal_intelligence.exchange_resolver import (
    ExchangeResolution,
    persist_resolution,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


class ExchangeContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_resolved(self, conn, symbol: str = "SUI", venue: str = "binance") -> None:
        persist_resolution(conn, ExchangeResolution(
            symbol, venue, f"{symbol}USDT", "RESOLVED",
            [{"venue": venue, "found": True}],
        ))

    def test_fetch_binance_metrics_mock(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.exchange_context._fetch_binance_context",
            return_value={"funding": 0.01, "volume_24h": 1e6, "last_price": 3.5},
        ):
            m = fetch_exchange_metrics("SUI", "binance", "SUIUSDT")
            self.assertEqual(m["funding"], 0.01)

    def test_capture_returns_none_when_unsupported(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            persist_resolution(conn, ExchangeResolution(
                "SUI", None, None, "UNSUPPORTED_SYMBOL", [],
            ))
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.get_or_resolve",
                return_value=ExchangeResolution("SUI", None, None, "UNSUPPORTED_SYMBOL", []),
            ):
                result = capture_exchange_context(conn, event_id=eid, symbol="SUI")
            self.assertIsNone(result)

    def test_capture_persists_context(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            self._seed_resolved(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"funding": 0.03, "volume_24h": 5000, "spread_bps": 2.1},
            ):
                result = capture_exchange_context(conn, event_id=eid, symbol="SUI")
            self.assertIsNotNone(result)
            row = conn.execute(
                "SELECT * FROM market_event_exchange_context WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(float(row["funding"]), 0.03)

    def test_vwap_atr_ema_computed(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            self._seed_resolved(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"funding": 0.01},
            ):
                capture_exchange_context(conn, event_id=eid, symbol="SUI")
            row = conn.execute(
                "SELECT vwap, atr, ema20_distance_pct FROM market_event_exchange_context WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row["vwap"])
            self.assertIsNotNone(row["atr"])

    def test_exchange_context_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = exchange_context_report(conn)
            self.assertIn("EXCHANGE CONTEXT", text)

    def test_raw_json_stored(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            self._seed_resolved(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"funding": 0.02},
            ):
                capture_exchange_context(conn, event_id=eid, symbol="SUI")
            row = conn.execute(
                "SELECT raw_json FROM market_event_exchange_context WHERE event_id = ?",
                (eid,),
            ).fetchone()
            raw = json.loads(row["raw_json"])
            self.assertIn("metrics", raw)

    def test_bybit_venue_key(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            persist_resolution(conn, ExchangeResolution(
                "BTC", "bybit", "BTCUSDT", "RESOLVED", [],
            ))
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"volume_24h": 100},
            ):
                capture_exchange_context(conn, event_id=eid, symbol="BTC")
            row = conn.execute(
                "SELECT venue FROM market_event_exchange_context WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(row["venue"], "bybit")

    def test_okx_metrics_path(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.okx_client.fetch_ticker_metrics",
            return_value={"funding": 0.005},
        ):
            m = fetch_exchange_metrics("ETH", "okx", "ETH-USDT-SWAP")
        self.assertEqual(m["funding"], 0.005)

    def test_unique_event_venue(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            self._seed_resolved(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"funding": 0.01},
            ):
                capture_exchange_context(conn, event_id=eid, symbol="SUI")
                capture_exchange_context(conn, event_id=eid, symbol="SUI")
            n = conn.execute(
                "SELECT COUNT(*) FROM market_event_exchange_context WHERE event_id = ?",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(int(n), 1)

    def test_report_lists_rows(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn)
            self._seed_resolved(conn)
            with patch(
                "bot.research.market_events.signal_intelligence.exchange_context.fetch_exchange_metrics",
                return_value={"funding": 0.04, "spread_bps": 3.0},
            ):
                capture_exchange_context(conn, event_id=eid, symbol="SUI")
            text = exchange_context_report(conn, days=30)
            self.assertIn("SUI", text)


if __name__ == "__main__":
    unittest.main()
