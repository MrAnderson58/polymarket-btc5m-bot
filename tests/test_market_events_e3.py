"""Phase E.3 lifecycle visibility and research audit tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_helpers import row_get, scalar
from bot.research.market_events.event_report import (
    shock_context_report,
    shock_event_report,
    shock_strategy_report,
)
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.lifecycle_audit import shock_lifecycle_audit
from bot.research.market_events.lifecycle_decisions import persist_reversal_decisions
from bot.research.market_events.reversal_confirmation import ReversalResult
from bot.research.market_events.shock_f_shadow import run_shock_f_shadow_audit
from bot.research.market_events.shock_opportunity_audit import shock_opportunity_audit
from bot.research.market_events.strategy_matrix_report import shock_strategy_matrix_report
from bot.research.market_events.tradfi_shock_readiness import tradfi_shock_readiness


class MarketEventsE3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e3.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_event(self, conn, *, with_runs: bool = False, closed: bool = False) -> int:
        now = int(time.time())
        eid = conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              detector_version, detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', 'SUI', 'UP', 'SHOCK_DETECTED',
              60, 1.22, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
            """,
            (now, now, f"dedup-{now}", now),
        ).lastrowid
        persist_reversal_decisions(
            conn,
            event_id=int(eid),
            decision_ts=now,
            results=[
                ReversalResult("R1", False, None, None, "reclaim=0.1% need=0.4%"),
                ReversalResult("R2", False, None, None, "insufficient_ticks"),
            ],
        )
        if with_runs:
            conn.execute(
                """
                INSERT INTO paper_strategy_runs (
                  event_id, strategy_name, strategy_version, reversal_variant, exit_variant,
                  eligibility, entry_ts, entry_price, initial_stop, created_at
                ) VALUES (?, 'REVERSAL_R1_EXIT_A', 'v1', 'R1', 'EXIT_A', 1, ?, 1.0, 0.99, ?)
                """,
                (eid, now, now),
            )
            if closed:
                conn.execute(
                    """
                    UPDATE paper_strategy_runs SET exit_ts=?, exit_price=?, net_return=0.5,
                      gross_return=0.5, mfe=0.6, mae=0.2, exit_reason='TP'
                    WHERE event_id=? AND reversal_variant='R1'
                    """,
                    (now + 60, 1.01, eid),
                )
        return int(eid)

    def test_scalar_without_alias(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT COUNT(*) FROM (SELECT 1)").fetchone()
        self.assertEqual(scalar(row), 1)

    def test_context_report_no_crash_empty(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = shock_context_report(conn, days=1)
            self.assertIn("events: 0", text)
            self.assertIn("events_with_context: 0", text)

    def test_context_report_with_unaliased_count(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE market_events (id INTEGER PRIMARY KEY, event_ts INTEGER);
            CREATE TABLE market_event_context (
              id INTEGER PRIMARY KEY, event_id INTEGER, context_type TEXT,
              source TEXT, source_record_id TEXT, context_ts INTEGER,
              time_delta_seconds INTEGER, created_at INTEGER
            );
        """)
        conn.execute("INSERT INTO market_events (id, event_ts) VALUES (1, ?)", (int(time.time()),))
        text = shock_context_report(conn, days=7)
        self.assertIn("events: 1", text)

    def test_all_reports_render(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_event(conn)
            shock_event_report(conn, days=1)
            shock_strategy_report(conn, days=1)
            shock_context_report(conn, days=1)
            shock_lifecycle_audit(conn, days=1)
            shock_strategy_matrix_report(conn, days=1)

    def test_lifecycle_no_reversal_activated(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn, with_runs=False)
            text = shock_lifecycle_audit(conn, days=1)
            self.assertIn(f"event_id={eid}", text)
            self.assertIn("paper_runs_created: 0", text)
            self.assertIn("R1 status=rejected", text)

    def test_lifecycle_open_run(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_event(conn, with_runs=True, closed=False)
            text = shock_lifecycle_audit(conn, days=1)
            self.assertIn("open: 1", text)

    def test_lifecycle_closed_run(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_event(conn, with_runs=True, closed=True)
            text = shock_lifecycle_audit(conn, days=1)
            self.assertIn("closed: 1", text)

    def test_opportunity_audit_read_only(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = conn.execute(
                """
                INSERT INTO market_events_instruments (
                  venue, venue_symbol, canonical_asset, reference_asset, asset_class,
                  instrument_type, trading_hours_mode, price_source, reference_price_source,
                  created_at, updated_at
                ) VALUES ('bybit_linear','XAUUSDT','GOLD','GOLD','COMMODITY',
                  'SYNTHETIC_PERPETUAL','COMMODITY_SESSION','bybit_last','bybit_index',?,?)
                """,
                (int(time.time()), int(time.time())),
            ).lastrowid
            now = int(time.time())
            base = 4100.0
            for i in range(50):
                px = base * (1 + 0.001 * (i % 5 - 2))
                conn.execute(
                    """
                    INSERT INTO market_events_price_observations (
                      instrument_id, obs_ts, trade_price, venue, same_venue_reference, raw_json
                    ) VALUES (?, ?, ?, 'bybit_linear', 1, '{}')
                    """,
                    (iid, now - (50 - i) * 30, px),
                )
            before = conn.execute("SELECT COUNT(*) AS n FROM market_events").fetchone()
            text = shock_opportunity_audit(conn, days=1)
            after = conn.execute("SELECT COUNT(*) AS n FROM market_events").fetchone()
            self.assertEqual(scalar(before), scalar(after))
            self.assertIn("GOLD", text)

    def test_shock_f_no_paper_runs(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = conn.execute(
                """
                INSERT INTO market_events_instruments (
                  venue, venue_symbol, canonical_asset, reference_asset, asset_class,
                  instrument_type, trading_hours_mode, price_source, reference_price_source,
                  created_at, updated_at
                ) VALUES ('bybit_linear','NVDAUSDT','NVDA','NVDA','EQUITY',
                  'SYNTHETIC_PERPETUAL','US_EQUITY','bybit_last','bybit_index',?,?)
                """,
                (int(time.time()), int(time.time())),
            ).lastrowid
            now = int(time.time())
            for i in range(40):
                conn.execute(
                    """
                    INSERT INTO market_events_price_observations (
                      instrument_id, obs_ts, trade_price, venue, same_venue_reference, raw_json
                    ) VALUES (?, ?, ?, 'bybit_linear', 1, '{}')
                    """,
                    (iid, now - (40 - i) * 60, 200.0 + i * 0.5),
                )
            run_shock_f_shadow_audit(conn, days=1, persist=True)
            paper = scalar(conn.execute("SELECT COUNT(*) AS n FROM paper_strategy_runs").fetchone())
            events = scalar(conn.execute("SELECT COUNT(*) AS n FROM market_events").fetchone())
            self.assertEqual(paper, 0)
            self.assertEqual(events, 0)
            shadow = scalar(conn.execute("SELECT COUNT(*) AS n FROM market_events_shadow_candidates").fetchone())
            self.assertGreaterEqual(shadow, 0)

    def test_tradfi_readiness_irregular_sampling(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            iid = conn.execute(
                """
                INSERT INTO market_events_instruments (
                  venue, venue_symbol, canonical_asset, reference_asset, asset_class,
                  instrument_type, trading_hours_mode, price_source, reference_price_source,
                  created_at, updated_at
                ) VALUES ('bybit_linear','XAUUSDT','GOLD','GOLD','COMMODITY',
                  'SYNTHETIC_PERPETUAL','COMMODITY_SESSION','bybit_last','bybit_index',?,?)
                """,
                (int(time.time()), int(time.time())),
            ).lastrowid
            now = int(time.time())
            for i, gap in enumerate([1, 1, 5, 1, 30, 1, 1]):
                conn.execute(
                    """
                    INSERT INTO market_events_price_observations (
                      instrument_id, obs_ts, trade_price, spread_bps, basis_bps,
                      session_regime, venue, same_venue_reference, raw_json
                    ) VALUES (?, ?, ?, 0.5, 2.0, 'CRYPTO_24H', 'bybit_linear', 1, '{}')
                    """,
                    (iid, now + i * gap, 4100.0),
                )
            text = tradfi_shock_readiness(conn, days=1)
            self.assertIn("GOLD", text)
            self.assertIn("max_gap_sec", text)

    def test_matrix_includes_zero_strategies(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = shock_strategy_matrix_report(conn, days=1)
            for rv in ("R1", "R2", "R3", "R4", "R5"):
                self.assertIn(rv, text)

    def test_schema_v4(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertTrue(any("v4" in a for a in applied) or SCHEMA_VERSION == 4)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_event_lifecycle_decisions", tables)
            self.assertIn("market_events_shadow_candidates", tables)


if __name__ == "__main__":
    unittest.main()
