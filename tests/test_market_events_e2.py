"""Phase E.2 multi-asset extension tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bot.research.market_events.basis_monitor import compute_basis_bps, observe_basis
from bot.research.market_events.cross_asset_classifier import classify_cross_asset
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.entity_graph import seed_entity_registry
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.instrument_master import InstrumentRecord, load_active_instruments, upsert_instrument
from bot.research.market_events.instrument_types import (
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    CROSS_ASSET_SPECIFIC,
    CROSS_TOKENIZED_DISLOCATION,
    HOURS_US_EQUITY,
    INST_TYPE_SYNTHETIC_PERPETUAL,
    SESSION_US_REGULAR,
    SESSION_WEEKEND,
    TIER_CORE,
    VENUE_BYBIT_LINEAR,
)
from bot.research.market_events.session_regime import classify_session_regime
from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger
from bot.research.market_events.universe import select_universe


class MarketEventsE2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e2.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v2_migrations(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertTrue(any("v2" in a for a in applied) or SCHEMA_VERSION == 2)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_events)").fetchall()}
            for col in ("instrument_id", "asset_class", "session_regime", "basis_bps", "cross_classification"):
                self.assertIn(col, cols)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_instruments", tables)

    def test_instrument_upsert_and_universe(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = upsert_instrument(
                conn,
                InstrumentRecord(
                    venue=VENUE_BYBIT_LINEAR,
                    venue_symbol="TSLAUSDT",
                    canonical_asset="TSLA",
                    reference_asset="TSLA",
                    asset_class=ASSET_CLASS_EQUITY,
                    instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
                    trading_hours_mode=HOURS_US_EQUITY,
                    price_source="bybit_last",
                    reference_price_source="bybit_index",
                    liquidity_tier=TIER_CORE,
                    active=1,
                ),
            )
            self.assertGreater(iid, 0)
            active = load_active_instruments(conn)
            self.assertEqual(len(active), 1)
            symbols, tag = select_universe(conn, mode="multi")
            self.assertIn("TSLA", symbols)
            self.assertTrue(tag.startswith("multi-"))

    def test_session_regime_us_regular(self) -> None:
        et = ZoneInfo("America/New_York")
        # Wed 2026-07-08 11:00 ET
        dt = datetime(2026, 7, 8, 11, 0, tzinfo=et)
        ts = int(dt.timestamp())
        regime = classify_session_regime(
            ts, asset_class=ASSET_CLASS_EQUITY, trading_hours_mode=HOURS_US_EQUITY,
        )
        self.assertEqual(regime, SESSION_US_REGULAR)

    def test_session_regime_weekend(self) -> None:
        et = ZoneInfo("America/New_York")
        dt = datetime(2026, 7, 11, 12, 0, tzinfo=et)  # Saturday
        ts = int(dt.timestamp())
        regime = classify_session_regime(
            ts, asset_class=ASSET_CLASS_EQUITY, trading_hours_mode=HOURS_US_EQUITY,
        )
        self.assertEqual(regime, SESSION_WEEKEND)

    def test_basis_monitor(self) -> None:
        basis = compute_basis_bps(105.0, 100.0)
        self.assertAlmostEqual(basis or 0, 500.0, places=1)
        obs = observe_basis(trade_price=102.0, reference_price=100.0, bid=101.5, ask=102.5)
        self.assertIsNotNone(obs.spread_bps)

    def test_cross_asset_dislocation(self) -> None:
        shock = ShockCandidate(
            symbol="TSLA",
            direction="DOWN",
            event_ts=int(time.time()),
            detected_ts=int(time.time()),
            return_pct=-5.0,
            velocity=-1.0,
            acceleration=0.0,
            volume_zscore=None,
            market_return_pct=0.0,
            btc_return_pct=0.0,
            relative_return_pct=-5.0,
            confidence=0.8,
            triggers=[ShockTrigger("SHOCK_A", 60, -5.0)],
            raw_metrics={},
        )
        label = classify_cross_asset(
            shock,
            asset_class=ASSET_CLASS_EQUITY,
            canonical_asset="TSLA",
            basis_bps=200.0,
            reference_return_pct=-0.3,
            peer_returns={"NVDA": -0.2, "AAPL": 0.1},
            btc_return_pct=None,
            median_crypto_return=None,
        )
        self.assertEqual(label, CROSS_TOKENIZED_DISLOCATION)

    def test_cross_asset_specific_equity(self) -> None:
        shock = ShockCandidate(
            symbol="TSLA",
            direction="DOWN",
            event_ts=int(time.time()),
            detected_ts=int(time.time()),
            return_pct=-6.0,
            velocity=-1.0,
            acceleration=0.0,
            volume_zscore=None,
            market_return_pct=0.0,
            btc_return_pct=0.0,
            relative_return_pct=-6.0,
            confidence=0.8,
            triggers=[ShockTrigger("SHOCK_A", 60, -6.0)],
            raw_metrics={},
        )
        label = classify_cross_asset(
            shock,
            asset_class=ASSET_CLASS_EQUITY,
            canonical_asset="TSLA",
            basis_bps=20.0,
            reference_return_pct=-5.5,
            peer_returns={"NVDA": -0.2, "QQQ": 0.1},
            btc_return_pct=None,
            median_crypto_return=None,
        )
        self.assertEqual(label, CROSS_ASSET_SPECIFIC)

    def test_entity_graph_seed(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            n = seed_entity_registry(conn)
            self.assertGreater(n, 0)
            row = conn.execute(
                "SELECT * FROM market_events_entity_registry WHERE canonical_asset='TSLA'",
            ).fetchone()
            self.assertIsNotNone(row)

    @patch("bot.research.market_events.instrument_discovery.BinanceDiscoveryClient")
    @patch("bot.research.market_events.instrument_discovery.BybitMarketClient")
    def test_discovery_mock(self, mock_bybit_cls, mock_binance_cls) -> None:
        from bot.research.market_events.instrument_discovery import run_instrument_discovery
        from bot.research.market_events.venue_binance_discovery import BinanceInstrument
        from bot.research.market_events.venue_bybit import BybitInstrument, BybitTicker

        mock_binance = mock_binance_cls.return_value
        mock_binance.fetch_perpetual_instruments.return_value = [
            BinanceInstrument("BTCUSDT", "BTC", "USDT", "PERPETUAL", "TRADING"),
        ]
        mock_binance.fetch_ticker_24h.return_value = {"quoteVolume": "100000000"}

        mock_bybit = mock_bybit_cls.return_value
        mock_bybit.fetch_instruments.return_value = []
        mock_bybit.fetch_ticker.return_value = BybitTicker(
            symbol="TSLAUSDT", last_price=250.0, index_price=248.0,
            mark_price=249.0, bid=249.5, ask=250.5, volume_24h=1e6, turnover_24h=5e8, ts=int(time.time()),
        )

        with self._conn() as conn:
            apply_migrations(conn)
            result = run_instrument_discovery(conn, enable_tradfi=False)
            self.assertGreaterEqual(result.binance_count, 1)
            btc = conn.execute(
                "SELECT * FROM market_events_instruments WHERE canonical_asset='BTC'",
            ).fetchone()
            self.assertIsNotNone(btc)
            self.assertEqual(btc["asset_class"], ASSET_CLASS_CRYPTO)

    def test_universe_core_unchanged(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            symbols, tag = select_universe(conn, mode="core")
            self.assertIn("BTC", symbols)
            self.assertTrue(tag.startswith("core-"))


if __name__ == "__main__":
    unittest.main()
