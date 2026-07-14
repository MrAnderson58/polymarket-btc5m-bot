"""G3.1 candidate pipeline tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    STATE_REJECTED,
    build_candidates_g31,
    format_candidate_stats,
    format_candidates_report,
    load_g31_universe_symbols,
    persist_candidates_g31,
    run_candidate_pipeline_g31,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import (
    LiquidityStateG3,
    compute_liquidity_state_g3,
    persist_liquidity_state_g3,
)
from bot.research.market_events.signal_intelligence.recorder_g3 import (
    SnapshotPayloadG3,
    persist_snapshot_g3,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import (
    TrendWindowG3,
    run_trend_detection_g3,
)


def _seed_candles(conn, *, symbol: str, n: int = 80) -> None:
    now = int(time.time())
    price = 100.0
    for i in range(n):
        ts = now - (n - i) * 300
        o, c = price, price * 0.995
        price = c
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, 2000, 'test', ?)
            """,
            (symbol, ts, o, max(o, c) * 1.002, min(o, c) * 0.998, c, now),
        )


class CandidateG31Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g31.db"
        self._env = patch.dict(os.environ, {"ME_G31_CANDIDATE_PIPELINE": "true"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v30(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v37", applied)
            self.assertEqual(SCHEMA_VERSION, 40)

    def test_universe_includes_extended_symbols(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            symbols = load_g31_universe_symbols(conn)
            for sym in ("BTC", "ETH", "SOL", "MANTA", "SUI", "DOGE", "XRP", "BNB", "TON", "ADA"):
                self.assertIn(sym, symbols)

    def test_candidate_pipeline_persists_all_symbols(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            symbols = load_g31_universe_symbols(conn)
            for sym in symbols[:8]:
                _seed_candles(conn, symbol=sym)

            payload = SnapshotPayloadG3(
                snapshot_uuid=f"u-{int(time.time())}",
                snapshot_ts=int(time.time()),
                funding=-0.0002,
                fear_greed=30.0,
                atr=2.0,
            )
            sid = persist_snapshot_g3(conn, payload)
            trends = run_trend_detection_g3(conn, snapshot_id=sid, symbols=symbols[:8])
            liquidity = compute_liquidity_state_g3(conn, snapshot_id=sid)
            persist_liquidity_state_g3(conn, snapshot_id=sid, state=liquidity)

            candidates = run_candidate_pipeline_g31(
                conn, snapshot_id=sid, trends=trends, liquidity=liquidity,
            )
            self.assertGreaterEqual(len(candidates), 8)

            row = conn.execute(
                "SELECT COUNT(*) AS n FROM market_candidate_g31 WHERE snapshot_id = ?",
                (sid,),
            ).fetchone()
            self.assertEqual(int(row["n"]), len(symbols))

    def test_cli_reports(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            _seed_candles(conn, symbol="SOL", n=80)
            sid = persist_snapshot_g3(conn, SnapshotPayloadG3(
                snapshot_uuid="cli-test", snapshot_ts=int(time.time()), funding=-0.0001,
            ))
            trends = [
                TrendWindowG3(
                    symbol="SOL", window_minutes=15, pattern_type="slow_bleed",
                    consecutive_candles=12, trend_score=75.0, direction="DOWN",
                    details={"description": "12 red candles"},
                ),
            ]
            liquidity = LiquidityStateG3(
                primary_state="Capitulation",
                probabilities={"Capitulation": 0.8},
                factors={"funding": -0.0001, "oi_rising": True, "liquidations": 100},
            )
            candidates = build_candidates_g31(
                conn, snapshot_id=sid, trends=trends, liquidity=liquidity,
            )
            persist_candidates_g31(conn, snapshot_id=sid, candidates=candidates)

            top = format_candidates_report(conn, limit=5)
            self.assertIn("Coverage", top)
            stats = format_candidate_stats(conn, hours=24)
            self.assertIn("Total candidates", stats)


if __name__ == "__main__":
    unittest.main()
