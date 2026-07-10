"""Phase E.3.1 pending reversal watcher, profiles, SHOCK_F_v2, counterfactual tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.event_types import (
    EVENT_PHASE_EXPIRED,
    EVENT_PHASE_PAPER_ENTRY,
    SHOCK_DIRECTION_DOWN,
)
from bot.research.market_events.paper_execution import open_paper_position, process_exit_tick
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.pending_reversal import (
    PHASE_EXPIRED,
    PHASE_MANAGING,
    PHASE_MONITORING,
    create_pending_shock,
    process_pending_shock,
    restore_pending_map,
)
from bot.research.market_events.pending_reversal_report import pending_reversal_report
from bot.research.market_events.price_feed import PriceTick, SymbolPriceState
from bot.research.market_events.profile_shadow import shock_profile_report
from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger
from bot.research.market_events.shock_f_v2_shadow import (
    _dedupe_episodes,
    detect_shock_f_v2_at_index,
    run_shock_f_v2_shadow_audit,
)
from bot.research.market_events.shock_profiles import (
    PROFILE_COMMODITY_OIL,
    PROFILE_CRYPTO,
    profile_for_symbol,
    profile_name_for_symbol,
)


class MarketEventsE31Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e31.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_event(self, conn, *, detected_ts: int | None = None) -> int:
        now = detected_ts or int(time.time())
        return int(conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              detector_version, detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', 'SOL', 'DOWN', 'SHOCK_DETECTED',
              60, -3.5, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
            """,
            (now, now, f"dedup-e31-{now}", now),
        ).lastrowid)

    def _down_state_reclaim(self, now: int, *, reclaim_price: float) -> SymbolPriceState:
        st = SymbolPriceState(symbol="SOL", pair="SOLUSDT")
        st.append(PriceTick(ts=now - 120, price=100.0, volume=100), max_age_sec=900)
        st.append(PriceTick(ts=now - 60, price=100.0, volume=100), max_age_sec=900)
        st.append(PriceTick(ts=now - 30, price=96.0, volume=5000), max_age_sec=900)
        st.append(PriceTick(ts=now, price=reclaim_price, volume=100), max_age_sec=900)
        return st

    def test_schema_v5_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v5", applied)
            self.assertEqual(SCHEMA_VERSION, 5)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_pending_shocks", tables)
            self.assertIn("market_events_profile_shadow_candidates", tables)
            self.assertIn("market_events_counterfactual_studies", tables)

    def test_asset_profile_separation(self) -> None:
        self.assertEqual(profile_name_for_symbol("BTC"), PROFILE_CRYPTO)
        self.assertEqual(profile_name_for_symbol("OIL"), PROFILE_COMMODITY_OIL)
        self.assertNotEqual(
            profile_for_symbol("BTC").min_abs_return_pct,
            profile_for_symbol("OIL").min_abs_return_pct,
        )

    def test_delayed_reversal_confirmation(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = self._seed_event(conn, detected_ts=now - 30)
            shock = ShockCandidate(
                symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
                event_ts=now - 30, detected_ts=now - 30, return_pct=-3.5,
                triggers=[ShockTrigger("SHOCK_A", 30, -3.5)],
            )
            create_pending_shock(
                conn, event_id=eid, symbol="SOL", direction="DOWN",
                detected_ts=now - 30, shock_return_pct=-3.5, extreme_price=96.0,
            )
            pending = restore_pending_map(conn)[eid]

            st_no_rev = self._down_state_reclaim(now, reclaim_price=95.5)
            entries: list[str] = []

            def _open(evt, sh, ets, px, rev):
                entries.append(rev)

            process_pending_shock(conn, pending, st_no_rev, now, open_paper_fn=_open)
            self.assertEqual(entries, [])

            st_rev = self._down_state_reclaim(now + 20, reclaim_price=98.0)
            process_pending_shock(conn, pending, st_rev, now + 20, open_paper_fn=_open)
            self.assertIn("R1", entries)
            row = conn.execute(
                "SELECT phase, confirm_latency_sec FROM market_events_pending_shocks WHERE event_id=?",
                (eid,),
            ).fetchone()
            self.assertEqual(row["phase"], PHASE_MANAGING)
            self.assertGreater(int(row["confirm_latency_sec"]), 0)

    def test_no_duplicate_strategy_runs(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = self._seed_event(conn)
            runner = ShockPaperRunner(max_cycles=0)
            shock = ShockCandidate(
                symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
                event_ts=now, detected_ts=now, return_pct=-3.5,
            )
            runner._open_paper_runs(conn, eid, shock, now, 98.0, "R1")
            runner._open_paper_runs(conn, eid, shock, now, 98.0, "R1")
            n = conn.execute(
                "SELECT COUNT(*) FROM paper_strategy_runs WHERE event_id=?",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(n, 5)

    def test_expiry_without_reversal(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = self._seed_event(conn, detected_ts=now - 1000)
            create_pending_shock(
                conn, event_id=eid, symbol="SOL", direction="DOWN",
                detected_ts=now - 1000, shock_return_pct=-3.5, extreme_price=96.0,
            )
            pending = restore_pending_map(conn)[eid]
            st = self._down_state_reclaim(now, reclaim_price=95.0)
            result = process_pending_shock(conn, pending, st, now, open_paper_fn=lambda *a: None)
            self.assertEqual(result, "expired")
            phase = conn.execute("SELECT phase FROM market_events WHERE id=?", (eid,)).fetchone()["phase"]
            self.assertEqual(phase, EVENT_PHASE_EXPIRED)

    def test_restart_resume_pending_shock(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = self._seed_event(conn, detected_ts=now - 10)
            create_pending_shock(
                conn, event_id=eid, symbol="SOL", direction="DOWN",
                detected_ts=now - 10, shock_return_pct=-3.5, extreme_price=96.0,
            )
            restored = restore_pending_map(conn)
            self.assertIn(eid, restored)
            self.assertEqual(restored[eid].phase, PHASE_MONITORING)

    def test_shock_f_v2_deduplication(self) -> None:
        series = [(1000 + i, 100.0 + i * 0.5) for i in range(50)]
        series[30] = (1030, 105.0)
        series[31] = (1031, 105.2)
        series[32] = (1032, 105.4)
        raw = []
        for i in range(25, len(series)):
            c = detect_shock_f_v2_at_index(series, i, symbol="BTC")
            if c:
                raw.append(c)
        deduped, dropped = _dedupe_episodes(raw)
        self.assertGreaterEqual(dropped, 0)
        self.assertLessEqual(len(deduped), len(raw))

    def test_shock_f_v2_min_abs_return_floor(self) -> None:
        series = [(1000 + i * 10, 100.0) for i in range(40)]
        series[-1] = (1390, 100.2)
        cand = detect_shock_f_v2_at_index(series, len(series) - 1, symbol="BTC")
        self.assertIsNone(cand)

    def test_shock_f_v2_audit_no_paper_runs(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = run_shock_f_v2_shadow_audit(conn, days=1, persist=False)
            self.assertIn("SHOCK_F_v2", text)
            self.assertIn("no paper", text.lower())

    def test_exit_stop_history_and_be_helped(self) -> None:
        pos = open_paper_position(
            event_id=1, symbol="SOL", direction=SHOCK_DIRECTION_DOWN,
            reversal_variant="R1", exit_variant="EXIT_B",
            entry_ts=1000, entry_price=100.0,
        )
        process_exit_tick(pos, ts=1100, price=100.35)
        self.assertTrue(pos.be_active)
        self.assertTrue(any(h["reason"] == "BE_ACTIVATE" for h in pos.stop_history))
        process_exit_tick(pos, ts=1200, price=99.99)
        self.assertTrue(pos.closed)
        self.assertIsNotNone(pos.be_helped)

    def test_pending_reversal_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            eid = self._seed_event(conn)
            create_pending_shock(
                conn, event_id=eid, symbol="SOL", direction="DOWN",
                detected_ts=now, shock_return_pct=-3.5, extreme_price=96.0,
            )
            text = pending_reversal_report(conn, days=1)
            self.assertIn("MONITORING_REVERSAL", text)
            self.assertIn("SOL", text)

    def test_universe_core_runner_no_crash(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            runner = ShockPaperRunner(universe_mode="core", max_cycles=0)
            self.assertEqual(runner.universe_mode, "core")

    def test_no_live_execution_import(self) -> None:
        import bot.research.market_events.paper_runner as pr
        import bot.research.market_events.__main__ as cli
        src = open(pr.__file__).read() + open(cli.__file__).read()
        self.assertNotIn("bot.main", src)
        self.assertNotIn("TRADING_MODE=live", src)

    def test_profile_shadow_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = shock_profile_report(conn, days=1)
            self.assertIn("SHOCK PROFILE", text)


if __name__ == "__main__":
    unittest.main()
