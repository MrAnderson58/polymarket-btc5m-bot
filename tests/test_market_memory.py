"""G3.6 Market Memory tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_REJECTED,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.market_memory_g36 import (
    append_market_memory_g36,
    format_market_memory_cli_g36,
    market_memory_dashboard_g36,
    maybe_append_market_memory_g36,
)
from bot.research.market_events.signal_intelligence.watchlist_g36 import (
    add_watchlist_symbol_g36,
    format_watchlist_g36,
)


class MarketMemoryG36Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "memory.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {
            "ME_G36_VISION_MEMORY": "true",
            "ME_G36_MEMORY_INTERVAL_SEC": "0",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _seed(self, conn) -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (
              snapshot_uuid, snapshot_ts, funding, open_interest, fear_greed, btc_dominance,
              volume, atr, btc_price, recorder_status, created_at
            ) VALUES ('mem', ?, -0.02, 2e9, 40, 52.0, 5000, 100, 60000, 'ok', ?)
            """,
            (now, now),
        )
        sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        cand = CandidateG31(
            symbol="SOL", trend_score=65, market_score=63, liquidity_score=0.7,
            confidence=6.8, rr=2.3, btc_alignment="Neutral",
            funding_score=55, oi_score=58, volume_score=50, atr_score=45,
            fear_greed=30, candidate_state=STATE_REJECTED,
            rejection_reason="below threshold", direction="LONG",
        )
        persist_candidates_g31(conn, snapshot_id=sid, candidates=[cand], candidate_ts=now)
        add_watchlist_symbol_g36(conn, symbol="SOL")

    def test_schema_market_memory_table(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v40", applied)
            self.assertEqual(SCHEMA_VERSION, 40)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_market_memory)").fetchall()}
            self.assertIn("funding", cols)
            self.assertIn("claude_summary", cols)

    def test_append_memory(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed(conn)
            n = append_market_memory_g36(conn)
            conn.commit()
            self.assertGreater(n, 0)
            row = conn.execute(
                "SELECT * FROM market_market_memory WHERE symbol = 'SOL' ORDER BY id DESC LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(float(row["confidence"]), 6.8)

    def test_maybe_append_respects_interval(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed(conn)
            n1 = maybe_append_market_memory_g36(conn)
            n2 = maybe_append_market_memory_g36(conn)
            conn.commit()
            self.assertGreater(n1, 0)
            self.assertEqual(n2, 0)

    def test_market_memory_cli(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed(conn)
            append_market_memory_g36(conn)
            conn.commit()
            text = format_market_memory_cli_g36(conn, "SOL")
            self.assertIn("Market Memory", text)
            self.assertIn("Funding", text)
            self.assertIn("Confidence", text)

    def test_watchlist_format(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed(conn)
            text = format_watchlist_g36(conn)
            self.assertIn("SOL", text)
            self.assertIn("Confidence", text)

    def test_dashboard_payload(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed(conn)
            append_market_memory_g36(conn)
            conn.commit()
            dash = market_memory_dashboard_g36(conn)
            self.assertEqual(dash["tab"], "Market Memory")
            self.assertGreater(dash["total_rows"], 0)


if __name__ == "__main__":
    unittest.main()
