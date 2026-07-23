"""S56.1 postmortem stabilization — backfill, lock retry, doctor/report."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.doctor import format_doctor
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence import trade_postmortem_s56 as s56
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestS561Stabilization(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s561.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_closed_without_snapshots(self, conn, *, n: int = 60) -> None:
        for i in range(n):
            pnl = 15.0 - i * 0.5
            direction = "LONG" if i % 2 == 0 else "SHORT"
            funding = -0.001 if (direction == "LONG" and pnl < 0) else 0.0004
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                  mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason, exit_price,
                  status, capital_usd, leverage, updated_at, decision_confidence
                ) VALUES ('bf', ?, ?, ?, 100, 95, 105, 110, ?, ?, 1000,
                  1, -1, ?, ?, ?, ?, 101, 'CLOSED', 100, 20, ?, ?)
                """,
                (
                    i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    self.now - 8000,
                    self.now - 100,
                    pnl,
                    pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    "STOP" if pnl < 0 else "TP1",
                    self.now,
                    0.6,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  hour, weekday, funding, news_score, ai_score,
                  gate_decision, gate_expected_pnl_pct, created_at, closed_at,
                  result, pnl_usd, pnl_pct, exit_reason
                ) VALUES (?, 'bf', ?, ?, ?, ?, 1, ?, ?, ?, 'ALLOWED', 0.1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    i % 24,
                    funding,
                    0.5,
                    0.55,
                    self.now, self.now,
                    "WIN" if pnl > 0 else "LOSS",
                    pnl, pnl,
                    "STOP" if pnl < 0 else "TP1",
                ),
            )
        conn.commit()

    def test_diagnose_explains_zero_snapshots(self) -> None:
        with market_events_connection() as live:
            self._seed_closed_without_snapshots(live, n=10)
            with research_connection() as research:
                diag = s56.diagnose_snapshots(research, live_conn=live)
        self.assertEqual(diag["snapshots"], 0)
        self.assertEqual(diag["closed_s42"], 10)
        self.assertEqual(diag["missing"], 10)
        self.assertTrue(any("backfill" in r.lower() or "NEW closes" in r for r in diag["reasons"]))

    def test_backfill_writes_and_runs_rca(self) -> None:
        with market_events_connection() as live:
            self._seed_closed_without_snapshots(live, n=60)
            with research_connection() as research:
                with patch.object(s56, "S56_MIN_EVIDENCE", 10):
                    out = s56.backfill_snapshots(
                        research, limit=60, run_rca=True, now=self.now, live_conn=live,
                    )
                research.commit()
                self.assertTrue(out["ok"])
                self.assertEqual(out["written"], 60)
                self.assertEqual(s56.snapshot_count(research), 60)
                self.assertGreaterEqual(len(s56.top_winners(research)), 1)
                self.assertGreaterEqual(len(s56.top_losers(research)), 1)
                self.assertIsNotNone(out.get("postmortem"))
                self.assertIsNotNone((out.get("postmortem") or {}).get("run_id"))

    def test_backfill_last_limit(self) -> None:
        with market_events_connection() as live:
            self._seed_closed_without_snapshots(live, n=40)
            with research_connection() as research:
                out = s56.backfill_snapshots(
                    research, limit=15, run_rca=False, now=self.now, live_conn=live,
                )
                research.commit()
                self.assertEqual(out["written"], 15)
                self.assertEqual(s56.snapshot_count(research), 15)

    def test_snapshot_writer_uses_retry_on_lock(self) -> None:
        with market_events_connection() as live:
            live.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                  mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason, exit_price,
                  status, capital_usd, leverage, updated_at
                ) VALUES ('g3', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, ?, 10,
                  0, 0, 1, 10, 'WIN', 'TP1', 105, 'CLOSED', 100, 20, ?)
                """,
                (self.now, self.now, self.now),
            )
            live.commit()
            row = live.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()

        with research_connection() as conn:
            calls = {"n": 0}
            real_execute = conn.execute

            def flaky(sql, params=()):
                calls["n"] += 1
                # Fail first two INSERT attempts with locked, then succeed
                if "INSERT OR REPLACE INTO market_events_trade_snapshots_s56" in str(sql) and calls["n"] <= 2:
                    raise sqlite3.OperationalError("database is locked")
                return real_execute(sql, params)

            with patch.object(conn, "execute", side_effect=flaky):
                ok = s56.record_close_snapshot(conn, trade_row=row, now=self.now, trigger_postmortem=False)
            self.assertTrue(ok)
            self.assertGreaterEqual(calls["n"], 3)

    def test_report_and_doctor_show_s56(self) -> None:
        with market_events_connection() as live:
            self._seed_closed_without_snapshots(live, n=40)
            with research_connection() as research:
                s56.backfill_snapshots(
                    research, limit=40, run_rca=False, now=self.now, live_conn=live,
                )
                research.commit()
                block = "\n".join(s56.format_s56_report_block(research))
                status = s56.doctor_s56_status(research, live_conn=live)
            text = s42.format_paper_performance_s42(live)
        self.assertIn("Top Winner", text)
        self.assertIn("Top Loser", text)
        self.assertIn("Most profitable symbol", text)
        self.assertIn("Worst symbol", text)
        self.assertIn("Most profitable hour", block)
        self.assertIn("Best AI score", block)
        self.assertGreaterEqual(status["snapshots"], 40)

        from bot.research.market_events.doctor import Check

        data = {
            "ok": True,
            "sha": "test",
            "db": [Check("SQLite", True)],
            "market": [Check("Yahoo", True, critical=False)],
            "news": [Check("News", True, critical=False)],
            "ai": [Check("AI", True, critical=False)],
            "telegram": Check("Connected", True),
            "paper": Check("Running", True, critical=False),
            "open_trades": 0,
            "signals_today": 0,
            "last_report": "—",
            "s56": status,
        }
        doc = format_doctor(data)
        self.assertIn("S56", doc)
        self.assertIn("Snapshots", doc)
        self.assertIn(str(status["snapshots"]), doc)
        self.assertIn("Suggestions", doc)
        self.assertIn("Last RCA", doc)


if __name__ == "__main__":
    unittest.main()
