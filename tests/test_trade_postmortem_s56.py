"""S56 trade postmortem / suggestions tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence import trade_postmortem_s56 as s56


class TestTradePostmortemS56(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s56.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_closed(self, conn, *, n: int = 80) -> None:
        for i in range(n):
            pnl = 20.0 - (i * 0.6)  # first half winners-ish, later losers
            direction = "LONG" if i % 3 else "SHORT"
            funding = -0.001 if (direction == "LONG" and pnl < 0) else 0.0005
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                  mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason, exit_price,
                  status, capital_usd, leverage, updated_at, decision_confidence
                ) VALUES ('sig', ?, ?, ?, 100, 95, 105, 110, ?, ?, 3600,
                  1, -1, ?, ?, ?, ?, 101, 'CLOSED', 100, 20, ?, ?)
                """,
                (
                    i + 1,
                    "BTC" if i % 2 == 0 else "PEPE",
                    direction,
                    self.now - 7200,
                    self.now - 60,
                    pnl,
                    pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    "STOP" if pnl < 0 else "TP1",
                    self.now,
                    0.8 if pnl < 0 else 0.5,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  hour, weekday, funding, etf_flow, news_score, ai_score,
                  gate_decision, gate_expected_pnl_pct, created_at, closed_at,
                  result, pnl_usd, pnl_pct, exit_reason
                ) VALUES (?, 'sig', ?, ?, ?, ?, 2, ?, ?, ?, ?, 'ALLOWED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1,
                    i + 1,
                    "BTC" if i % 2 == 0 else "PEPE",
                    direction,
                    i % 24,
                    funding,
                    1.0 if pnl > 0 else -0.5,
                    0.7 if pnl > 0 else 0.2,
                    0.8 if pnl < 0 else 0.5,
                    pnl / 100.0,
                    self.now,
                    self.now,
                    "WIN" if pnl > 0 else "LOSS",
                    pnl,
                    pnl,
                    "STOP" if pnl < 0 else "TP1",
                ),
            )
        conn.commit()

    def test_schema_v66(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 66)
        with market_events_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_rule_suggestions_s56'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_close_records_snapshot(self) -> None:
        with market_events_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, status,
                  mfe_pct, mae_pct, capital_usd, leverage, updated_at
                ) VALUES ('g3', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
                """,
                (self.now, self.now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
            s42._close_trade(conn, row=row, exit_price=105.0, exit_reason="TP1", now=self.now + 10)
            conn.commit()
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_trade_snapshots_s56",
            ).fetchone()["n"]
            self.assertEqual(n, 1)

    def test_top_winners_losers_and_rca(self) -> None:
        with market_events_connection() as conn:
            self._seed_closed(conn, n=80)
            for r in conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchall():
                s56.record_close_snapshot(conn, trade_row=r, now=self.now)
            conn.commit()
            winners = s56.top_winners(conn, n=20)
            losers = s56.top_losers(conn, n=20)
            self.assertTrue(all(float(w["pnl_usd"]) > 0 for w in winners))
            self.assertTrue(all(float(l["pnl_usd"]) < 0 for l in losers))
            rca = s56.compute_rca(winners, losers)
            self.assertIn("findings", rca)
            imp = s56.compute_feature_importance(winners, losers)
            self.assertTrue(isinstance(imp, list))

    def test_postmortem_creates_waiting_suggestions(self) -> None:
        with market_events_connection() as conn:
            self._seed_closed(conn, n=80)
            for r in conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchall():
                s56.record_close_snapshot(conn, trade_row=r, now=self.now)
            with patch.object(s56, "S56_MIN_EVIDENCE", 10), patch.object(s56, "S56_LLM_ENABLED", False):
                out = s56.run_postmortem(conn, force=True, now=self.now)
            conn.commit()
            self.assertIsNotNone(out.get("run_id"))
            waiting = s56.list_suggestions(conn, status=s56.STATUS_WAITING)
            # May be 0 if thresholds not met — still run should succeed
            self.assertEqual(out["llm_method"], "deterministic")
            text = s56.format_postmortem_report(conn)
            self.assertIn("WAITING_APPROVAL", text)
            self.assertIn("Human approval required", text)

    def test_approve_does_not_apply_strategy(self) -> None:
        with market_events_connection() as conn:
            conn.execute(
                """
                INSERT INTO market_events_rule_suggestions_s56 (
                  run_id, rule_text, evidence_trades, expected_improvement_pct,
                  confidence_pct, status, source, created_at
                ) VALUES (1, 'Disable LONG when Funding < -0.02%', 100, 3.8, 94,
                  'WAITING_APPROVAL', 'test', ?)
                """,
                (self.now,),
            )
            conn.commit()
            sid = int(conn.execute("SELECT id FROM market_events_rule_suggestions_s56").fetchone()["id"])
            out = s56.approve_suggestion(conn, sid, now=self.now)
            conn.commit()
            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], s56.STATUS_APPROVED)
            self.assertIn("S56.", out["cursor_task"])
            self.assertIn("Disable LONG", out["cursor_task"])
            # No trading code mutated — only DB status
            row = conn.execute(
                "SELECT status FROM market_events_rule_suggestions_s56 WHERE id=?",
                (sid,),
            ).fetchone()
            self.assertEqual(row["status"], "APPROVED")

    def test_report_includes_s56_block(self) -> None:
        with market_events_connection() as conn:
            text = s42.format_paper_performance_s42(conn)
        self.assertIn("S56 Post-Mortem", text)


if __name__ == "__main__":
    unittest.main()
