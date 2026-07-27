"""Tests for paper-gate funnel, MFE queue, and lock diagnostics."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.paper_gate_funnel_s55 import (
    build_paper_gate_funnel,
    format_paper_gate_funnel,
)
from bot.research.market_events.sqlite_manager_g05 import (
    cluster_lock_incidents,
    format_lock_diagnostics_block,
    summarize_lock_diagnostics,
)


class PaperGateFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "funnel.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_funnel_counts_negative_expectancy(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            for i in range(3):
                est = {
                    "expected_pnl_pct": -0.12,
                    "winrate": 41.0,
                    "p_tp1": 30.0,
                    "p_sl": 40.0,
                    "n": 50,
                    "similar_count": 50,
                }
                blob = json.dumps({"gate_estimate": est})
                conn.execute(
                    """
                    INSERT INTO market_events_trade_features_s55 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      features_json, gate_decision, gate_expected_pnl_pct,
                      similar_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "g3_signal",
                        i + 1,
                        "BTC",
                        "LONG",
                        blob,
                        "NEGATIVE_EXPECTANCY",
                        -0.12,
                        50,
                        now,
                    ),
                )
            conn.commit()
            data = build_paper_gate_funnel(conn, since_ts=now - 10)
            self.assertEqual(data["funnel"]["candidates"], 3)
            self.assertEqual(data["funnel"]["rejected_negative_expectancy"], 3)
            self.assertEqual(data["funnel"]["opened"], 0)
            self.assertEqual(len(data["rejects"]), 3)
            self.assertAlmostEqual(float(data["rejects"][0]["ev"]), -0.12)
            self.assertAlmostEqual(float(data["rejects"][0]["probability"]), 0.41)
            text = format_paper_gate_funnel(conn, since_ts=now - 10)
            self.assertIn("NEGATIVE_EXPECTANCY", text)
            self.assertIn("Отсеяно", text)


class LockDiagnosticsTests(unittest.TestCase):
    def test_cluster_and_summary_shape(self) -> None:
        base = time.time()
        events = [
            {"ts": base, "pid": 1, "table": "t1", "sql": "a", "locked": True},
            {"ts": base + 0.1, "pid": 1, "table": "t1", "sql": "a", "locked": True},
            {"ts": base + 0.2, "pid": 1, "table": "t1", "sql": "b", "locked": True},
            {"ts": base + 5.0, "pid": 1, "table": "t1", "sql": "c", "locked": True},
            {"ts": base, "pid": 2, "table": "t2", "sql": "d", "locked": True},
        ]
        incidents = cluster_lock_incidents(events, gap_sec=2.0)
        self.assertEqual(len(incidents), 3)  # pid1 burst + pid1 later + pid2
        with patch(
            "bot.research.market_events.sqlite_manager_g05.get_recent_lock_events",
            return_value=events,
        ), patch(
            "bot.research.market_events.sqlite_manager_g05.get_integrity_counts",
            return_value={"writes_lost": 0, "paper_trades_lost": 0, "mfe_mae_deferred": 1},
        ):
            summary = summarize_lock_diagnostics(lookback_sec=300)
        self.assertEqual(summary["raw_lock_events"], 5)
        self.assertEqual(summary["real_lock_incidents"], 3)
        self.assertEqual(summary["retry_attempts"], 2)
        self.assertEqual(summary["writes_lost"], 0)
        block = "\n".join(format_lock_diagnostics_block(summary))
        self.assertIn("Raw lock events", block)
        self.assertIn("Real lock incidents", block)


class MfeQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "mfe.db"
        self.queue = Path(self._tmpdir.name) / "mfe-queue.jsonl"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_enqueue_and_flush(self) -> None:
        from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42

        with patch.object(s42, "_MFE_QUEUE_PATH", self.queue):
            with market_events_connection() as conn:
                apply_migrations(conn)
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      mfe_pct, mae_pct, capital_usd, leverage, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "g3_signal",
                        99,
                        "BTC",
                        "LONG",
                        100.0,
                        99.0,
                        101.0,
                        102.0,
                        int(time.time()),
                        "OPEN",
                        0.0,
                        0.0,
                        100.0,
                        20,
                        int(time.time()),
                    ),
                )
                conn.commit()
                tid = int(
                    conn.execute(
                        "SELECT id FROM market_events_paper_trades_s42 WHERE s40_signal_id=99"
                    ).fetchone()["id"]
                )
                s42._enqueue_mfe_mae_update(
                    trade_id=tid, mfe_pct=1.5, mae_pct=-0.2, updated_at=int(time.time()),
                )
                self.assertTrue(self.queue.is_file())
                n = s42._flush_mfe_mae_queue(conn)
                conn.commit()
                self.assertEqual(n, 1)
                row = conn.execute(
                    "SELECT mfe_pct, mae_pct FROM market_events_paper_trades_s42 WHERE id=?",
                    (tid,),
                ).fetchone()
                self.assertAlmostEqual(float(row["mfe_pct"]), 1.5)
                self.assertAlmostEqual(float(row["mae_pct"]), -0.2)


if __name__ == "__main__":
    unittest.main()
