"""G5.1 Research Data Lake tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candle_pattern_g51 import build_candle_patterns_g51
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_REJECTED,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.claude_json_parser_g501 import (
    extract_json_from_claude_text,
)
from bot.research.market_events.signal_intelligence.liquidity_history_g51 import build_liquidity_history_g51
from bot.research.market_events.signal_intelligence.replay_timeline_g51 import build_replay_timeline_g51
from bot.research.market_events.signal_intelligence.research_dataset_g51 import (
    audit_research_dataset_g51,
    build_research_lake_g51,
    filter_missing_info_g51,
    format_dataset_telegram_g51,
    format_research_data_audit_g51,
    query_research_dataset_g51,
)
from bot.research.market_events.signal_intelligence.research_lake_types_g51 import G51_WINDOWS
from bot.research.market_events.signal_intelligence.snapshot_history_g51 import build_snapshot_history_g51
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)


class ResearchDataLakeG51Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g51.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {
            "ME_G51_DATA_LAKE": "true",
            "ME_G32_CANDIDATE_REPLAY": "true",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _seed_candles(self, conn, *, symbol: str = "SOL", n: int = 320) -> None:
        now = int(time.time())
        base = 100.0
        for i in range(n):
            ts = now - (n - i) * 300
            price = base + i * 0.05
            conn.execute(
                """
                INSERT OR IGNORE INTO market_events_historical_candles (
                  venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
                ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, ?, 'test', ?)
                """,
                (symbol, ts, price, price + 0.2, price - 0.2, price + 0.05, 1000 + i, now),
            )

    def _seed_snapshots(self, conn, *, n: int = 20) -> None:
        now = int(time.time())
        for i in range(n):
            ts = now - (n - i) * 1800
            funding = -0.01 - i * 0.01
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, btc_price, eth_price, btc_dominance,
                  funding, open_interest, liquidations, volume, atr, fear_greed,
                  recorder_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                """,
                (
                    f"g51-snap-{i}", ts, 60000 + i, 3000 + i, 52.0,
                    funding, 1e9 + i * 1e6, 100 + i, 5000 + i, 500.0, 45 + i,
                    ts,
                ),
            )

    def _seed_candidate(self, conn) -> int:
        now = int(time.time())
        self._seed_candles(conn)
        self._seed_snapshots(conn)
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (snapshot_uuid, snapshot_ts, recorder_status, created_at)
            VALUES ('g51-cand-snap', ?, 'ok', ?)
            """,
            (now, now),
        )
        sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        cand = CandidateG31(
            symbol="SOL", trend_score=60, market_score=62, liquidity_score=0.7,
            confidence=7.2, rr=2.3, btc_alignment="Neutral",
            funding_score=55, oi_score=58, volume_score=50, atr_score=45,
            fear_greed=30, candidate_state=STATE_REJECTED,
            rejection_reason="Market Score below threshold", direction="SHORT",
        )
        persist_candidates_g31(conn, snapshot_id=sid, candidates=[cand], candidate_ts=now)
        row = conn.execute(
            "SELECT id FROM market_candidate_g31 WHERE snapshot_id = ? AND symbol = 'SOL' ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        return int(row["id"])

    def test_schema_v39(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v41", applied)
            self.assertEqual(SCHEMA_VERSION, 41)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_snapshot_history_g51", tables)
            self.assertIn("market_research_dataset_g51", tables)
            view = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' AND name='research_dataset'",
            ).fetchone()
            self.assertIsNotNone(view)

    def test_snapshot_history_all_windows(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            cid = self._seed_candidate(conn)
            now = int(time.time())
            rows = build_snapshot_history_g51(conn, candidate_id=cid, symbol="SOL", anchor_ts=now)
            self.assertEqual(len(rows), len(G51_WINDOWS))
            keys = {r["window_key"] for r in rows}
            self.assertEqual(keys, set(G51_WINDOWS.keys()))

    def test_candle_patterns(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            cid = self._seed_candidate(conn)
            now = int(time.time())
            rows = build_candle_patterns_g51(conn, candidate_id=cid, symbol="SOL", anchor_ts=now)
            self.assertGreater(len(rows), 0)
            self.assertTrue(all(r["pattern"] for r in rows))
            self.assertIn("green_pct", rows[0])

    def test_liquidity_evolution(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            cid = self._seed_candidate(conn)
            now = int(time.time())
            rows = build_liquidity_history_g51(conn, candidate_id=cid, symbol="SOL", anchor_ts=now)
            funding = next((r for r in rows if r["metric"] == "funding"), None)
            self.assertIsNotNone(funding)
            self.assertIn("↓", funding["evolution_text"])

    def test_replay_timeline(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            cid = self._seed_candidate(conn)
            now = int(time.time())
            rows = build_replay_timeline_g51(
                conn, candidate_id=cid, symbol="SOL", anchor_ts=now - 3600,
                direction="SHORT", entry_price=105.0,
            )
            self.assertGreaterEqual(len(rows), 4)
            self.assertIn("pnl_pct", rows[0])

    def test_sql_dataset_query(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            build_research_lake_g51(conn, days=30, limit=10)
            conn.commit()
            rows = query_research_dataset_g51(conn, symbol="SOL")
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "SOL")

    def test_research_data_audit_cli(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            build_research_lake_g51(conn, days=30, limit=10)
            conn.commit()
            text = format_research_data_audit_g51(conn)
            self.assertIn("Dataset completeness", text)
            self.assertIn("Funding", text)
            self.assertIn("Candles", text)

    def test_missing_info_filter(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            build_research_lake_g51(conn, days=30, limit=10)
            conn.commit()
            rows = query_research_dataset_g51(conn, symbol="SOL")
            filtered = filter_missing_info_g51(
                ["Funding history", "Need more data"],
                dataset_rows=rows,
            )
            labels = [str(x) for x in filtered]
            self.assertNotIn("Funding history", labels)
            self.assertIn("Need more data", labels)

    def test_dataset_telegram(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            build_research_lake_g51(conn, days=30, limit=10)
            conn.commit()
        result = handle_market_events_command("/dataset")
        self.assertTrue(result.ok)
        self.assertIn("Research Dataset", result.reply_text)
        self.assertIn("Completeness", result.reply_text)

    def test_audit_completeness_fields(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            build_research_lake_g51(conn, days=30, limit=10)
            conn.commit()
            audit = audit_research_dataset_g51(conn)
            for key in ("funding_pct", "oi_pct", "replay_pct", "candles_pct"):
                self.assertIn(key, audit)


if __name__ == "__main__":
    unittest.main()
