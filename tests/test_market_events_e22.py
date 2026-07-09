"""Phase E.2.2 observation runner diagnostics and scope tests."""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bot.research.market_events.activation_explain import explain_activation_rules
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.instrument_master import (
    InstrumentRecord,
    load_observe_instruments,
    upsert_instrument,
)
from bot.research.market_events.instrument_types import (
    ACTIVATION_PAPER_ACTIVE,
    ACTIVATION_WATCH,
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_EQUITY,
    HOURS_US_EQUITY,
    INST_TYPE_PERPETUAL,
    INST_TYPE_SYNTHETIC_PERPETUAL,
    VENUE_BINANCE_FUTURES,
    VENUE_BYBIT_LINEAR,
)
from bot.research.market_events.observe_scope import select_observe_universe
from bot.research.market_events.observation_report import observation_report
from bot.research.market_events.observation_runner import ObservationRunner
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.quote_quality import evaluate_bybit_quote
from bot.research.market_events.reference_provider import REFERENCE_PROVIDER_BYBIT_INDEX
from bot.research.market_events.venue_bybit import BybitTicker


class MarketEventsE22Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e22.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_tradfi_watch(self, conn) -> int:
        return upsert_instrument(
            conn,
            InstrumentRecord(
                venue=VENUE_BYBIT_LINEAR,
                venue_symbol="NVDAUSDT",
                canonical_asset="NVDA",
                reference_asset="NVDA",
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

    def _seed_crypto(self, conn) -> None:
        upsert_instrument(
            conn,
            InstrumentRecord(
                venue=VENUE_BINANCE_FUTURES,
                venue_symbol="BTCUSDT",
                canonical_asset="BTC",
                reference_asset="BTC",
                asset_class=ASSET_CLASS_CRYPTO,
                instrument_type=INST_TYPE_PERPETUAL,
                activation_tier=ACTIVATION_PAPER_ACTIVE,
                observe_enabled=1,
                paper_enabled=1,
                active=1,
            ),
        )

    def test_tradfi_observe_excludes_crypto(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_crypto(conn)
            self._seed_tradfi_watch(conn)
            instruments, tag = select_observe_universe(conn, mode="tradfi-observe")
            symbols = [i["canonical_asset"] for i in instruments]
            self.assertIn("NVDA", symbols)
            self.assertNotIn("BTC", symbols)
            self.assertTrue(tag.startswith("tradfi-observe"))

    def test_all_observe_includes_crypto(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_crypto(conn)
            self._seed_tradfi_watch(conn)
            instruments, _ = select_observe_universe(conn, mode="all-observe")
            symbols = [i["canonical_asset"] for i in instruments]
            self.assertIn("BTC", symbols)
            self.assertIn("NVDA", symbols)

    def test_explicit_symbols_scope(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_tradfi_watch(conn)
            instruments, _ = select_observe_universe(conn, explicit_symbols=["NVDAUSDT"])
            self.assertEqual(len(instruments), 1)
            self.assertEqual(instruments[0]["canonical_asset"], "NVDA")

    def test_one_timeout_does_not_block_others(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            i1 = upsert_instrument(
                conn,
                InstrumentRecord(
                    venue=VENUE_BYBIT_LINEAR, venue_symbol="FAILUSDT", canonical_asset="FAIL",
                    reference_asset="FAIL", asset_class=ASSET_CLASS_EQUITY,
                    instrument_type=INST_TYPE_SYNTHETIC_PERPETUAL, trading_hours_mode=HOURS_US_EQUITY,
                    price_source="bybit_last", reference_price_source="bybit_index",
                    activation_tier=ACTIVATION_WATCH, observe_enabled=1,
                ),
            )
            i2 = self._seed_tradfi_watch(conn)
            instruments = [
                dict(conn.execute("SELECT * FROM market_events_instruments WHERE id=?", (i1,)).fetchone()),
                dict(conn.execute("SELECT * FROM market_events_instruments WHERE id=?", (i2,)).fetchone()),
            ]
            runner = ObservationRunner(universe_mode="tradfi-observe", max_cycles=0)
            mock_bybit = mock.MagicMock()

            def _fetch(sym):
                if sym == "FAILUSDT":
                    return None
                return BybitTicker(
                    symbol=sym, last_price=200.0, index_price=199.0, mark_price=199.5,
                    bid=199.9, ask=200.1, volume_24h=1e6, turnover_24h=6e6, ts=int(time.time()),
                )

            mock_bybit.fetch_ticker.side_effect = _fetch
            runner.run_once(conn, instruments, mock_bybit, cycle=1)
            n = conn.execute("SELECT COUNT(*) AS c FROM market_events_price_observations").fetchone()["c"]
            self.assertEqual(n, 2)

    def test_progress_logged_per_instrument(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = self._seed_tradfi_watch(conn)
            inst = dict(conn.execute("SELECT * FROM market_events_instruments WHERE id=?", (iid,)).fetchone())
            runner = ObservationRunner(max_cycles=0)
            mock_bybit = mock.MagicMock()
            mock_bybit.fetch_ticker.return_value = BybitTicker(
                symbol="NVDAUSDT", last_price=200.0, index_price=199.0, mark_price=199.5,
                bid=199.9, ask=200.1, volume_24h=1e6, turnover_24h=6e6, ts=int(time.time()),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                runner.run_once(conn, [inst], mock_bybit, cycle=1)
            out = buf.getvalue()
            self.assertIn("1/1", out)
            self.assertIn("NVDA", out)
            self.assertIn("fetch_start", out)

    def test_stale_quote_recorded_with_flag(self) -> None:
        ticker = BybitTicker(
            symbol="XAUUSDT", last_price=4100.0, index_price=4101.0, mark_price=4100.5,
            bid=4099.9, ask=4100.1, volume_24h=1e6, turnover_24h=10e6,
            ts=int(time.time()) - 300,
        )
        qq = evaluate_bybit_quote(ticker, poll_ts=int(time.time()))
        self.assertFalse(qq.ok)
        self.assertIn("stale", qq.rejection_reason or "")

    def test_watch_creates_observation_not_paper(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = self._seed_tradfi_watch(conn)
            inst = dict(conn.execute("SELECT * FROM market_events_instruments WHERE id=?", (iid,)).fetchone())
            runner = ObservationRunner(max_cycles=0)
            mock_bybit = mock.MagicMock()
            mock_bybit.fetch_ticker.return_value = BybitTicker(
                symbol="NVDAUSDT", last_price=200.0, index_price=199.0, mark_price=199.5,
                bid=199.9, ask=200.1, volume_24h=1e6, turnover_24h=6e6, ts=int(time.time()),
            )
            runner.run_once(conn, [inst], mock_bybit, cycle=1)
            obs = conn.execute("SELECT COUNT(*) AS c FROM market_events_price_observations").fetchone()["c"]
            paper = conn.execute("SELECT COUNT(*) AS c FROM paper_strategy_runs").fetchone()["c"]
            self.assertEqual(obs, 1)
            self.assertEqual(paper, 0)

    def test_paper_runner_skips_watch(self) -> None:
        runner = ShockPaperRunner(max_cycles=0)
        runner._instrument_map = {"NVDA": {"asset_class": ASSET_CLASS_EQUITY, "paper_enabled": 0, "trading_hours_mode": HOURS_US_EQUITY}}
        ok, reason = runner._shock_allowed("NVDA", int(time.time()))
        self.assertFalse(ok)

    def test_observation_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = self._seed_tradfi_watch(conn)
            conn.execute(
                """
                INSERT INTO market_events_price_observations (
                  instrument_id, obs_ts, trade_price, reference_price, basis_bps, spread_bps,
                  venue, session_regime, quote_age_sec, same_venue_reference, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (iid, int(time.time()), 200.0, 199.0, 5.0, 2.0, "bybit_linear", "US_REGULAR", 0.0, 1,
                 json.dumps({"observe_status": "inserted"})),
            )
            report = observation_report(conn, days=7)
            self.assertIn("NVDA", report)
            self.assertIn("total_observations: 1", report)

    def test_activation_explain_documents_gates(self) -> None:
        text = explain_activation_rules()
        self.assertIn("$5,000,000", text)
        self.assertIn("QQQ", text)


if __name__ == "__main__":
    unittest.main()
