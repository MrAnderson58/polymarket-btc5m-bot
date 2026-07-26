"""Adaptive shock profile shadow A/B — isolated from production pipeline."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.adaptive_shock_shadow import (
    ShadowRunnerState,
    assert_baseline_unchanged,
    evaluate_detectors_for_profile,
    flush_shadow_writes,
    ingest_shadow_cycle,
    persist_shadow_evals,
    shadow_profile_report,
    shadow_profiles_dashboard,
)
from bot.research.market_events.collector_heartbeat import CollectorMetrics
from bot.research.market_events.config import (
    ADAPTIVE_V1_THRESHOLDS,
    PROFILE_ADAPTIVE_V1,
    PROFILE_BASELINE,
    SHOCK_THRESHOLDS,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.price_feed import PriceTick, SymbolPriceState
from bot.research.market_events.shock_detector import detect_shock_triggers


def _state_with_move(symbol: str, *, ret_pct: float, window_sec: int = 30) -> SymbolPriceState:
    now = int(time.time())
    px1 = 100.0
    px2 = px1 * (1.0 + ret_pct / 100.0)
    st = SymbolPriceState(symbol=symbol, pair=f"{symbol}USDT")
    st.append(PriceTick(ts=now - window_sec, price=px1, volume=1.0), max_age_sec=300)
    st.append(PriceTick(ts=now, price=px2, volume=1.0), max_age_sec=300)
    return st


class TestAdaptiveShockShadow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "me.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_baseline_thresholds_unchanged(self) -> None:
        assert_baseline_unchanged()
        self.assertEqual(SHOCK_THRESHOLDS["SHOCK_A"]["min_abs_return_pct"], 1.5)
        self.assertEqual(ADAPTIVE_V1_THRESHOLDS["SHOCK_A"]["min_abs_return_pct"], 0.30)

    def test_adaptive_accepts_where_baseline_rejects(self) -> None:
        now = int(time.time())
        st = _state_with_move("SOL", ret_pct=0.40, window_sec=30)
        base = evaluate_detectors_for_profile(
            st, profile_name=PROFILE_BASELINE, thresholds=SHOCK_THRESHOLDS, now_ts=now,
        )
        adap = evaluate_detectors_for_profile(
            st, profile_name=PROFILE_ADAPTIVE_V1, thresholds=ADAPTIVE_V1_THRESHOLDS, now_ts=now,
        )
        base_a = next(e for e in base if e.detector == "SHOCK_A")
        adap_a = next(e for e in adap if e.detector == "SHOCK_A")
        self.assertFalse(base_a.accepted)
        self.assertTrue(adap_a.accepted)

    def test_production_detector_still_baseline(self) -> None:
        now = int(time.time())
        st = _state_with_move("ETH", ret_pct=0.40, window_sec=30)
        triggers = detect_shock_triggers(st, now_ts=now)
        self.assertEqual(triggers, [])

    def test_shadow_table_and_report(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_shadow'",
            ).fetchone()
            self.assertIsNotNone(row)

            now = int(time.time())
            st = _state_with_move("BTC", ret_pct=0.45, window_sec=30)
            evals = evaluate_detectors_for_profile(
                st, profile_name=PROFILE_ADAPTIVE_V1,
                thresholds=ADAPTIVE_V1_THRESHOLDS, now_ts=now,
            )
            state = ShadowRunnerState()
            ingest_shadow_cycle(state, evals, now_ts=now)
            # Force reject flush into buffer for report coverage.
            state.pending_writes.extend(state.pending_rejects.values())
            state.pending_rejects.clear()
            n = flush_shadow_writes(conn, state)
            self.assertGreater(n, 0)
            conn.commit()

            report = shadow_profile_report(conn, days=1)
            self.assertIn("PROFILE", report)
            self.assertIn("baseline", report)
            self.assertIn("adaptive_v1", report)
            self.assertIn("SHOCK_A", report)

            dash = shadow_profiles_dashboard(conn, days=1)
            self.assertEqual(dash["tab"], "Shadow Profiles")
            self.assertIn(PROFILE_ADAPTIVE_V1, dash["profiles"])

    def test_heartbeat_shadow_block(self) -> None:
        m = CollectorMetrics()
        m.shadow_state.metrics_for(PROFILE_ADAPTIVE_V1).checked = 10
        m.shadow_state.metrics_for(PROFILE_ADAPTIVE_V1).accepted = 2
        m.shadow_state.metrics_for(PROFILE_ADAPTIVE_V1).rejected = 8
        text = m.render_heartbeat(
            universe="core", events_detected=0, pending_reversals=0, paper_runs_open=0,
        )
        self.assertIn("shadow adaptive_v1", text)
        self.assertIn("checked=10", text)
        self.assertIn("accepted=2", text)
        self.assertIn("accept_rate=", text)

    def test_cli_shadow_report_registered(self) -> None:
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertIn("shadow-report", proc.stdout + proc.stderr)
        self.assertIn("g40-shadow-report", proc.stdout + proc.stderr)

    def test_batched_persist_is_lock_retried(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            rows = []
            for i in range(25):
                st = _state_with_move(f"T{i}", ret_pct=0.40, window_sec=30)
                rows.extend(
                    evaluate_detectors_for_profile(
                        st, profile_name=PROFILE_ADAPTIVE_V1,
                        thresholds=ADAPTIVE_V1_THRESHOLDS, now_ts=now,
                    ),
                )
            n = persist_shadow_evals(conn, rows)
            self.assertEqual(n, len(rows))
            conn.commit()
            count = conn.execute("SELECT COUNT(*) AS n FROM market_events_shadow").fetchone()["n"]
            self.assertGreaterEqual(int(count), 25)


if __name__ == "__main__":
    unittest.main()
