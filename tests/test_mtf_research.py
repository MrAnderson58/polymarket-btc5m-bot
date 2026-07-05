"""Tests for multi-timeframe context research layer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from bot.database import connect, init_db
from bot.research.mtf.btc_context import build_btc_context
from bot.research.mtf.collector import collect_mtf_snapshot
from bot.research.mtf.discovery import (
    CANDIDATE_CLOSED,
    NO_CANDIDATE,
    compute_seconds_left,
    discover_15m_market,
    discover_1h_market,
    discover_daily_market,
    parse_15m_window_start_ts,
    seconds_left_for_ref,
    slug_1h_at,
    slug_15m_at,
    slug_daily_at,
    HtfMarketRef,
)
from bot.research.mtf.labels import alignment_label, htf_label_from_btc
from bot.research.mtf.models import BtcSpotContext, PolymarketTfContext
from bot.research.mtf.analysis import (
    context_performance_matrix,
    test_hypotheses,
    walk_forward_abc,
)
from bot.research.mtf.context_builder import build_trade_context
from bot.research.mtf.data_audit import audit_data_coverage
from bot.research.mtf.models import TradeContext
from bot.research.mtf.quotes import MtfTokenQuotes, get_mtf_token_quotes
from bot.research.mtf.snapshots import ensure_tables, insert_snapshot, snapshot_at_or_before
from bot.research.bidirectional_live_audit import LiveTrade

ET = ZoneInfo("America/New_York")


def _seed_mc(conn: sqlite3.Connection, base_ts: int, n: int = 100) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            seconds_remaining REAL NOT NULL,
            strike_price REAL NOT NULL,
            btc_price REAL NOT NULL,
            yes_bid REAL, yes_ask REAL, no_bid REAL, no_ask REAL,
            signal TEXT,
            checked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bidirectional_shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            window_start_ts INTEGER,
            side TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'closed',
            entry_price REAL NOT NULL,
            entry_ts INTEGER NOT NULL,
            entry_regime TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            holding_time_seconds REAL
        );
    """)
    price = 60000.0
    for i in range(n):
        ts = base_ts + i * 10
        price += 5 if i % 3 == 0 else -2
        conn.execute(
            """
            INSERT INTO market_checks (
                market_slug, seconds_remaining, strike_price, btc_price,
                yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
            ) VALUES ('btc-updown-5m-test', 200, 60000, ?, 0.4, 0.42, 0.58, 0.6, NULL,
                      datetime(?, 'unixepoch'))
            """,
            (price, ts),
        )
    conn.commit()


class MtfDiscoveryTestCase(unittest.TestCase):
    def test_parse_15m_window_start_ts(self) -> None:
        ws = 1_783_354_500
        slug = f"btc-updown-15m-{ws}"
        self.assertEqual(parse_15m_window_start_ts(slug), ws)
        self.assertIsNone(parse_15m_window_start_ts("bitcoin-up-or-down-july-6-2026-1pm-et"))

    def test_15m_seconds_left_inside_window(self) -> None:
        ws = 1_783_354_500
        snapshot_ts = ws + 300
        ref = HtfMarketRef(
            timeframe="15m",
            slug=f"btc-updown-15m-{ws}",
            title="test",
            window_start_ts=ws,
            end_ts=ws + 900,
            active=True,
        )
        self.assertEqual(seconds_left_for_ref(ref, snapshot_ts), 600)
        self.assertEqual(compute_seconds_left(ws + 900, snapshot_ts), 600)

    def test_15m_seconds_left_not_null_at_start(self) -> None:
        ws = 1_783_354_500
        ref = HtfMarketRef("15m", f"btc-updown-15m-{ws}", "", ws, ws + 900, True)
        self.assertEqual(seconds_left_for_ref(ref, ws), 900)

    def test_15m_seconds_left_zero_after_expiry(self) -> None:
        ws = 1_783_354_500
        end_ts = ws + 900
        self.assertEqual(compute_seconds_left(end_ts, end_ts + 10), 0)

    def test_slug_15m_alignment(self) -> None:
        ts = 1_783_354_567
        self.assertTrue(slug_15m_at(ts).endswith(str((ts // 900) * 900)))

    def test_slug_1h_confirmed_format(self) -> None:
        # July 6, 2026 1PM ET
        ts = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        self.assertEqual(slug_1h_at(ts), "bitcoin-up-or-down-july-6-2026-1pm-et")

    def test_slug_daily_confirmed_format(self) -> None:
        ts = int(datetime(2026, 7, 6, 10, 0, tzinfo=ET).timestamp())
        self.assertEqual(slug_daily_at(ts), "bitcoin-up-or-down-on-july-6-2026")

    @patch("bot.research.mtf.discovery._fetch_gamma_event")
    def test_discover_15m_rejects_expired(self, mock_fetch: MagicMock) -> None:
        ws = 1_700_000_000
        ts = ws + 950
        mock_fetch.side_effect = [
            {
                "title": "expired current",
                "active": True,
                "closed": False,
                "endDate": "2023-06-01T00:00:00Z",
            },
            {
                "title": "expired previous",
                "active": True,
                "closed": False,
                "endDate": "2023-06-01T00:00:00Z",
            },
        ]
        ref = discover_15m_market(ts)
        self.assertIsNone(ref)

    @patch("bot.research.mtf.discovery._fetch_gamma_event")
    def test_discover_1h_real_slug(self, mock_fetch: MagicMock) -> None:
        ts = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        slug = slug_1h_at(ts)
        mock_fetch.return_value = {
            "title": "Bitcoin Up or Down - July 6, 1PM ET",
            "active": True,
            "closed": False,
            "endDate": "2026-07-06T18:00:00Z",
        }
        ref = discover_1h_market(ts)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.slug, slug)
        self.assertEqual(ref.timeframe, "1h")

    @patch("bot.research.mtf.discovery._fetch_gamma_event")
    def test_discover_daily_real_slug(self, mock_fetch: MagicMock) -> None:
        ts = int(datetime(2026, 7, 6, 10, 0, tzinfo=ET).timestamp())
        slug = slug_daily_at(ts)
        mock_fetch.return_value = {
            "title": "Bitcoin Up or Down on July 6?",
            "active": True,
            "closed": False,
            "endDate": "2026-07-06T16:00:00Z",
        }
        ref = discover_daily_market(ts)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.slug, slug)

    @patch("bot.research.mtf.discovery._fetch_gamma_event")
    def test_discover_1h_closed_reason(self, mock_fetch: MagicMock) -> None:
        ts = int(datetime(2026, 7, 6, 13, 0, tzinfo=ET).timestamp())
        mock_fetch.return_value = {
            "title": "closed hour",
            "active": False,
            "closed": True,
            "endDate": "2026-07-06T18:00:00Z",
        }
        ref = discover_1h_market(ts)
        self.assertIsNone(ref)


class MtfQuotesTestCase(unittest.TestCase):
    def test_bid_ask_orientation_from_clob_semantics(self) -> None:
        client = MagicMock()
        client.get_price.side_effect = lambda token_id, side: {"price": "0.50" if side == "BUY" else "0.51"}
        q = get_mtf_token_quotes(client, "token")
        self.assertEqual(q.bid, 0.50)
        self.assertEqual(q.ask, 0.51)
        self.assertLessEqual(q.bid, q.ask)

    def test_bid_ask_normalization_when_crossed(self) -> None:
        client = MagicMock()
        client.get_price.side_effect = lambda token_id, side: {"price": "0.55" if side == "BUY" else "0.54"}
        q = get_mtf_token_quotes(client, "token")
        self.assertEqual(q.bid, 0.54)
        self.assertEqual(q.ask, 0.55)


class MtfCollectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @patch("bot.research.mtf.strike_resolver.get_or_resolve_strike")
    @patch("bot.research.mtf.quotes.fetch_mtf_market_quotes")
    @patch("bot.research.mtf.discovery.discover_active_htf_markets")
    def test_snapshot_persistence_all_timeframes(
        self,
        mock_discover: MagicMock,
        mock_quotes: MagicMock,
        mock_strike: MagicMock,
    ) -> None:
        from bot.research.mtf.strike_resolver import BINANCE_OPEN, StrikeResult

        ws = 1_783_354_500
        now_ts = ws + 120
        mock_discover.return_value = {
            "15m": HtfMarketRef("15m", f"btc-updown-15m-{ws}", "t", ws, ws + 900, True),
            "1h": HtfMarketRef("1h", "bitcoin-up-or-down-july-6-2026-1pm-et", "t", None, now_ts + 3600, True),
            "daily": HtfMarketRef("daily", "bitcoin-up-or-down-on-july-6-2026", "t", None, now_ts + 86400, True),
        }
        mock_quotes.return_value = {
            "yes_bid": 0.48,
            "yes_ask": 0.50,
            "no_bid": 0.50,
            "no_ask": 0.52,
        }
        mock_strike.return_value = StrikeResult(60100.0, BINANCE_OPEN, ws, 0.92, "slug")
        with patch("bot.research.mtf.collector.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                collect_mtf_snapshot(
                    conn,
                    market_5m_slug="btc-updown-5m-test",
                    btc_price=60000,
                    yes_bid=0.48,
                    yes_ask=0.50,
                    no_bid=0.50,
                    no_ask=0.52,
                    strike=60000,
                )
                conn.commit()
                row = snapshot_at_or_before(conn, now_ts)
        self.assertIsNotNone(row)
        self.assertEqual(row["market_15m_seconds_left"], 780)
        self.assertEqual(row["market_15m_slug"], f"btc-updown-15m-{ws}")
        self.assertEqual(row["market_1h_slug"], "bitcoin-up-or-down-july-6-2026-1pm-et")
        self.assertEqual(row["market_daily_slug"], "bitcoin-up-or-down-on-july-6-2026")
        self.assertEqual(row["market_15m_yes_bid"], 0.48)
        self.assertLessEqual(row["market_15m_yes_bid"], row["market_15m_yes_ask"])
        self.assertEqual(row["market_15m_strike"], 60100.0)

    @patch("bot.research.mtf.strike_resolver.get_or_resolve_strike")
    @patch("bot.research.mtf.quotes.fetch_mtf_market_quotes")
    @patch("bot.research.mtf.discovery.discover_active_htf_markets")
    def test_graceful_missing_quotes(
        self, mock_discover: MagicMock, mock_quotes: MagicMock, mock_strike: MagicMock,
    ) -> None:
        from bot.research.mtf.strike_resolver import UNKNOWN, StrikeResult

        ws = 1_783_354_500
        now_ts = ws + 60
        mock_discover.return_value = {
            "15m": HtfMarketRef("15m", f"btc-updown-15m-{ws}", "t", ws, ws + 900, True),
            "1h": None,
            "daily": None,
        }
        mock_quotes.return_value = None
        mock_strike.return_value = StrikeResult(None, UNKNOWN, None, 0.0, f"btc-updown-15m-{ws}")
        with patch("bot.research.mtf.collector.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                collect_mtf_snapshot(
                    conn,
                    market_5m_slug="btc-updown-5m-test",
                    btc_price=60000,
                    yes_bid=0.4,
                    yes_ask=0.42,
                    no_bid=0.58,
                    no_ask=0.6,
                )
                conn.commit()
                row = snapshot_at_or_before(conn, now_ts)
        self.assertIsNotNone(row)
        self.assertEqual(row["market_15m_seconds_left"], 840)
        self.assertIsNone(row["market_15m_yes_ask"])

    @patch("bot.research.mtf.strike_resolver.get_or_resolve_strike")
    @patch("bot.research.mtf.quotes.fetch_mtf_market_quotes")
    @patch("bot.research.mtf.discovery.discover_active_htf_markets")
    def test_no_look_ahead_quote_timestamp(
        self, mock_discover: MagicMock, mock_quotes: MagicMock, mock_strike: MagicMock,
    ) -> None:
        from bot.research.mtf.strike_resolver import UNKNOWN, StrikeResult

        snapshot_ts = 1_700_000_100
        mock_discover.return_value = {
            "15m": HtfMarketRef("15m", "btc-updown-15m-1700000000", "t", 1_700_000_000, 1_700_000_900, True),
            "1h": None,
            "daily": None,
        }
        mock_quotes.return_value = {"yes_bid": 0.5, "yes_ask": 0.52, "no_bid": 0.48, "no_ask": 0.5}
        mock_strike.return_value = StrikeResult(None, UNKNOWN, None, 0.0, "btc-updown-15m-1700000000")
        with patch("bot.research.mtf.collector.time.time", return_value=snapshot_ts):
            with connect(self.db_path) as conn:
                collect_mtf_snapshot(
                    conn,
                    market_5m_slug="btc-updown-5m-test",
                    btc_price=60000,
                    yes_bid=0.5,
                    yes_ask=0.52,
                    no_bid=0.48,
                    no_ask=0.5,
                )
                conn.commit()
                row = snapshot_at_or_before(conn, snapshot_ts)
        self.assertLessEqual(row["timestamp"], snapshot_ts)


class MtfBtcContextTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_btc_context_no_look_ahead(self) -> None:
        base = 1_700_000_000
        with connect(self.db_path) as conn:
            _seed_mc(conn, base, 200)
            ctx = build_btc_context(conn, base + 1000)
        self.assertIsNotNone(ctx.price)
        self.assertIsNotNone(ctx.return_5m)

    def test_htf_labels(self) -> None:
        up = BtcSpotContext(timestamp=0, price=60000, return_1h=0.5, return_15m=0.2)
        self.assertEqual(htf_label_from_btc(up), "HTF_STRONG_UP")
        down = BtcSpotContext(timestamp=0, price=60000, return_1h=-0.5, return_15m=-0.2)
        self.assertEqual(htf_label_from_btc(down), "HTF_STRONG_DOWN")


class MtfAnalysisTestCase(unittest.TestCase):
    def test_walk_forward_runs(self) -> None:
        contexts = []
        for i in range(40):
            btc = BtcSpotContext(timestamp=i, price=60000, return_1h=0.2 if i % 2 else -0.2)
            pm = PolymarketTfContext("15m", available=True, prob_yes=0.6, prob_direction="UP")
            contexts.append(TradeContext(
                i, f"m{i}", i * 100, "YES" if i % 2 else "NO", 0.4, 5.0 if i % 3 else -4.0,
                btc=btc, pm_15m=pm,
            ))
        wf = walk_forward_abc(contexts)
        self.assertIn("MODEL_A_5m_only", wf.get("models", {}))

    def test_hypotheses_structure(self) -> None:
        contexts = [
            TradeContext(1, "m", 100, "NO", 0.4, 10.0, btc=BtcSpotContext(100, 60000),
                         pm_15m=PolymarketTfContext("15m", available=True, prob_direction="DOWN"),
                         pm_1h=PolymarketTfContext("1h", available=True, prob_direction="DOWN")),
        ]
        h = test_hypotheses(contexts)
        self.assertIn("A_no_triple_align", h)


class MtfSnapshotsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_audit_and_snapshots(self) -> None:
        with connect(self.db_path) as conn:
            ensure_tables(conn)
            insert_snapshot(conn, {
                "timestamp": 1_700_000_000,
                "btc_price": 60000,
                "market_5m_slug": "btc-updown-5m-1700000000",
                "market_15m_slug": "btc-updown-15m-1700000000",
                "market_15m_yes_bid": 0.48,
                "market_15m_yes_ask": 0.50,
                "market_15m_no_bid": 0.50,
                "market_15m_no_ask": 0.52,
                "market_15m_seconds_left": 600,
            })
            audit = audit_data_coverage(conn)
        self.assertEqual(audit["snapshot_total"], 1)
        self.assertEqual(audit["snapshot_15m"], 1)


if __name__ == "__main__":
    unittest.main()
