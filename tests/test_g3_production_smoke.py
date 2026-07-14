"""G3 production smoke test — recorder, trends, pipeline, telegram render, health."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.health_g3 import format_g3_health_report
from bot.research.market_events.signal_intelligence.recorder_g3 import (
    SnapshotPayloadG3,
    persist_snapshot_g3,
)
from bot.research.market_events.signal_intelligence.runner_g3 import run_g3_cycle
from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
    STAGE_F7_COMPLETED,
    STAGE_F7_SKIPPED,
)
from bot.research.market_events.telegram_ops.cli import run_demo_event


def _seed_candles(conn, *, symbol: str, n: int = 80, down: bool = True) -> None:
    now = 1_700_100_000
    price = 150.0
    for i in range(n):
        ts = now - (n - i) * 300
        if down:
            o, c = price, price * 0.995
        else:
            o, c = price, price * 1.005
        price = c
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, 1000, 'test', ?)
            """,
            (symbol, ts, o, max(o, c) * 1.002, min(o, c) * 0.998, c, now),
        )


class G3ProductionSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g3_smoke.db"
        self._env = patch.dict(os.environ, {
            "ME_G3_LIVE_SIGNAL": "true",
            "ME_G2_CLAUDE_RESEARCH": "true",
            "ANTHROPIC_API_KEY": "",
            "ME_TELEGRAM_ALERTS_ENABLED": "false",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _mock_snapshot(self) -> SnapshotPayloadG3:
        now = int(time.time())
        return SnapshotPayloadG3(
            snapshot_uuid=f"test-snapshot-{now}",
            snapshot_ts=now,
            btc_price=65000.0,
            eth_price=3500.0,
            sol_price=150.0,
            bnb_price=600.0,
            total3=-2.5,
            btc_dominance=52.0,
            funding=-0.0002,
            open_interest=1_000_000.0,
            liquidations=500.0,
            volume=12000.0,
            atr=2.5,
            fear_greed=25.0,
            volume_delta=100.0,
            exchange_ts=now - 5,
            collector_latency_ms=42.0,
            recorder_status="ok",
        )

    def test_g3_production_smoke(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v37", applied)
            self.assertEqual(SCHEMA_VERSION, 37)

            for sym in ("SOL", "ETH", "BNB", "BTC", "TOTAL3", "SUI"):
                _seed_candles(conn, symbol=sym, n=100, down=True)

            mock_payload = self._mock_snapshot()
            with patch(
                "bot.research.market_events.signal_intelligence.recorder_g3.collect_snapshot_g3",
                return_value=mock_payload,
            ):
                stats = run_g3_cycle(conn)
            conn.commit()

            self.assertEqual(stats.errors, 0)
            self.assertIsNotNone(stats.last_snapshot_id)

            snap = conn.execute("SELECT 1 FROM market_snapshots_g3 LIMIT 1").fetchone()
            self.assertIsNotNone(snap)

            trend = conn.execute("SELECT 1 FROM market_trend_windows_g3 LIMIT 1").fetchone()
            self.assertIsNotNone(trend)

            liq = conn.execute("SELECT 1 FROM market_liquidity_state_g3 LIMIT 1").fetchone()
            self.assertIsNotNone(liq)

            health = format_g3_health_report(conn)
            self.assertIn("G3 Live Signal Engine", health)

            code, demo_text = run_demo_event(conn, force_g2=True)
            conn.commit()
            self.assertEqual(code, 0)
            self.assertIn("F0→G1→F5→F7→G2 pipeline", demo_text)

            f5 = conn.execute("SELECT 1 FROM market_events_signal_reports_f5 LIMIT 1").fetchone()
            g2 = conn.execute("SELECT 1 FROM market_events_ai_research_g2 LIMIT 1").fetchone()
            self.assertIsNotNone(f5)
            self.assertIsNotNone(g2)
            f7_trace = conn.execute(
                """
                SELECT stage FROM market_events_signal_trace_f51
                WHERE stage IN (?, ?) ORDER BY id DESC LIMIT 1
                """,
                (STAGE_F7_COMPLETED, STAGE_F7_SKIPPED),
            ).fetchone()
            self.assertIsNotNone(f7_trace)
            self.assertTrue(
                "F7 completed" in demo_text or "F7 skipped" in demo_text,
            )

            event_id = conn.execute("SELECT id FROM market_events ORDER BY id DESC LIMIT 1").fetchone()[0]
            with patch(
                "bot.research.market_events.signal_intelligence.recorder_g3.collect_snapshot_g3",
                return_value=self._mock_snapshot(),
            ), patch(
                "bot.research.market_events.signal_intelligence.signal_generator_g3.send_live_signal_telegram_g3",
                return_value=False,
            ):
                from bot.research.market_events.signal_intelligence.signal_generator_g3 import run_g3_for_event
                sig = run_g3_for_event(conn, int(event_id))
            conn.commit()

            if sig:
                self.assertIn("SOLUSDT", sig.telegram_rendered)
                self.assertIn("Claude Research", sig.telegram_rendered)
                paper = conn.execute(
                    "SELECT 1 FROM paper_strategy_runs WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                self.assertIsNotNone(paper)

            ops = conn.execute(
                "SELECT value FROM market_events_g3_ops_state WHERE key = 'recorder_health'",
            ).fetchone()
            self.assertIsNotNone(ops)


if __name__ == "__main__":
    unittest.main()
