"""Phase E.2.1 discovery validation and safe activation tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bot.research.market_events.activation_rules import (
    ACTIVATION_INACTIVE,
    ACTIVATION_PAPER_ACTIVE,
    ACTIVATION_WATCH,
    DEFAULT_RULES,
    evaluate_activation,
    is_shock_eligible,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.instrument_discovery import run_instrument_discovery
from bot.research.market_events.instrument_master import (
    InstrumentRecord,
    load_observe_instruments,
    load_paper_instruments,
    upsert_instrument,
)
from bot.research.market_events.instrument_types import (
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_EQUITY,
    HOURS_US_EQUITY,
    INST_TYPE_SYNTHETIC_PERPETUAL,
    SESSION_UNDERLYING_CLOSED,
    SESSION_US_REGULAR,
    VENUE_BYBIT_LINEAR,
)
from bot.research.market_events.observation_runner import ObservationRunner
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.quote_quality import evaluate_bybit_quote
from bot.research.market_events.reference_provider import (
    REFERENCE_PROVIDER_BYBIT_INDEX,
    basis_independence_warning,
    reference_audit_report,
)
from bot.research.market_events.session_regime import classify_session_regime
from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger
from bot.research.market_events.universe import needs_multi_venue_feed, select_universe
from bot.research.market_events.venue_bybit import BybitTicker


class MarketEventsE21Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e21.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v3(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_events_instruments)").fetchall()}
            for col in ("activation_tier", "observe_enabled", "paper_enabled", "reference_provider"):
                self.assertIn(col, cols)
            self.assertGreaterEqual(SCHEMA_VERSION, 3)

    def test_index_audit_reports_zero_legacy(self) -> None:
        from bot.research.market_events.index_discovery_audit import audit_bybit_index_discovery
        with patch("bot.research.market_events.index_discovery_audit.BybitMarketClient") as mock_cls:
            client = mock_cls.return_value
            client.fetch_ticker.side_effect = lambda sym: None
            client.fetch_instruments.return_value = []
            audit = audit_bybit_index_discovery(client)
            self.assertEqual(audit.bybit_index_count, 0)
            self.assertIn("US500USDT", audit.legacy_map_misses)

    def test_enable_tradfi_does_not_blind_activate_inactive(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            upsert_instrument(
                conn,
                InstrumentRecord(
                    venue=VENUE_BYBIT_LINEAR,
                    venue_symbol="AAPLUSDT",
                    canonical_asset="AAPL",
                    reference_asset="AAPL",
                    asset_class=ASSET_CLASS_EQUITY,
                    instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
                    trading_hours_mode=HOURS_US_EQUITY,
                    price_source="bybit_last",
                    reference_price_source="bybit_index",
                    reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
                    liquidity_tier="INACTIVE",
                    activation_tier=ACTIVATION_INACTIVE,
                    observe_enabled=0,
                    paper_enabled=0,
                ),
            )
            ticker = BybitTicker(
                symbol="AAPLUSDT", last_price=100.0, index_price=100.0,
                mark_price=100.0, bid=99.9, ask=100.1, volume_24h=1, turnover_24h=500_000,
                ts=int(time.time()),
            )
            decision = evaluate_activation(
                asset_class=ASSET_CLASS_EQUITY,
                ticker=ticker,
                session_regime=SESSION_US_REGULAR,
                allow_paper_promotion=True,
            )
            self.assertEqual(decision.tier, ACTIVATION_INACTIVE)

    def test_paper_active_requires_rules(self) -> None:
        ticker = BybitTicker(
            symbol="XAUUSDT", last_price=4100.0, index_price=4101.0,
            mark_price=4100.5, bid=4099.9, ask=4100.1, volume_24h=1e6, turnover_24h=10_000_000,
            ts=int(time.time()),
        )
        decision = evaluate_activation(
            asset_class=ASSET_CLASS_COMMODITY,
            ticker=ticker,
            session_regime=SESSION_US_REGULAR,
            prior_metadata={"observation_poll_count": 3},
            allow_paper_promotion=True,
        )
        self.assertEqual(decision.tier, ACTIVATION_PAPER_ACTIVE)
        self.assertEqual(decision.paper_enabled, 1)

    def test_watch_without_enable_tradfi_caps_paper(self) -> None:
        ticker = BybitTicker(
            symbol="XAUUSDT", last_price=4100.0, index_price=4101.0,
            mark_price=4100.5, bid=4099.9, ask=4100.1, volume_24h=1e6, turnover_24h=10_000_000,
            ts=int(time.time()),
        )
        decision = evaluate_activation(
            asset_class=ASSET_CLASS_COMMODITY,
            ticker=ticker,
            session_regime=SESSION_US_REGULAR,
            prior_metadata={"observation_poll_count": 3},
            allow_paper_promotion=False,
        )
        self.assertEqual(decision.tier, ACTIVATION_WATCH)
        self.assertEqual(decision.paper_enabled, 0)
        self.assertEqual(decision.observe_enabled, 1)

    def test_stale_quote_rejected(self) -> None:
        ticker = BybitTicker(
            symbol="NVDAUSDT", last_price=200.0, index_price=200.0,
            mark_price=200.0, bid=199.9, ask=200.1, volume_24h=1e6, turnover_24h=10_000_000,
            ts=int(time.time()) - 300,
        )
        qq = evaluate_bybit_quote(ticker, poll_ts=int(time.time()))
        self.assertFalse(qq.ok)
        self.assertIn("stale", qq.rejection_reason or "")

    def test_closed_session_blocks_equity_shock(self) -> None:
        ok, reason = is_shock_eligible(
            asset_class=ASSET_CLASS_EQUITY,
            session_regime=SESSION_UNDERLYING_CLOSED,
            quote_ok=True,
            rejection_reason=None,
        )
        self.assertFalse(ok)
        self.assertIn("underlying_closed", reason or "")

    def test_reference_same_venue_warning(self) -> None:
        warn = basis_independence_warning(REFERENCE_PROVIDER_BYBIT_INDEX)
        self.assertIsNotNone(warn)
        self.assertIn("intra-venue", warn or "")
        audit = reference_audit_report()
        self.assertFalse(audit["external_market_basis"])

    def test_watch_observation_no_paper_trades(self) -> None:
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
                    reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
                    activation_tier=ACTIVATION_WATCH,
                    observe_enabled=1,
                    paper_enabled=0,
                ),
            )
            runner = ObservationRunner(max_cycles=0)
            ticker = BybitTicker(
                symbol="TSLAUSDT", last_price=400.0, index_price=399.0,
                mark_price=399.5, bid=399.5, ask=400.5, volume_24h=1e5, turnover_24h=2e6,
                ts=int(time.time()),
            )
            inst = dict(conn.execute("SELECT * FROM market_events_instruments WHERE id=?", (iid,)).fetchone())
            mock_bybit = mock.MagicMock()
            mock_bybit.fetch_ticker.return_value = ticker
            runner.run_once(conn, [inst], mock_bybit, cycle=1)
            obs = conn.execute("SELECT COUNT(*) AS n FROM market_events_price_observations").fetchone()["n"]
            self.assertEqual(obs, 1)
            row = conn.execute("SELECT * FROM market_events_price_observations LIMIT 1").fetchone()
            self.assertEqual(row["same_venue_reference"], 1)
            self.assertEqual(row["reference_provider"], REFERENCE_PROVIDER_BYBIT_INDEX)

    def test_paper_active_in_tradfi_universe(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            upsert_instrument(
                conn,
                InstrumentRecord(
                    venue=VENUE_BYBIT_LINEAR,
                    venue_symbol="XAUUSDT",
                    canonical_asset="GOLD",
                    reference_asset="GOLD",
                    asset_class=ASSET_CLASS_COMMODITY,
                    instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
                    trading_hours_mode=HOURS_US_EQUITY,
                    price_source="bybit_last",
                    reference_price_source="bybit_index",
                    reference_provider=REFERENCE_PROVIDER_BYBIT_INDEX,
                    activation_tier=ACTIVATION_PAPER_ACTIVE,
                    observe_enabled=1,
                    paper_enabled=1,
                    active=1,
                ),
            )
            symbols, tag = select_universe(conn, mode="tradfi-liquid")
            self.assertIn("GOLD", symbols)
            self.assertTrue(tag.startswith("tradfi-liquid"))

    def test_inactive_not_in_observe_list(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            upsert_instrument(
                conn,
                InstrumentRecord(
                    venue=VENUE_BYBIT_LINEAR,
                    venue_symbol="AAPLUSDT",
                    canonical_asset="AAPL",
                    reference_asset="AAPL",
                    asset_class=ASSET_CLASS_EQUITY,
                    instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL,
                    trading_hours_mode=HOURS_US_EQUITY,
                    price_source="bybit_last",
                    reference_price_source="bybit_index",
                    activation_tier=ACTIVATION_INACTIVE,
                    observe_enabled=0,
                    paper_enabled=0,
                ),
            )
            self.assertEqual(len(load_observe_instruments(conn)), 0)
            self.assertEqual(len(load_paper_instruments(conn, tradfi_only=True)), 0)

    def test_paper_runner_skips_watch_for_shocks(self) -> None:
        runner = ShockPaperRunner(max_cycles=0)
        runner._instrument_map = {
            "TSLA": {
                "asset_class": ASSET_CLASS_EQUITY,
                "paper_enabled": 0,
                "trading_hours_mode": HOURS_US_EQUITY,
            },
        }
        ok, reason = runner._shock_allowed("TSLA", int(time.time()))
        self.assertFalse(ok)
        self.assertEqual(reason, "not_paper_enabled")

    def test_universe_modes(self) -> None:
        self.assertFalse(needs_multi_venue_feed("core"))
        self.assertTrue(needs_multi_venue_feed("tradfi-liquid"))
        self.assertTrue(needs_multi_venue_feed("multi-paper"))
        self.assertTrue(needs_multi_venue_feed("core", ["XAUUSDT"]))


if __name__ == "__main__":
    unittest.main()
