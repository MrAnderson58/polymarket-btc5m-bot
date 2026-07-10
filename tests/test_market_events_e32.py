"""Phase E.3.2 collector observability and near-miss audit tests."""

from __future__ import annotations

import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.market_events.collector_heartbeat import CollectorMetrics, format_uptime
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.detector_diagnostics import (
    REJECTION_BELOW_RETURN,
    REJECTION_FIRED,
    REJECTION_INSUFFICIENT_HISTORY,
    diagnose_detectors_for_state,
)
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.near_miss_shadow import (
    NearMissSnapshot,
    shock_near_miss_report,
    update_near_miss_from_state,
)
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.pending_reversal_report import pending_reversal_report
from bot.research.market_events.price_feed import PriceTick, SymbolPriceState


class MarketEventsE32Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e32.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v6_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v6", applied)
            self.assertEqual(SCHEMA_VERSION, 6)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_near_miss_summaries", tables)

    def test_heartbeat_emitted_after_interval(self) -> None:
        metrics = CollectorMetrics()
        metrics.record_cycle(10.0, fetch_ok=10, fetch_failed=0)
        metrics.last_heartbeat_ts = int(time.time()) - 61
        self.assertTrue(metrics.should_heartbeat(int(time.time()), 60))
        text = metrics.render_heartbeat(
            universe="core", events_detected=0, pending_reversals=0, paper_runs_open=0,
        )
        self.assertIn("[shock-paper] heartbeat", text)
        self.assertIn("universe=core", text)
        self.assertIn("cycles=1", text)

    def test_format_uptime(self) -> None:
        self.assertEqual(format_uptime(5025), "01:23:45")

    def test_heartbeat_does_not_alter_db_on_runner(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            events_before = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            runner = ShockPaperRunner(max_cycles=0, heartbeat_sec=1)
            runner.metrics.record_cycle(5.0, fetch_ok=1, fetch_failed=0)
            runner.metrics.last_heartbeat_ts = 0
            buf = io.StringIO()
            with redirect_stdout(buf):
                runner._maybe_heartbeat(conn, ["BTC"])
            events_after = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            self.assertEqual(events_before, events_after)
            self.assertIn("heartbeat", buf.getvalue())

    def test_near_miss_does_not_create_market_events(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            st = SymbolPriceState(symbol="OIL", pair="OILUSDT")
            st.append(PriceTick(ts=now - 60, price=100.0, volume=100), max_age_sec=900)
            st.append(PriceTick(ts=now, price=100.5, volume=100), max_age_sec=900)
            tracker: dict = {}
            update_near_miss_from_state(tracker, st, now_ts=now)
            self.assertTrue(tracker)
            events = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            runs = conn.execute("SELECT COUNT(*) FROM paper_strategy_runs").fetchone()[0]
            self.assertEqual(events, 0)
            self.assertEqual(runs, 0)

    def test_near_miss_report_empty(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = shock_near_miss_report(conn, days=1, persist=False)
            self.assertIn("NEAR-MISS", text)

    def test_detector_rejection_below_threshold(self) -> None:
        now = int(time.time())
        st = SymbolPriceState(symbol="BTC", pair="BTCUSDT")
        st.append(PriceTick(ts=now - 60, price=100.0, volume=1000), max_age_sec=300)
        st.append(PriceTick(ts=now, price=100.5, volume=1000), max_age_sec=300)
        diag = diagnose_detectors_for_state(st, now_ts=now, symbol="BTC")
        self.assertGreater(diag.counts["SHOCK_A"][REJECTION_BELOW_RETURN], 0)

    def test_detector_fired_on_large_move(self) -> None:
        now = int(time.time())
        st = SymbolPriceState(symbol="SOL", pair="SOLUSDT")
        st.append(PriceTick(ts=now - 30, price=100.0, volume=1000), max_age_sec=300)
        st.append(PriceTick(ts=now, price=97.5, volume=1000), max_age_sec=300)
        diag = diagnose_detectors_for_state(st, now_ts=now, symbol="SOL")
        self.assertGreater(diag.counts["SHOCK_A"][REJECTION_FIRED], 0)

    def test_insufficient_history_counted(self) -> None:
        st = SymbolPriceState(symbol="GOLD", pair="GOLDUSDT")
        diag = diagnose_detectors_for_state(st, now_ts=int(time.time()), symbol="GOLD")
        self.assertGreater(diag.counts["SHOCK_A"][REJECTION_INSUFFICIENT_HISTORY], 0)

    def test_legacy_event_distinguished(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (?, ?, 'binance_futures', 'SUI', 'UP', 'SHOCK_DETECTED',
                  60, 1.22, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
                """,
                (now, now, f"legacy-{now}", now),
            )
            text = pending_reversal_report(conn, days=1)
            self.assertIn("Legacy events", text)
            self.assertIn("pre-E.3.1", text)
            self.assertIn("SUI", text)
            self.assertIn("not a lifecycle failure", text)

    def test_new_event_pending_lifecycle_section(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = conn.execute(
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (?, ?, 'binance_futures', 'SOL', 'DOWN', 'MONITORING_REVERSAL',
                  60, -3.0, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
                """,
                (now, now, f"new-{now}", now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO market_events_pending_shocks (
                  event_id, symbol, direction, phase, detected_ts, shock_return_pct,
                  monitor_until_ts, created_at, updated_at
                ) VALUES (?, 'SOL', 'DOWN', 'MONITORING_REVERSAL', ?, -3.0, ?, ?, ?)
                """,
                (eid, now, now + 900, now, now),
            )
            text = pending_reversal_report(conn, days=1)
            self.assertIn("E.3.1+ lifecycle", text)
            self.assertIn("MONITORING_REVERSAL", text)

    def test_tradfi_path_reaches_detector_evaluation(self) -> None:
        from bot.research.market_events.collector_path_audit import run_collector_path_audit
        from bot.research.market_events.detector_diagnostics import diagnose_universe

        now = int(time.time())
        feed = MagicMock()
        states = {}
        for sym in ("GOLD", "OIL", "SILVER"):
            st = SymbolPriceState(symbol=sym, pair=f"{sym}USDT")
            st.append(PriceTick(ts=now - 120, price=100.0, volume=100), max_age_sec=900)
            st.append(PriceTick(ts=now, price=100.1, volume=100), max_age_sec=900)
            states[sym] = st
        feed.get_state = lambda s: states.get(s)
        diag = diagnose_universe(feed, ["GOLD", "OIL", "SILVER"], now_ts=now)
        self.assertGreater(diag.total_evaluations(), 0)

    def test_universe_core_unchanged(self) -> None:
        runner = ShockPaperRunner(universe_mode="core", max_cycles=0)
        self.assertEqual(runner.universe_mode, "core")

    def test_no_live_execution_import(self) -> None:
        import bot.research.market_events.paper_runner as pr
        import bot.research.market_events.collector_path_audit as cpa
        src = open(pr.__file__).read() + open(cpa.__file__).read()
        self.assertNotIn("bot.main", src)
        self.assertNotIn("TRADING_MODE=live", src)

    def test_near_miss_threshold_reached_calculation(self) -> None:
        now = int(time.time())
        st = SymbolPriceState(symbol="OIL", pair="OILUSDT")
        st.append(PriceTick(ts=now - 30, price=100.0, volume=100), max_age_sec=900)
        st.append(PriceTick(ts=now, price=100.72, volume=100), max_age_sec=900)
        tracker: dict = {}
        update_near_miss_from_state(tracker, st, now_ts=now)
        snap = next(iter(tracker.values()))
        self.assertGreater(snap.max_threshold_reached_pct, 40)
        self.assertLess(snap.max_threshold_reached_pct, 60)


if __name__ == "__main__":
    unittest.main()
