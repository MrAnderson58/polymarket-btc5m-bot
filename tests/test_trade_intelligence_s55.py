"""S55.1 — Trade intelligence gate tests."""

from __future__ import annotations

import sqlite3
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


def _seed_closed_feature(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    symbol: str = "BTC",
    direction: str = "LONG",
    funding: float = 0.01,
    pnl_pct: float = 5.0,
    result: str = "WIN",
    reached_tp1: int = 1,
    stopped: int = 0,
    trailing: int = 0,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_trade_features_s55 (
          s40_signal_type, s40_signal_id, symbol, direction,
          hour, weekday, funding, atr, fear_greed, ai_score,
          result, pnl_pct, pnl_usd, mae_pct, mfe_pct,
          reached_tp1, reached_tp2, stopped, trailing,
          duration_sec, exit_reason, gate_decision, created_at, closed_at
        ) VALUES (
          'g3', ?, ?, ?,
          12, 2, ?, 1.5, 50, 7,
          ?, ?, 10, -1, 6,
          ?, 0, ?, ?,
          3600, 'TP1', 'open', ?, ?
        )
        """,
        (signal_id, symbol, direction, funding, result, pnl_pct, reached_tp1, stopped, trailing, now - 100, now - 50),
    )


class TestOutcomeFlagsS55(unittest.TestCase):
    def test_flags_from_exit_reasons(self) -> None:
        self.assertEqual(s55.outcome_flags_from_exit("TP1")["reached_tp1"], 1)
        self.assertEqual(s55.outcome_flags_from_exit("TP2")["reached_tp2"], 1)
        self.assertEqual(s55.outcome_flags_from_exit("STOP")["stopped"], 1)
        self.assertEqual(s55.outcome_flags_from_exit("TRAILING")["trailing"], 1)
        self.assertEqual(s55.outcome_flags_from_exit("TRAILING")["reached_tp1"], 1)


class TestSimilarityAndGateS55(unittest.TestCase):
    def test_schema_v65(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 65)
        conn = _mem()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(market_events_trade_features_s55)")}
        self.assertIn("gate_decision", cols)
        self.assertIn("gate_expected_pnl_pct", cols)
        self.assertIn("pnl_pct", cols)

    def test_similarity_prefers_closer_funding(self) -> None:
        conn = _mem()
        for i in range(30):
            _seed_closed_feature(conn, signal_id=i, funding=0.01, pnl_pct=3.0)
        for i in range(30, 40):
            _seed_closed_feature(conn, signal_id=i, funding=-0.04, pnl_pct=-5.0, result="LOSS", reached_tp1=0, stopped=1)
        conn.commit()
        feats = {
            "symbol": "BTC",
            "direction": "LONG",
            "hour": 12,
            "weekday": 2,
            "funding": 0.012,
            "atr": 1.5,
            "fear_greed": 50,
            "ai_score": 7,
        }
        sims = s55.find_similar_trades(conn, feats, k=10)
        self.assertGreaterEqual(len(sims), 5)
        # Top neighbors should skew toward positive funding cohort
        avg_f = sum(float(r["funding"]) for r in sims) / len(sims)
        self.assertGreater(avg_f, 0.0)

    def test_gate_rejects_negative_expected_pnl(self) -> None:
        conn = _mem()
        for i in range(25):
            _seed_closed_feature(
                conn, signal_id=i, funding=0.01, pnl_pct=-4.0, result="LOSS",
                reached_tp1=0, stopped=1,
            )
        conn.commit()
        feats = {
            "symbol": "BTC",
            "direction": "LONG",
            "hour": 12,
            "weekday": 2,
            "funding": 0.01,
            "atr": 1.5,
            "fear_greed": 50,
            "ai_score": 7,
        }
        with patch.object(s55, "S55_ENABLED", True), patch.object(s55, "S55_MIN_SIMILAR", 20), patch.object(
            s55, "S55_MIN_EXPECTED_PNL_PCT", 0.0,
        ):
            allow, decision, est = s55.should_open_trade(conn, features=feats, open_count=0)
        self.assertFalse(allow)
        self.assertEqual(decision, "reject_expected_pnl")
        self.assertLess(est["expected_pnl_pct"], 0)

    def test_max_open_cap(self) -> None:
        conn = _mem()
        feats = {"symbol": "ETH", "direction": "SHORT", "hour": 1, "weekday": 1}
        with patch.object(s55, "S55_ENABLED", True), patch.object(s55, "S55_MAX_OPEN_TRADES", 25):
            allow, decision, _ = s55.should_open_trade(conn, features=feats, open_count=25)
        self.assertFalse(allow)
        self.assertEqual(decision, "max_open")

    def test_disabled_allows_open(self) -> None:
        conn = _mem()
        feats = {"symbol": "ETH", "direction": "SHORT", "hour": 1, "weekday": 1}
        with patch.object(s55, "S55_ENABLED", False):
            allow, decision, _ = s55.should_open_trade(conn, features=feats, open_count=100)
        self.assertTrue(allow)
        self.assertEqual(decision, "disabled")


class TestOpenGateIntegrationS55(unittest.TestCase):
    def test_open_respects_max_and_records_features(self) -> None:
        conn = _mem()
        now = int(time.time())
        # Seed losing history so gate would reject if enough neighbors — use cold_start by keeping history small
        for i in range(5):
            conn.execute(
                """
                INSERT INTO market_events_signal_learning_s40_signals (
                  signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
                  timestamp, snapshot_decision_confidence, snapshot_funding,
                  created_at, updated_at
                ) VALUES ('g3', ?, 'BTC', 'LONG', 100, 95, 105, 110, ?, 7, 0.01, ?, ?)
                """,
                (i + 1, now, now, now),
            )
        conn.commit()

        with patch(
            "bot.research.market_events.signal_intelligence.trade_intelligence_s55.S55_MAX_OPEN_TRADES",
            2,
        ), patch(
            "bot.research.market_events.signal_intelligence.trade_intelligence_s55.S55_ENABLED",
            True,
        ), patch(
            "bot.research.market_events.signal_intelligence.trade_intelligence_s55.S55_MIN_SIMILAR",
            20,
        ):
            opened = s42.open_paper_trades_from_s40(conn, limit=10)
            conn.commit()
        self.assertEqual(opened, 2)
        open_n = conn.execute(
            "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status='OPEN'",
        ).fetchone()["n"]
        self.assertEqual(open_n, 2)
        feat_n = conn.execute(
            "SELECT COUNT(*) AS n FROM market_events_trade_features_s55 WHERE gate_decision IN ('open','cold_start')",
        ).fetchone()["n"]
        self.assertEqual(feat_n, 2)

    def test_close_finalizes_features(self) -> None:
        conn = _mem()
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42 (
              s40_signal_type, s40_signal_id, symbol, direction,
              entry, stop, tp1, tp2, created_at, status,
              mfe_pct, mae_pct, capital_usd, leverage, updated_at
            ) VALUES ('g3', 1, 'BTC', 'LONG', 100, 95, 105, 110, ?, 'OPEN', 0, 0, 100, 20, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
              gate_decision, created_at
            ) VALUES (1, 'g3', 1, 'BTC', 'LONG', 'cold_start', ?)
            """,
            (now,),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
        s42._close_trade(conn, row=row, exit_price=105.0, exit_reason="TP1", now=now + 10)
        conn.commit()
        feat = conn.execute(
            "SELECT result, reached_tp1, exit_reason, closed_at FROM market_events_trade_features_s55 WHERE s40_signal_id=1",
        ).fetchone()
        self.assertEqual(feat["result"], "WIN")
        self.assertEqual(feat["reached_tp1"], 1)
        self.assertEqual(feat["exit_reason"], "TP1")
        self.assertIsNotNone(feat["closed_at"])

    def test_disabled_opens_all_candidates(self) -> None:
        conn = _mem()
        now = int(time.time())
        for i in range(3):
            conn.execute(
                """
                INSERT INTO market_events_signal_learning_s40_signals (
                  signal_type, signal_id, symbol, direction, entry, stop, tp1, tp2,
                  timestamp, created_at, updated_at
                ) VALUES ('g3', ?, 'SOL', 'SHORT', 50, 55, 45, 40, ?, ?, ?)
                """,
                (i + 1, now, now, now),
            )
        conn.commit()
        with patch(
            "bot.research.market_events.signal_intelligence.trade_intelligence_s55.S55_ENABLED",
            False,
        ):
            opened = s42.open_paper_trades_from_s40(conn, limit=10)
            conn.commit()
        self.assertEqual(opened, 3)


if __name__ == "__main__":
    unittest.main()
