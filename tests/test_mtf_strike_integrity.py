"""Tests for MTF strike enrichment and production integrity."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bot.database import connect, init_db
from bot.research.mtf.alignment import (
    is_15m_window_correct,
    is_1h_market_correct,
    parse_15m_slug_bounds,
)
from bot.research.mtf.discovery import slug_1h_at, slug_daily_at
from bot.research.mtf.metadata import (
    MarketMetadata,
    ensure_metadata_table,
    get_cached_metadata,
    upsert_metadata,
)
from bot.research.mtf.quote_audit import BID_GT_ASK, audit_tf_row
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot
from bot.research.mtf.strike_resolver import (
    BINANCE_OPEN,
    CHAINLINK_REFERENCE,
    UNKNOWN,
    StrikeResult,
    backfill_snapshot_strikes,
    get_or_resolve_strike,
    resolve_strike,
)

ET = ZoneInfo("America/New_York")


def _gamma_1h_event() -> dict:
    return {
        "title": "Bitcoin Up or Down - July 6, 1PM ET",
        "active": True,
        "closed": False,
        "endDate": "2026-07-06T18:00:00Z",
        "description": "Binance BTC/USDT 1 hour candle open/close",
        "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
        "markets": [{
            "description": "resolve Up if close >= open for BTC/USDT 1 hour candle on Binance",
            "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
            "endDate": "2026-07-06T18:00:00Z",
        }],
    }


def _gamma_15m_event(ws: int) -> dict:
    return {
        "title": "Bitcoin Up or Down 15m",
        "active": True,
        "closed": False,
        "endDate": datetime.fromtimestamp(ws + 900, tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": "Chainlink BTC/USD at beginning of range",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "markets": [{
            "eventStartTime": datetime.fromtimestamp(ws, tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "description": "Chainlink BTC/USD data stream",
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
            "endDate": datetime.fromtimestamp(ws + 900, tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }],
    }


class StrikeResolverTestCase(unittest.TestCase):
    def test_15m_unknown_before_window_start(self) -> None:
        ws = 1_700_000_000
        slug = f"btc-updown-15m-{ws}"
        result = resolve_strike(
            slug, "15m",
            snapshot_ts=ws - 10,
            event=_gamma_15m_event(ws),
        )
        self.assertIsNone(result.strike)
        self.assertEqual(result.strike_source, UNKNOWN)

    def test_15m_chainlink_required_without_credentials(self) -> None:
        ws = 1_700_000_100
        slug = f"btc-updown-15m-{ws}"
        result = resolve_strike(
            slug, "15m",
            snapshot_ts=ws + 60,
            event=_gamma_15m_event(ws),
        )
        self.assertIsNone(result.strike)
        self.assertEqual(result.strike_source, UNKNOWN)
        self.assertIn("Chainlink", result.resolution_notes)

    @patch("bot.research.mtf.strike_resolver._fetch_binance_kline_open")
    def test_1h_binance_open_after_window(self, mock_kline: MagicMock) -> None:
        ts = int(datetime(2026, 7, 6, 13, 30, tzinfo=ET).timestamp())
        ws = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        slug = slug_1h_at(ws)
        mock_kline.return_value = (98500.0, ws)
        result = resolve_strike(slug, "1h", snapshot_ts=ts, event=_gamma_1h_event())
        self.assertEqual(result.strike, 98500.0)
        self.assertEqual(result.strike_source, BINANCE_OPEN)
        self.assertLessEqual(result.strike_source_timestamp or 0, ts)

    def test_1h_no_future_strike(self) -> None:
        ws = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        slug = slug_1h_at(ws)
        result = resolve_strike(slug, "1h", snapshot_ts=ws - 60, event=_gamma_1h_event())
        self.assertIsNone(result.strike)
        self.assertEqual(result.strike_source, UNKNOWN)

    @patch("bot.research.mtf.strike_resolver._fetch_binance_1m_close_at")
    def test_daily_reference_noon_close(self, mock_close: MagicMock) -> None:
        day_ts = int(datetime(2026, 7, 6, 10, 0, tzinfo=ET).timestamp())
        prior_noon = int(datetime(2026, 7, 5, 12, 0, tzinfo=ET).timestamp())
        slug = slug_daily_at(day_ts)
        mock_close.return_value = (97000.0, prior_noon)
        event = {
            "title": "Bitcoin Up or Down on July 6?",
            "endDate": "2026-07-06T16:00:00Z",
            "description": "Jul 4 12:00 ET close vs Jul 5 12:00 ET close on Binance",
            "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
            "markets": [{"description": "Binance 1 minute candle close at noon ET", "endDate": "2026-07-06T16:00:00Z"}],
        }
        result = resolve_strike(slug, "daily", snapshot_ts=day_ts, event=event)
        self.assertEqual(result.strike, 97000.0)
        self.assertEqual(result.strike_source, "DESCRIPTION_PARSE")


class MetadataCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_immutable_metadata_cache(self) -> None:
        with connect(self.db_path) as conn:
            ensure_metadata_table(conn)
            upsert_metadata(conn, MarketMetadata(
                "btc-updown-15m-1", "15m", 100, 1000,
                60000.0, BINANCE_OPEN, 100, 0.92, 200,
            ))
            cached = get_cached_metadata(conn, "btc-updown-15m-1")
            self.assertEqual(cached.strike, 60000.0)
            upsert_metadata(conn, MarketMetadata(
                "btc-updown-15m-1", "15m", 100, 1000,
                None, UNKNOWN, None, 0.0, 300,
            ))
            cached2 = get_cached_metadata(conn, "btc-updown-15m-1")
            self.assertEqual(cached2.strike, 60000.0)

    @patch("bot.research.mtf.strike_resolver.resolve_strike")
    def test_get_or_resolve_uses_cache(self, mock_resolve: MagicMock) -> None:
        with connect(self.db_path) as conn:
            ensure_metadata_table(conn)
            upsert_metadata(conn, MarketMetadata(
                "bitcoin-up-or-down-july-6-2026-1pm-et", "1h",
                100, 4600, 99000.0, BINANCE_OPEN, 100, 0.92, 150,
            ))
            result = get_or_resolve_strike(
                conn, "bitcoin-up-or-down-july-6-2026-1pm-et", "1h", snapshot_ts=200,
            )
            mock_resolve.assert_not_called()
            self.assertEqual(result.strike, 99000.0)


class BackfillTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_safe_backfill_no_look_ahead(self) -> None:
        ws = 1_700_000_000
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            ensure_metadata_table(conn)
            insert_snapshot(conn, {
                "timestamp": ws + 120,
                "market_15m_slug": f"btc-updown-15m-{ws}",
            })
            upsert_metadata(conn, MarketMetadata(
                f"btc-updown-15m-{ws}", "15m", ws, ws + 900,
                60123.0, CHAINLINK_REFERENCE, ws, 0.95, ws + 60,
            ))
            stats = backfill_snapshot_strikes(conn)
            conn.commit()
            row = conn.execute(
                "SELECT market_15m_strike FROM multi_timeframe_snapshots"
            ).fetchone()
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(row["market_15m_strike"], 60123.0)

    def test_backfill_skips_future_source_ts(self) -> None:
        ws = 1_700_000_000
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            ensure_metadata_table(conn)
            insert_snapshot(conn, {"timestamp": ws + 60, "market_15m_slug": f"btc-updown-15m-{ws}"})
            upsert_metadata(conn, MarketMetadata(
                f"btc-updown-15m-{ws}", "15m", ws, ws + 900,
                60123.0, CHAINLINK_REFERENCE, ws + 500, 0.95, ws + 500,
            ))
            stats = backfill_snapshot_strikes(conn)
            row = conn.execute("SELECT market_15m_strike FROM multi_timeframe_snapshots").fetchone()
        self.assertEqual(stats["skipped"], 1)
        self.assertIsNone(row["market_15m_strike"])


class AlignmentTestCase(unittest.TestCase):
    def test_15m_window_bounds(self) -> None:
        ws = 1_783_354_500
        slug = f"btc-updown-15m-{ws}"
        self.assertTrue(is_15m_window_correct(slug, ws + 100))
        self.assertFalse(is_15m_window_correct(slug, ws + 900))
        bounds = parse_15m_slug_bounds(slug)
        self.assertEqual(bounds, (ws, ws + 900))

    def test_1h_et_boundary_selection(self) -> None:
        ws = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        slug = slug_1h_at(ws)
        self.assertTrue(is_1h_market_correct(slug, ws + 1800))
        self.assertFalse(is_1h_market_correct(slug, ws - 1))

    def test_dst_slug_format(self) -> None:
        # US DST spring forward 2026: Mar 8
        ts = int(datetime(2026, 3, 8, 14, 0, tzinfo=ET).timestamp())
        slug = slug_1h_at(ts)
        self.assertIn("march-8-2026", slug)
        self.assertIn("et", slug)


class QuoteAuditTestCase(unittest.TestCase):
    def test_bid_le_ask_detection(self) -> None:
        row = {
            "id": 1,
            "timestamp": 1000,
            "market_15m_slug": "btc-updown-15m-900",
            "market_15m_yes_bid": 0.76,
            "market_15m_yes_ask": 0.75,
            "market_15m_no_bid": 0.25,
            "market_15m_no_ask": 0.26,
            "market_15m_strike": None,
            "market_15m_seconds_left": 100,
        }
        # sqlite3.Row needs dict wrapper — audit_tf_row uses row[col]
        class R(dict):
            def __getitem__(self, k): return dict.__getitem__(self, k)

        stats = audit_tf_row(R(row), "15m")
        self.assertEqual(stats.invalid_bid_ask, 1)
        self.assertTrue(any(i.reason == BID_GT_ASK for i in stats.issues))


if __name__ == "__main__":
    unittest.main()
