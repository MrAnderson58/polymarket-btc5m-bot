"""S56.2 deep statistical analysis."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import trade_postmortem_s56 as s56


_EXITS = ("STOP", "TP1", "TP2", "TRAILING", "TIMEOUT", "PORTFOLIO_REPLACE")
_SYMBOLS = ("BTC", "ETH", "ARB", "SOL", "PEPE")


class TestS562DeepStats(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s562.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 120) -> None:
        for i in range(n):
            pnl = 25.0 - i * 0.4
            symbol = _SYMBOLS[i % len(_SYMBOLS)]
            direction = "LONG" if i % 2 == 0 else "SHORT"
            exit_reason = _EXITS[i % len(_EXITS)]
            funding = -0.001 if (direction == "LONG" and pnl < 0) else 0.0004
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, closed_at, holding_seconds,
                  mfe_pct, mae_pct, pnl_pct, pnl_usd, result, exit_reason, exit_price,
                  status, capital_usd, leverage, updated_at, decision_confidence
                ) VALUES ('s562', ?, ?, ?, 100, 95, 105, 110, ?, ?, ?,
                  1, -1, ?, ?, ?, ?, 101, 'CLOSED', 100, 20, ?, ?)
                """,
                (
                    i + 1,
                    symbol,
                    direction,
                    self.now - 9000,
                    self.now - 100,
                    500 + (i % 50) * 60,
                    pnl,
                    pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    exit_reason,
                    self.now,
                    0.55,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  hour, weekday, funding, news_score, macro_score, ai_score,
                  gate_decision, gate_expected_pnl_pct, created_at, closed_at,
                  result, pnl_usd, pnl_pct, exit_reason, market_regime
                ) VALUES (?, 's562', ?, ?, ?, ?, 2, ?, ?, ?, ?, 'ALLOWED', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1,
                    i + 1,
                    symbol,
                    direction,
                    i % 24,
                    funding,
                    0.4 if pnl < 0 else 0.8,
                    0.3 if pnl < 0 else 0.7,
                    0.2 if pnl < 0 else 0.75,
                    pnl / 100.0,
                    self.now,
                    self.now,
                    "WIN" if pnl > 0 else "LOSS",
                    pnl,
                    pnl,
                    exit_reason,
                    "RISK_ON" if pnl > 0 else "RISK_OFF",
                ),
            )
        conn.commit()

    def test_deep_stats_tables(self) -> None:
        with market_events_connection() as conn:
            self._seed(conn, n=120)
            for r in conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchall():
                s56.record_close_snapshot(conn, trade_row=r, now=self.now)
            conn.commit()

            deep = s56.compute_deep_stats(conn)
            self.assertGreaterEqual(deep["overall"]["n"], 120)
            self.assertEqual(len(deep["hours"]), 24)
            self.assertTrue(any(h["n"] > 0 for h in deep["hours"]))
            self.assertGreaterEqual(len(deep["symbols_top"]), 5)
            for row in deep["symbols_top"]:
                self.assertIn("winrate", row)
                self.assertIn("expectancy", row)
                self.assertIn("pf", row)
                self.assertIn("avg_win", row)
                self.assertIn("avg_loss", row)

            dirs = {(r["symbol"], r["direction"]) for r in deep["symbol_direction"]}
            self.assertIn(("BTC", "LONG"), dirs)
            self.assertIn(("BTC", "SHORT"), dirs)
            self.assertIn(("ETH", "LONG"), dirs)

            exit_labels = {e["exit_reason"] for e in deep["exits"] if e["n"] > 0}
            for need in ("STOP", "TP1", "TP2", "Trailing", "Timeout", "Portfolio Replace"):
                self.assertIn(need, exit_labels)

            text = s56.format_postmortem_report(conn)
            self.assertIn("S56.2 Deep Stats", text)
            self.assertIn("Symbols TOP-", text)
            self.assertIn("Hours (0–23 UTC)", text)
            self.assertIn("Symbol × Direction", text)
            self.assertIn("Exit reasons", text)
            self.assertIn("Suggestions enabled:   False", text)

            block = "\n".join(s56.format_s56_report_block(conn))
            self.assertIn("Overall:", block)
            self.assertIn("Exits:", block)

    def test_feature_importance_includes_categoricals(self) -> None:
        with market_events_connection() as conn:
            self._seed(conn, n=100)
            for r in conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchall():
                s56.record_close_snapshot(conn, trade_row=r, now=self.now)
            conn.commit()
            winners = s56.top_winners(conn, n=40)
            losers = s56.top_losers(conn, n=40)
            imp = s56.compute_feature_importance(winners, losers)
            feats = {i["feature"] for i in imp}
            self.assertTrue({"symbol", "direction", "exit_reason"} & feats)
            self.assertTrue({"hour", "funding", "ai_score", "news_score"} & feats)

    def test_suggestions_disabled_by_default(self) -> None:
        with market_events_connection() as conn:
            self._seed(conn, n=80)
            for r in conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchall():
                s56.record_close_snapshot(conn, trade_row=r, now=self.now)
            with patch.object(s56, "S56_MIN_EVIDENCE", 10), patch.object(s56, "S56_LLM_ENABLED", False):
                out = s56.run_postmortem(conn, force=True, now=self.now)
            conn.commit()
            self.assertFalse(out.get("suggestions_enabled"))
            self.assertEqual(out.get("suggestions_created"), 0)
            self.assertEqual(len(s56.list_suggestions(conn, status=s56.STATUS_WAITING)), 0)
            self.assertIn("deep_stats", out)
            self.assertIn("coverage", out["deep_stats"])


if __name__ == "__main__":
    unittest.main()
