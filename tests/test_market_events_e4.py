"""Phase E.4 historical replay + context intelligence tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.schema import STAGE7_VERSION, apply_migrations as apply_agent_migrations
from bot.research.futures_agent.telegram_multi_intent import (
    analyze_multi_intent,
    extract_deterministic_intents,
    persist_multi_intent,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.historical_replay.candle_backfill import insert_candle_ignore
from bot.research.market_events.historical_replay.constants import REPLAY_RUN_TAG_DEFAULT
from bot.research.market_events.historical_replay.context_linker import link_replay_context
from bot.research.market_events.historical_replay.coverage import historical_replay_coverage
from bot.research.market_events.historical_replay.runner import run_full_replay_pipeline
from bot.research.market_events.historical_replay.shock_replay import run_shock_replay
from bot.research.market_events.historical_replay.splits import (
    compute_split_boundaries,
    persist_replay_split,
    split_bucket_for_ts,
)
from bot.research.market_events.instrument_master import InstrumentRecord, upsert_instrument


UNI_FIXTURE = (
    "$UNI лонг вход 3.099 тейки: 3.179, 3.3 стоп 2.925 "
    "вход с 25 плечем на 100 долларов банк уже 955 долларов"
)


class MarketEventsE4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e4.db"
        self.agent_db_path = Path(self._tmpdir.name) / "fa_e4.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _agent_conn(self):
        from bot.research.futures_agent.db import agent_connection
        return agent_connection(url=f"sqlite:///{self.agent_db_path}")

    def _seed_instrument(self, conn, *, symbol: str = "BTC", asset_class: str = "CRYPTO") -> int:
        return upsert_instrument(
            conn,
            InstrumentRecord(
                venue="binance_futures",
                venue_symbol=f"{symbol}USDT",
                canonical_asset=symbol,
                reference_asset=symbol,
                asset_class=asset_class,
                instrument_type="PERP",
                quote_currency="USDT",
                trading_hours_mode="24x7",
                price_source="trade",
                reference_price_source="trade",
                liquidity_tier="CORE",
                active=1,
            ),
        )

    def _seed_observations(self, conn, iid: int, prices: list[tuple[int, float]]) -> None:
        for ts, px in prices:
            conn.execute(
                """
                INSERT INTO market_events_price_observations (
                  instrument_id, obs_ts, trade_price, session_regime
                ) VALUES (?, ?, ?, 'US')
                """,
                (iid, ts, px),
            )

    def test_schema_v8_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v8", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 8)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            for t in (
                "market_events_historical_candles",
                "market_events_replay_runs",
                "market_events_replay_shocks",
                "market_events_replay_path_metrics",
                "market_events_replay_strategy_results",
                "market_events_replay_context_links",
                "market_events_replay_ai_critic",
            ):
                self.assertIn(t, tables)

    def test_replay_isolation_from_production_market_events(self) -> None:
        base = int(time.time()) - 3600
        prices = [(base + i * 30, 100.0 + (5.0 if i == 40 else 0)) for i in range(80)]
        with self._conn() as conn:
            apply_migrations(conn)
            prod_before = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            iid = self._seed_instrument(conn)
            self._seed_observations(conn, iid, prices)
            stats = run_shock_replay(conn, run_tag="e4_iso", days=1)
            prod_after = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            replay_n = conn.execute(
                "SELECT COUNT(*) FROM market_events_replay_shocks",
            ).fetchone()[0]
            self.assertEqual(prod_before, prod_after)
            self.assertGreaterEqual(replay_n, 0)
            self.assertIn("run_id", stats)

    def test_chronological_split_integrity(self) -> None:
        ts = list(range(1_700_000_000, 1_700_000_000 + 20 * 300, 300))
        bounds = compute_split_boundaries(ts)
        self.assertEqual(bounds["verdict"], "OK")
        with self._conn() as conn:
            apply_migrations(conn)
            persist_replay_split(conn, run_tag="split_test", event_timestamps=ts)
            self.assertEqual(split_bucket_for_ts(conn, run_tag="split_test", event_ts=ts[0]), "train")
            self.assertEqual(split_bucket_for_ts(conn, run_tag="split_test", event_ts=ts[-1]), "holdout")

    def test_insufficient_data_split_verdict(self) -> None:
        bounds = compute_split_boundaries([1, 2, 3])
        self.assertEqual(bounds["verdict"], "INSUFFICIENT_DATA")

    def test_idempotent_candle_backfill(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            ts = int(time.time()) - 120
            ok1 = insert_candle_ignore(
                conn, venue="binance_futures", symbol="BTC", timeframe="1m",
                open_ts=ts, o=1, h=2, l=0.5, c=1.5, source="test",
            )
            ok2 = insert_candle_ignore(
                conn, venue="binance_futures", symbol="BTC", timeframe="1m",
                open_ts=ts, o=1, h=2, l=0.5, c=1.5, source="test",
            )
            n = conn.execute(
                "SELECT COUNT(*) FROM market_events_historical_candles",
            ).fetchone()[0]
            self.assertTrue(ok1)
            self.assertFalse(ok2)
            self.assertEqual(n, 1)

    def test_no_lookahead_context(self) -> None:
        event_ts = int(time.time()) - 100
        with self._conn() as conn:
            apply_migrations(conn)
            run_id = conn.execute(
                """
                INSERT INTO market_events_replay_runs (
                  run_tag, data_source, detector_version, profile_version,
                  status, created_at
                ) VALUES ('ctx_test', 'HISTORICAL_REPLAY', 'v1', 'v1', 'complete', ?)
                """,
                (int(time.time()),),
            ).lastrowid
            shock_id = conn.execute(
                """
                INSERT INTO market_events_replay_shocks (
                  run_id, symbol, event_ts, direction, detector_id, impulse_pct,
                  source_provenance, created_at
                ) VALUES (?, 'BTC', ?, 'DOWN', 'SHOCK_A', -2.0, 'test', ?)
                """,
                (run_id, event_ts, int(time.time())),
            ).lastrowid

        with self._agent_conn() as agent:
            apply_agent_migrations(agent)
            agent.execute(
                """
                INSERT INTO futures_agent_trader_posts (
                  source_message_id, channel_name, message_ts, raw_text, content_hash,
                  symbols_json, content_type, created_at
                ) VALUES ('past1', 'signalyp', ?, 'BTC dump', 'hash1', '["BTC"]', 'COMMENTARY', datetime('now'))
                """,
                (event_ts - 600,),
            )
            agent.execute(
                """
                INSERT INTO futures_agent_trader_posts (
                  source_message_id, channel_name, message_ts, raw_text, content_hash,
                  symbols_json, content_type, created_at
                ) VALUES ('future1', 'signalyp', ?, 'future leak', 'hash2', '["BTC"]', 'COMMENTARY', datetime('now'))
                """,
                (event_ts + 600,),
            )

        with self._conn() as conn:
            n = link_replay_context(conn, run_tag="ctx_test")
            self.assertGreaterEqual(n, 0)
            leaks = conn.execute(
                """
                SELECT COUNT(*) FROM market_events_replay_context_links
                WHERE shock_id = ? AND context_ts >= ?
                """,
                (shock_id, event_ts),
            ).fetchone()[0]
            self.assertEqual(leaks, 0)

    def test_coverage_audit_distinguishes_span(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            report = historical_replay_coverage(conn)
            self.assertIn("HISTORICAL REPLAY COVERAGE AUDIT", report)
            self.assertIn("does not mutate market_events", report)

    def test_full_pipeline_writes_research_tables(self) -> None:
        base = int(time.time()) - 7200
        prices = []
        px = 50.0
        for i in range(200):
            if i == 100:
                px *= 1.04
            prices.append((base + i * 30, px))
        with self._conn() as conn:
            apply_migrations(conn)
            iid = self._seed_instrument(conn, symbol="SOL")
            self._seed_observations(conn, iid, prices)
            stats = run_full_replay_pipeline(conn, run_tag="e4_pipe", days=1)
            self.assertIn("shocks", stats)
            self.assertIn("ai_critic", stats)

    def test_telegram_multi_intent_uni_regression(self) -> None:
        intents = extract_deterministic_intents(UNI_FIXTURE)
        labels = {i.label for i in intents}
        self.assertIn("EXPLICIT_SIGNAL", labels)
        self.assertIn("LEVERAGE", labels)
        self.assertIn("POSITION_SIZING", labels)
        self.assertIn("PERFORMANCE_UPDATE", labels)
        sig = next(i for i in intents if i.label == "EXPLICIT_SIGNAL")
        self.assertIn("UNI", sig.payload.get("symbols", []))
        self.assertEqual(sig.payload.get("direction"), "LONG")
        self.assertAlmostEqual(float(sig.payload.get("entry", 0) or sig.payload["entry"][0]), 3.099, places=3)
        self.assertIn(3.179, sig.payload.get("targets", []))
        self.assertAlmostEqual(float(sig.payload.get("stop", 0)), 2.925, places=3)
        lev = next(i for i in intents if i.label == "LEVERAGE")
        self.assertEqual(lev.payload.get("leverage"), 25)
        pos = next(i for i in intents if i.label == "POSITION_SIZING")
        self.assertEqual(pos.payload.get("amount_usd"), 100.0)
        perf = next(i for i in intents if i.label == "PERFORMANCE_UPDATE")
        self.assertEqual(perf.payload.get("bank_usd"), 955.0)

    def test_multi_intent_stage7_schema(self) -> None:
        with self._agent_conn() as agent:
            applied = apply_agent_migrations(agent)
            self.assertTrue(
                any("multi_intent" in a for a in applied)
                or STAGE7_VERSION <= 7,
            )
            result = analyze_multi_intent(UNI_FIXTURE, source_type="test", source_record_id="1")
            n = persist_multi_intent(agent, result)
            self.assertGreater(n, 0)
            row = agent.execute(
                "SELECT label FROM futures_agent_post_multi_intent WHERE source_record_id = '1'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_postgres_candle_insert_compat(self) -> None:
        """insert_candle_ignore uses ON CONFLICT when PG wrapper detected."""
        from unittest.mock import MagicMock

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"id": 1}
        with patch(
            "bot.research.market_events.db.connection_is_postgres",
            return_value=True,
        ):
            ok = insert_candle_ignore(
                mock_conn, venue="v", symbol="BTC", timeframe="1m",
                open_ts=1, o=1, h=1, l=1, c=1, source="t",
            )
            self.assertTrue(ok)
            sql = mock_conn.execute.call_args[0][0]
            self.assertIn("ON CONFLICT", sql)


if __name__ == "__main__":
    unittest.main()
