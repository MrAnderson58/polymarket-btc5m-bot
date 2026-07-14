"""G3.2 candidate replay and threshold optimizer tests."""

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
    CandidateG31,
    STATE_REJECTED,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.missed_opportunities_g32 import (
    build_missed_opportunities_telegram_g32,
)
from bot.research.market_events.signal_intelligence.replay_g32 import (
    STATUS_OPEN,
    format_candidate_replay_report,
    seed_outcome_g32,
    update_open_outcomes_g32,
)
from bot.research.market_events.signal_intelligence.threshold_optimizer_g32 import (
    format_threshold_optimizer_report,
    run_threshold_optimizer_g32,
)


def _seed_candles(conn, *, symbol: str, n: int = 80, drift: float = 0.005) -> None:
    now = int(time.time())
    price = 100.0
    for i in range(n):
        ts = now - (n - i) * 300
        o = price
        price = price * (1.0 + drift)
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, 2000, 'test', ?)
            """,
            (symbol, ts, o, max(o, price) * 1.002, min(o, price) * 0.998, price, now),
        )


class G32ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g32.db"
        self._env = patch.dict(os.environ, {
            "ME_G32_CANDIDATE_REPLAY": "true",
            "ME_G31_CANDIDATE_PIPELINE": "true",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v31(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v37", applied)
            self.assertEqual(SCHEMA_VERSION, 38)

    def test_outcome_seeded_and_updated(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            _seed_candles(conn, symbol="SOL", drift=0.008)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, recorder_status, created_at
                ) VALUES ('snap1', ?, 'ok', ?)
                """,
                (now, now),
            )
            sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            candidates = [
                CandidateG31(
                    symbol="SOL", trend_score=70, market_score=63, liquidity_score=55,
                    confidence=7.3, rr=2.1, btc_alignment="Neutral",
                    funding_score=60, oi_score=70, volume_score=55, atr_score=50,
                    fear_greed=30, candidate_state=STATE_REJECTED,
                    rejection_reason="Market Score 63 < 65", direction="LONG",
                ),
            ]
            persist_candidates_g31(conn, snapshot_id=sid, candidates=candidates, candidate_ts=now)
            cid = conn.execute("SELECT id FROM market_candidate_g31 LIMIT 1").fetchone()[0]
            row = conn.execute(
                "SELECT 1 FROM market_candidate_outcomes_g32 WHERE candidate_id = ?",
                (cid,),
            ).fetchone()
            self.assertIsNotNone(row)

            n = update_open_outcomes_g32(conn)
            self.assertGreaterEqual(n, 1)
            outcome = conn.execute(
                "SELECT max_profit_pct, replay_status FROM market_candidate_outcomes_g32 WHERE candidate_id = ?",
                (cid,),
            ).fetchone()
            self.assertIsNotNone(outcome["max_profit_pct"])

    def test_threshold_optimizer_and_replay_cli(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time()) - 7200
            _seed_candles(conn, symbol="BTC")
            cid = conn.execute(
                """
                INSERT INTO market_candidate_g31 (
                  candidate_ts, symbol, confidence, market_score, rr,
                  candidate_state, rejection_reason, direction, created_at
                ) VALUES (?, 'BTC', 7.3, 63, 2.1, 'rejected', 'Market Score 63 < 65', 'LONG', ?)
                """,
                (now, now),
            ).lastrowid
            seed_outcome_g32(conn, candidate_id=int(cid), symbol="BTC", direction="LONG", created_at=now, rr=2.1)
            conn.execute(
                """
                UPDATE market_candidate_outcomes_g32 SET
                  max_profit_pct = 5.8, max_drawdown_pct = -0.5,
                  would_hit_tp = 1, replay_status = 'COMPLETE', price_24h = 105
                WHERE candidate_id = ?
                """,
                (cid,),
            )

            scenarios = run_threshold_optimizer_g32(conn, days=7)
            self.assertTrue(any(s.sample_size > 0 for s in scenarios))
            report = format_threshold_optimizer_report(conn, days=7)
            self.assertIn("Threshold Optimizer", report)
            replay = format_candidate_replay_report(conn, limit=5)
            self.assertIn("BTC", replay)

    def test_missed_opportunities_telegram(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time()) - 3600
            cid = conn.execute(
                """
                INSERT INTO market_candidate_g31 (
                  candidate_ts, symbol, confidence, market_score, rr,
                  candidate_state, rejection_reason, direction, created_at
                ) VALUES (?, 'SOL', 7.3, 70, 2.5, 'rejected', 'Confidence 7.3 < 7.5', 'LONG', ?)
                """,
                (now, now),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO market_candidate_outcomes_g32 (
                  candidate_id, symbol, direction, created_at, price_entry,
                  max_profit_pct, replay_status, updated_at
                ) VALUES (?, 'SOL', 'LONG', ?, 100, 5.8, 'COMPLETE', ?)
                """,
                (cid, now, now),
            )
            msg = build_missed_opportunities_telegram_g32(conn)
            if msg:
                self.assertIn("упущены", msg)
                self.assertIn("SOL", msg)


if __name__ == "__main__":
    unittest.main()
