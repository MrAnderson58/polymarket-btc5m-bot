"""S58 Decision Trace tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import decision_trace_s58 as s58
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestDecisionTraceS58(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s58.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_v68(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 68)
        with research_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_trade_decisions_s58'",
            ).fetchone()
            self.assertIsNotNone(row)

    def _insert_open_trade(self, conn, *, trade_id: int = 1) -> None:
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              id, s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, capital_usd, leverage, updated_at
            ) VALUES (?, 's58', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN',
              0, 0, 100, 20, ?)
            """,
            (trade_id, trade_id, self.now - 100, self.now),
        )

    def test_decision_saved_and_restored(self) -> None:
        with market_events_connection() as live:
            self._insert_open_trade(live, trade_id=7)
            live.commit()
        features = {
            "symbol": "BTC",
            "direction": "LONG",
            "funding": -0.0004,
            "ai_score": 0.64,
            "market_regime": "WEAK_BULL",
            "regime_btc_return_pct": 0.55,
            "atr": 1.2,
            "fear_greed": 58,
            "news_score": 0.4,
            "macro_score": 0.3,
        }
        estimate = {"expected_pnl_pct": 3.8, "similar_count": 12}
        with research_connection() as conn:
            with patch.object(s58, "_candles_emas", return_value={"ema20": 101.0, "ema50": 100.0, "ema200": 98.0}):
                ok = s58.record_decision_on_open(
                    conn,
                    paper_trade_id=7,
                    s40_signal_type="s58",
                    s40_signal_id=7,
                    features=features,
                    estimate=estimate,
                    gate_decision="ALLOWED",
                    entry_price=100.0,
                    opened_at=self.now - 100,
                    open_count=3,
                    now=self.now,
                )
            conn.commit()
            self.assertTrue(ok)
            d = s58.get_decision(conn, 7)
            self.assertIsNotNone(d)
            assert d is not None
            self.assertEqual(d["symbol"], "BTC")
            self.assertEqual(d["direction"], "LONG")
            self.assertEqual(d["market_regime"], "WEAK_BULL")
            self.assertAlmostEqual(float(d["expected_pnl_pct"]), 3.8)
            why = d.get("why_opened") or []
            tags = {r["tag"] for r in why}
            self.assertIn("EMA", tags)
            self.assertIn("Funding", tags)
            self.assertIn("Regime", tags)
            self.assertIn("Gate", tags)

            text = s58.format_explain_trade(conn, 7)
            self.assertIn("========== TRADE =========", text)
            self.assertIn("LONG BTC", text)
            self.assertIn("Why opened:", text)
            self.assertIn("✓ EMA", text)
            self.assertIn("✓ Funding", text)

    def test_decision_finalized_on_close(self) -> None:
        with market_events_connection() as live:
            self._insert_open_trade(live, trade_id=9)
            live.commit()
        with research_connection() as rconn:
            s58.record_decision_on_open(
                rconn,
                paper_trade_id=9,
                s40_signal_type="s58",
                s40_signal_id=9,
                features={"symbol": "BTC", "direction": "LONG", "funding": -0.001, "ai_score": 0.7},
                estimate={"expected_pnl_pct": 2.0},
                gate_decision="ALLOWED",
                entry_price=100.0,
                now=self.now,
            )
            rconn.commit()
        with market_events_connection() as live:
            row = live.execute("SELECT * FROM market_events_paper_trades_s42 WHERE id=9").fetchone()
            s42._close_trade(live, row=row, exit_price=98.0, exit_reason="STOP", now=self.now + 500)
            live.commit()
        with research_connection() as rconn:
            d = s58.get_decision(rconn, 9)
            assert d is not None
            self.assertEqual(d["exit_reason"], "STOP")
            self.assertIsNotNone(d["final_pnl_usd"])
            self.assertLess(float(d["final_pnl_usd"]), 0)
            self.assertEqual(int(d["duration_sec"]), 600)
            text = s58.format_explain_trade(rconn, 9)
            self.assertIn("STOP", text)
            self.assertIn("Difference:", text)

    def test_decision_report(self) -> None:
        with research_connection() as conn:
            for i in range(1, 21):
                pnl = 15.0 - i  # winners and losers
                conn.execute(
                    """
                    INSERT INTO market_events_trade_decisions_s58 (
                      paper_trade_id, opened_at, symbol, direction, entry_price,
                      gate_result, gate_reason, why_opened_json,
                      final_pnl_usd, exit_reason, closed_at, created_at, updated_at
                    ) VALUES (?, ?, 'BTC', 'LONG', 100, 'PASS', 'ALLOWED', ?, ?, 'TP1', ?, ?, ?)
                    """,
                    (
                        i,
                        self.now,
                        '[{"tag":"EMA","ok":true},{"tag":"Funding","ok":true},{"tag":"Gate","ok":true}]'
                        if pnl > 0 else
                        '[{"tag":"EMA","ok":true},{"tag":"AI","ok":false},{"tag":"Gate","ok":true}]',
                        pnl,
                        self.now,
                        self.now,
                        self.now,
                    ),
                )
            conn.commit()
            rep = s58.compute_decision_report(conn)
            self.assertGreater(rep["n_winners"], 0)
            self.assertGreater(rep["n_losers"], 0)
            self.assertTrue(rep["top_reasons_winners"])
            text = s58.format_decision_report(conn)
            self.assertIn("S58 Decision Report", text)
            self.assertIn("Most profitable trades", text)
            self.assertIn("Most losing trades", text)


if __name__ == "__main__":
    unittest.main()
