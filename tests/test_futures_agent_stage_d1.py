"""Phase D.1 historical outcome engine tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bot.research.futures_agent.env_bootstrap import (
    configure_unit_test_db_isolation,
    reset_bootstrap_for_tests,
    resolve_agent_db_config,
)
from bot.research.futures_agent.db import connection_is_postgres, insert_returning_id, agent_connection
from bot.research.futures_agent.historical_candles import Candle, resolve_exchange_symbol
from bot.research.futures_agent.market_provider import BinanceMarketProvider
from bot.research.futures_agent.schema import STAGE5_VERSION, apply_migrations
from bot.research.futures_agent.signal_outcome_build import build_signal_outcomes
from bot.research.futures_agent.signal_outcome_constants import ENGINE_VERSION
from bot.research.futures_agent.signal_outcome_exit_policies import evaluate_exit_policy
from bot.research.futures_agent.signal_outcome_path import (
    PathEvaluation,
    TargetLevel,
    directional_return,
    evaluate_signal_path,
    find_market_entry,
    find_numeric_entry,
    pre_entry_touch_ignored,
)
from bot.research.futures_agent.signal_outcome_report import run_outcome_report
from bot.research.futures_agent.outcome_test_contamination_audit import (
    build_cleanup_plan,
    run_test_contamination_audit,
    run_test_contamination_cleanup,
)


def _c(ts: int, o: float, h: float, l: float, cl: float | None = None) -> Candle:
    return Candle(open_ts=ts, open=o, high=h, low=l, close=cl if cl is not None else o)


class MockCandleProvider:
    def __init__(self, candles: dict[str, list[Candle]]) -> None:
        self._candles = candles

    def fetch_range(
        self, exchange_symbol: str, start_ts: int, end_ts: int, *, interval: str = "1m",
    ) -> tuple[list[Candle], dict[str, Any]]:
        data = [c for c in self._candles.get(exchange_symbol, []) if start_ts <= c.open_ts <= end_ts]
        return data, {
            "fetch_status": "mock",
            "gap_count": 0,
            "candle_count": len(data),
            "data_source": "mock",
        }


class StageD1Tests(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "agent_d1_test.db")
        self.agent_url = f"sqlite:///{self.db_path}"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _conn(self):
        return agent_connection(self.agent_url)

    def _assert_isolated_sqlite(self, conn) -> None:
        self.assertFalse(connection_is_postgres(conn))
        cfg = resolve_agent_db_config()
        self.assertEqual(cfg.backend, "sqlite")
        self.assertEqual(cfg.sqlite_path, self.db_path)

    def test_unit_test_db_isolated_from_dotenv_postgres(self) -> None:
        """Regression: project .env PG URL must not redirect D.1 unit tests."""
        os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///trading_ai"
        reset_bootstrap_for_tests()
        configure_unit_test_db_isolation(self.db_path)
        with self._conn() as conn:
            self._assert_isolated_sqlite(conn)
        cfg = resolve_agent_db_config()
        self.assertFalse(cfg.is_postgres)
        self.assertEqual(cfg.url, self.agent_url)

    def _seed_explicit_signal(
        self,
        conn,
        *,
        post_id: str = "d1",
        raw: str,
        direction: str = "LONG",
        symbol: str = "BTC",
        message_ts: int = 1_700_000_000,
        levels: list[tuple[str, float, int]],
    ) -> int:
        post_row_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_trader_posts (
              source_message_id, channel_name, message_ts, raw_text,
              content_hash, content_type, symbols_json, deterministic_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (post_id, "signalyp", message_ts, raw, f"h{post_id}", "EXPLICIT_SIGNAL", json.dumps([symbol]), 0.9),
        )
        self.assertIsNotNone(post_row_id)
        thesis_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_trader_theses (
              post_id, symbol, direction, thesis_text, horizon, condition_text, invalidation_text, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (post_row_id, symbol, direction, "test", "1d", None, None, 0.9),
        )
        self.assertIsNotNone(thesis_id)
        for ltype, price, ord_ in levels:
            conn.execute(
                """
                INSERT INTO futures_agent_trader_levels (
                  thesis_id, level_type, price, ordinal, confidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (thesis_id, ltype, price, ord_, 0.9),
            )
        conn.commit()
        post_check = conn.execute(
            "SELECT id FROM futures_agent_trader_posts WHERE id = ?",
            (post_row_id,),
        ).fetchone()
        thesis_check = conn.execute(
            "SELECT post_id FROM futures_agent_trader_theses WHERE id = ?",
            (thesis_id,),
        ).fetchone()
        self.assertIsNotNone(post_check)
        self.assertEqual(int(thesis_check["post_id"]), int(post_row_id))
        return int(thesis_id)

    def test_binance_canonical_source_locked_per_symbol(self) -> None:
        from unittest.mock import MagicMock
        from bot.research.futures_agent.historical_candles import BinanceCandleProvider

        provider = BinanceMarketProvider()
        inner = BinanceCandleProvider(provider)
        spot_rows = [[1_700_000_000_000, "1", "2", "0.5", "1.5", "0"]]
        provider.fetch_spot_klines = MagicMock(return_value=[])  # type: ignore[method-assign]
        provider.fetch_futures_klines = MagicMock(return_value=spot_rows)  # type: ignore[method-assign]

        candles, meta = inner.fetch_range("FOOUSDT", 1_700_000_000, 1_700_000_060)
        self.assertEqual(meta["data_source"], "binance_futures")
        self.assertEqual(inner.canonical_source_for("FOOUSDT"), "binance_futures")

        provider.fetch_spot_klines = MagicMock(return_value=spot_rows)  # type: ignore[method-assign]
        inner.fetch_range("FOOUSDT", 1_700_000_000, 1_700_000_120)
        provider.fetch_spot_klines.assert_not_called()

    def test_schema_stage5_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            v = conn.execute(
                "SELECT version FROM futures_agent_migrations WHERE version = ?",
                (STAGE5_VERSION,),
            ).fetchone()
        self.assertTrue(any("stage5" in a for a in applied) or v is not None)

    def test_long_entry_tp_before_stop(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 101, 99, 100),
            _c(t0 + 60, 100, 110, 99, 108),
            _c(t0 + 120, 108, 109, 95, 96),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "STOP": [95.0], "TARGET": [110.0]},
            raw_text="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
            candles=candles,
        )
        self.assertEqual(ev.entry_status, "ENTERED")
        self.assertEqual(ev.conservative_terminal, "TP1")
        self.assertEqual(ev.max_target_reached, 1)

    def test_long_entry_stop_before_tp(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 101, 99.5, 100),
            _c(t0 + 60, 100, 101, 94, 95),
            _c(t0 + 120, 95, 120, 94, 115),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "STOP": [95.0], "TARGET": [110.0]},
            raw_text="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
            candles=candles,
        )
        self.assertEqual(ev.conservative_terminal, "STOP")

    def test_short_entry_tp(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 100.5, 99.5, 100),
            _c(t0 + 60, 100, 101, 88, 90),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="SHORT",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "STOP": [105.0], "TARGET": [90.0]},
            raw_text="BTC SHORT\nEntry: 100\nSL: 105\nTP: 90",
            candles=candles,
        )
        self.assertEqual(ev.conservative_terminal, "TP1")
        self.assertGreater(directional_return("SHORT", 100.0, 90.0), 0)

    def test_numeric_entry_not_entered(self) -> None:
        t0 = 1_700_000_000
        candles = [_c(t0 + i * 60, 120, 121, 119, 120) for i in range(10)]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [101.0], "STOP": [95.0], "TARGET": [110.0]},
            raw_text="BTC LONG\nEntry: 100-101\nSL: 95\nTP: 110",
            candles=candles,
        )
        self.assertEqual(ev.entry_status, "NOT_ENTERED")

    def test_pre_entry_touch_ignored(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 100.5, 89, 95),
            _c(t0 + 60, 95, 101, 94, 100),
        ]
        self.assertTrue(pre_entry_touch_ignored(
            candles, t0, t0 + 60, "LONG", 95.0, 110.0,
        ))

    def test_market_entry_first_candle_after_decision(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 99, 100, 98, 99),
            _c(t0 + 60, 100, 101, 99, 100.5),
        ]
        ts, px = find_market_entry(candles, t0)
        self.assertEqual(ts, t0 + 60)
        self.assertEqual(px, 100.0)

    def test_ambiguous_intrabar_same_candle(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 101, 99.5, 100),
            _c(t0 + 60, 100, 112, 93, 105),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "STOP": [95.0], "TARGET": [110.0]},
            raw_text="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
            candles=candles,
        )
        self.assertEqual(ev.ambiguous_intrabar, 1)
        self.assertEqual(ev.conservative_terminal, "STOP")

    def test_deferred_stop_no_invented_stop(self) -> None:
        t0 = 1_700_000_000
        candles = [_c(t0 + i * 60, 100, 101, 99, 100) for i in range(5)]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "TARGET": [110.0]},
            raw_text="BTC LONG\nEntry: 100\nСтоп: пока не ставлю\nTP: 110",
            candles=candles,
        )
        self.assertEqual(ev.stop_mode, "DEFERRED_STOP")
        self.assertIsNone(ev.stop_price)

    def test_unknown_symbol_unresolved(self) -> None:
        sym, _, status = resolve_exchange_symbol("NOT A SYMBOL!!")
        self.assertIsNone(sym)
        self.assertEqual(status, "unresolved_symbol")

    def test_target_direction_validation(self) -> None:
        t0 = 1_700_000_000
        candles = [_c(t0, 100, 101, 99, 100), _c(t0 + 60, 100, 105, 99, 104)]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0], "TARGET": [95.0]},
            raw_text="BTC LONG\nEntry: 100\nTP: 95",
            candles=candles,
        )
        self.assertEqual(ev.targets[0].validity, "DIRECTION_INCONSISTENT")

    def test_markout_and_mfe_mae(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 101, 99.5, 100),
            _c(t0 + 60, 100, 105, 98, 104),
            _c(t0 + 3600, 104, 106, 103, 105),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0]},
            raw_text="BTC LONG\nEntry: 100",
            candles=candles,
        )
        self.assertIsNotNone(ev.mfe_pct)
        self.assertIsNotNone(ev.mae_pct)
        self.assertTrue(any(m.horizon == "1h" for m in ev.markouts))

    def test_partial_target_policy_p2(self) -> None:
        ev = PathEvaluation(
            decision_ts=1, direction="LONG", entry_mode="NUMERIC_ZONE",
            entry_status="ENTERED", entry_ts=1, entry_price=100.0,
            entry_fill_model="conservative_boundary", stop_mode="NUMERIC_STOP",
            stop_price=95.0, targets=[
                TargetLevel(110.0, 1, "VALID"),
                TargetLevel(120.0, 2, "VALID"),
            ],
            conservative_terminal="TP2", max_target_reached=2,
            markouts=[type("M", (), {"horizon": "24h", "mark_price": 120.0})()],
        )
        pr = evaluate_exit_policy(ev, "P2")
        self.assertIsNotNone(pr.return_pct)
        self.assertGreater(pr.return_pct, 0)

    def test_seed_helper_preserves_post_thesis_fk(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            thesis_id = self._seed_explicit_signal(
                conn,
                post_id="fk1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            row = conn.execute(
                """
                SELECT t.id AS thesis_id, t.post_id, p.id AS post_pk, p.source_message_id
                FROM futures_agent_trader_theses t
                JOIN futures_agent_trader_posts p ON p.id = t.post_id
                WHERE t.id = ?
                """,
                (thesis_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["post_id"], row["post_pk"])
        self.assertEqual(row["source_message_id"], "fk1")

    def test_build_idempotent_no_duplicates(self) -> None:
        t0 = 1_700_000_000
        candles = {
            "BTCUSDT": [
                _c(t0, 100, 101, 99.5, 100),
                _c(t0 + 60, 100, 110, 99, 108),
            ],
        }
        provider = MockCandleProvider(candles)
        with self._conn() as conn:
            self._assert_isolated_sqlite(conn)
            apply_migrations(conn)
            thesis_id = self._seed_explicit_signal(
                conn,
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            s1 = build_signal_outcomes(conn, channel="signalyp", candle_provider=provider)
            s2 = build_signal_outcomes(conn, channel="signalyp", candle_provider=provider)
            n = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM futures_agent_research_signal_outcomes
                WHERE thesis_id = ? AND engine_version = ?
                """,
                (thesis_id, ENGINE_VERSION),
            ).fetchone()["n"]
            dup = conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                  SELECT thesis_id, engine_version, COUNT(*) AS c
                  FROM futures_agent_research_signal_outcomes
                  WHERE thesis_id = ?
                  GROUP BY thesis_id, engine_version
                  HAVING COUNT(*) > 1
                ) x
                """,
                (thesis_id,),
            ).fetchone()["n"]
            ev_n = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM futures_agent_research_signal_events e
                JOIN futures_agent_research_signal_outcomes o ON o.id = e.outcome_id
                WHERE o.thesis_id = ?
                """,
                (thesis_id,),
            ).fetchone()["n"]
        self.assertEqual(s1.outcomes_inserted, 1)
        self.assertEqual(s2.outcomes_inserted, 0)
        self.assertEqual(n, 1)
        self.assertEqual(dup, 0)
        self.assertGreater(ev_n, 0)

    def test_no_future_candle_leakage_in_markouts(self) -> None:
        t0 = 1_700_000_000
        candles = [
            _c(t0, 100, 101, 99.5, 100),
            _c(t0 + 60, 100, 150, 99, 140),
        ]
        ev = evaluate_signal_path(
            decision_ts=t0,
            direction="LONG",
            levels={"ENTRY_LOW": [100.0], "ENTRY_HIGH": [100.0]},
            raw_text="BTC LONG\nEntry: 100",
            candles=candles,
        )
        m15 = next(m for m in ev.markouts if m.horizon == "15m")
        self.assertLessEqual(m15.mark_ts, t0 + 60 + 15 * 60)

    def test_test_contamination_audit_detects_fixture_ids(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_explicit_signal(
                conn,
                post_id="d1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            report = run_test_contamination_audit(conn, channel="signalyp")
        self.assertGreaterEqual(len(report.posts), 1)
        self.assertIn("d1", report.posts[0].detail)

    def _seed_three_fixture_posts(self, conn) -> list[int]:
        thesis_ids = []
        for post_id in ("d1", "fk1", "wf1"):
            thesis_ids.append(self._seed_explicit_signal(
                conn,
                post_id=post_id,
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            ))
        return thesis_ids

    def _seed_outcome_chain(self, conn, thesis_id: int, post_id: int) -> int:
        from bot.research.futures_agent.signal_outcome_build import _persist_outcome
        from bot.research.futures_agent.signal_outcome_path import (
            MarkoutPoint,
            PathEvaluation,
            SignalEvent,
        )

        ev = PathEvaluation(
            decision_ts=1_700_000_000,
            direction="LONG",
            entry_mode="NUMERIC_ZONE",
            entry_status="ENTERED",
            entry_ts=1_700_000_060,
            entry_price=100.0,
            entry_fill_model="conservative_boundary",
            stop_mode="NUMERIC_STOP",
            stop_price=95.0,
            targets=[],
            outcome_status="ENTERED",
            data_quality_status="COMPLETE",
            conservative_terminal="TP1",
        )
        ev.events = [
            SignalEvent(
                event_type="ENTRY",
                event_ts=1_700_000_060,
                event_price=100.0,
                candle_open_ts=1_700_000_000,
            ),
            SignalEvent(
                event_type="TP1",
                event_ts=1_700_000_120,
                event_price=110.0,
                candle_open_ts=1_700_000_060,
                target_index=0,
            ),
        ]
        ev.markouts = [
            MarkoutPoint(
                horizon=h,
                horizon_seconds=sec,
                mark_ts=1_700_000_000 + sec,
                mark_price=100.0 + i,
                directional_return_pct=float(i),
                mfe_pct=float(i),
                mae_pct=-0.5,
            )
            for i, (h, sec) in enumerate((
                ("15m", 900),
                ("1h", 3600),
                ("4h", 14400),
                ("1d", 86400),
                ("3d", 259200),
                ("7d", 604800),
            ), start=1)
        ]
        row = conn.execute(
            """
            SELECT t.id AS thesis_id, t.post_id, p.channel_name, p.message_ts,
                   t.symbol, t.direction, p.raw_text
            FROM futures_agent_trader_theses t
            JOIN futures_agent_trader_posts p ON p.id = t.post_id
            WHERE t.id = ?
            """,
            (thesis_id,),
        ).fetchone()
        oid, _, _ = _persist_outcome(
            conn,
            row=row,
            exchange_symbol="BTCUSDT",
            symbol_status="resolved",
            candle_meta={"fetch_status": "mock"},
            ev=ev,
            engine_version=ENGINE_VERSION,
        )
        conn.commit()
        return oid

    def test_cleanup_dry_run_does_not_delete(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_explicit_signal(
                conn,
                post_id="d1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_trader_posts",
            ).fetchone()["n"]
            result = run_test_contamination_cleanup(conn, channel="signalyp", apply=False)
            after = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_trader_posts",
            ).fetchone()["n"]
        self.assertFalse(result.applied)
        self.assertEqual(before, after)
        self.assertGreaterEqual(result.plan.counts["posts"], 1)

    def test_cleanup_apply_removes_fixture_chain(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            thesis_id = self._seed_explicit_signal(
                conn,
                post_id="d1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            self._seed_outcome_chain(conn, thesis_id, 1)
            result = run_test_contamination_cleanup(conn, channel="signalyp", apply=True)
            audit = run_test_contamination_audit(conn, channel="signalyp")
        self.assertTrue(result.applied)
        self.assertEqual(result.deleted["posts"], 1)
        self.assertEqual(audit.total_suspicious, 0)

    def test_cleanup_known_three_fixture_counts(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            thesis_ids = self._seed_three_fixture_posts(conn)
            for tid in thesis_ids:
                self._seed_outcome_chain(conn, tid, 1)
            plan = build_cleanup_plan(conn, channel="signalyp")
            self.assertEqual(plan.counts["posts"], 3)
            self.assertEqual(plan.counts["theses"], 3)
            self.assertEqual(plan.counts["levels"], 12)
            self.assertEqual(plan.counts["outcomes"], 3)
            self.assertEqual(plan.counts["events"], 6)
            self.assertEqual(plan.counts["markouts"], 18)
            result = run_test_contamination_cleanup(conn, channel="signalyp", apply=True)
        self.assertTrue(result.applied)
        self.assertTrue(result.known_fixture_assertion_ok)
        self.assertEqual(result.deleted, {
            "markouts": 18,
            "events": 6,
            "outcomes": 3,
            "levels": 12,
            "theses": 3,
            "posts": 3,
        })

    def test_cleanup_fk_safe_delete_order(self) -> None:
        from bot.research.futures_agent import outcome_test_contamination_audit as mod

        order: list[str] = []
        original = mod._delete_by_ids

        def recording_delete(conn, table, ids):
            order.append(table)
            return original(conn, table, ids)

        with self._conn() as conn:
            apply_migrations(conn)
            thesis_id = self._seed_explicit_signal(
                conn,
                post_id="d1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            self._seed_outcome_chain(conn, thesis_id, 1)
            mod._delete_by_ids = recording_delete  # type: ignore[assignment]
            try:
                run_test_contamination_cleanup(conn, channel="signalyp", apply=True)
            finally:
                mod._delete_by_ids = original  # type: ignore[assignment]

        self.assertEqual(order, [
            "futures_agent_research_signal_markouts",
            "futures_agent_research_signal_events",
            "futures_agent_research_signal_outcomes",
            "futures_agent_trader_levels",
            "futures_agent_trader_theses",
            "futures_agent_trader_posts",
        ])

    def test_cleanup_rollback_on_delete_failure(self) -> None:
        from bot.research.futures_agent import outcome_test_contamination_audit as mod

        original = mod._delete_by_ids
        calls = {"n": 0}

        def failing_delete(conn, table, ids):
            calls["n"] += 1
            if table == "futures_agent_research_signal_outcomes":
                raise RuntimeError("simulated delete failure")
            return original(conn, table, ids)

        with self._conn() as conn:
            apply_migrations(conn)
            thesis_id = self._seed_explicit_signal(
                conn,
                post_id="d1",
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            self._seed_outcome_chain(conn, thesis_id, 1)
            mod._delete_by_ids = failing_delete  # type: ignore[assignment]
            try:
                result = run_test_contamination_cleanup(conn, channel="signalyp", apply=True)
            finally:
                mod._delete_by_ids = original  # type: ignore[assignment]
            posts = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_trader_posts",
            ).fetchone()["n"]
            outcomes = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_research_signal_outcomes",
            ).fetchone()["n"]
        self.assertFalse(result.applied)
        self.assertTrue(any("simulated" in e for e in result.errors))
        self.assertGreaterEqual(posts, 1)
        self.assertGreaterEqual(outcomes, 1)

    def test_walk_forward_report_structure(self) -> None:
        t0 = 1_700_000_000
        provider = MockCandleProvider({
            "BTCUSDT": [_c(t0, 100, 101, 99.5, 100), _c(t0 + 60, 100, 110, 99, 108)],
        })
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_explicit_signal(
                conn,
                post_id="wf1",
                message_ts=t0,
                raw="BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                levels=[
                    ("ENTRY_LOW", 100.0, 0),
                    ("ENTRY_HIGH", 100.0, 0),
                    ("STOP", 95.0, 0),
                    ("TARGET", 110.0, 1),
                ],
            )
            build_signal_outcomes(conn, channel="signalyp", candle_provider=provider)
            report = run_outcome_report(conn, channel="signalyp")
        self.assertIn(report.verdict, (
            "SOURCE_HAS_PERSISTENT_EDGE",
            "CONDITIONAL_EDGE",
            "NOT_STABLE",
            "INSUFFICIENT_DATA",
        ))
        self.assertGreaterEqual(report.corpus["outcomes_built"], 1)


if __name__ == "__main__":
    unittest.main()
