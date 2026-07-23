"""S57 Market Regime Intelligence tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import market_regime_s57 as s57
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55


class TestMarketRegimeS57(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s57.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_v67(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 67)
        with market_events_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_regime_runs_s57'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_classifier_labels(self) -> None:
        strong = s57.classify_market_regime(btc_return_pct=2.0)
        self.assertEqual(strong.regime, s57.REGIME_STRONG_BULL)
        self.assertEqual(strong.label, "Strong Bull")
        bear = s57.classify_market_regime(btc_return_pct=-2.0)
        self.assertEqual(bear.regime, s57.REGIME_STRONG_BEAR)
        rng = s57.classify_market_regime(btc_return_pct=0.0, trend=0.0)
        self.assertEqual(rng.regime, s57.REGIME_RANGE)

    def test_build_entry_features_sets_regime(self) -> None:
        with market_events_connection() as conn:
            row = {
                "symbol": "BTC",
                "direction": "LONG",
                "timestamp": self.now,
                "snapshot_funding": 0.0001,
                "snapshot_trend": 0.5,
                "snapshot_fear_greed": 70,
                "snapshot_decision_confidence": 0.6,
            }
            with patch.object(s57, "btc_return_pct_from_conn", return_value=1.8):
                feats = s55.build_entry_features(conn, row)
            self.assertEqual(feats.get("market_regime"), s57.REGIME_STRONG_BULL)
            self.assertIn("regime_score", feats)

    def test_regime_gate_blocks_on_bad_stats(self) -> None:
        with market_events_connection() as conn:
            # Seed enough losing LONG snapshots in STRONG_BEAR
            for i in range(40):
                conn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      market_regime, created_at
                    ) VALUES ('s57', ?, 'BTC', 'LONG', 100, 99, -5.0, -5.0, 100, 'STOP',
                      'STRONG_BEAR', ?)
                    """,
                    (i + 1, self.now),
                )
            conn.commit()
            feats = {
                "symbol": "BTC",
                "direction": "LONG",
                "market_regime": s57.REGIME_STRONG_BEAR,
                "trend": -1.0,
                "fear_greed": 20,
                "funding": 0.001,
            }
            with patch.object(s57, "S57_FILTER_ENABLED", True), patch.object(s57, "S57_MIN_EVIDENCE", 30):
                ok, reason, meta = s57.apply_regime_gate(conn, feats)
            self.assertFalse(ok)
            self.assertEqual(reason, s57.GATE_REGIME_BLOCK)
            self.assertGreaterEqual(int((meta.get("regime_dir_stats") or {}).get("n") or 0), 30)

    def test_stats_and_report(self) -> None:
        with market_events_connection() as conn:
            regimes = (
                s57.REGIME_STRONG_BULL,
                s57.REGIME_WEAK_BULL,
                s57.REGIME_RANGE,
                s57.REGIME_WEAK_BEAR,
                s57.REGIME_STRONG_BEAR,
            )
            for i in range(100):
                reg = regimes[i % 5]
                direction = "LONG" if i % 2 == 0 else "SHORT"
                pnl = 8.0 if (reg == s57.REGIME_STRONG_BULL and direction == "LONG") else -3.0
                symbol = "BTC" if i % 3 == 0 else ("ETH" if i % 3 == 1 else "ARB")
                conn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      market_regime, funding, trend, fear_greed, created_at
                    ) VALUES ('s57r', ?, ?, ?, 100, 101, ?, ?, 200, 'TP1',
                      ?, 0.0001, 0.2, 50, ?)
                    """,
                    (i + 1, symbol, direction, pnl, pnl, reg, self.now),
                )
            conn.commit()
            stats = s57.compute_regime_stats(conn)
            self.assertEqual(len(stats["regimes"]), 5)
            bull = next(r for r in stats["regimes"] if r["regime"] == s57.REGIME_STRONG_BULL)
            self.assertGreater(bull["n"], 0)
            self.assertIn("best_symbols", bull)
            self.assertIn("long", bull)
            text = s57.format_regime_report(conn)
            self.assertIn("Strong Bull", text)
            self.assertIn("Statistical findings", text)
            block = "\n".join(s57.format_s57_report_block(conn))
            self.assertIn("S57 Market Regime", block)

    def test_suggestions_off_by_default(self) -> None:
        with market_events_connection() as conn:
            for i in range(40):
                conn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      market_regime, created_at
                    ) VALUES ('s57s', ?, 'BTC', 'LONG', 100, 99, -4.0, -4.0, 100, 'STOP',
                      'STRONG_BEAR', ?)
                    """,
                    (i + 1, self.now),
                )
            conn.commit()
            out = s57.run_regime_analysis(conn, with_llm=False, write_suggestions=False)
            self.assertEqual(out.get("suggestions_created"), 0)
            self.assertIsNotNone(out.get("run_id"))
            self.assertIn("Confidence", out.get("llm_text") or "")

    def test_should_open_trade_regime_first(self) -> None:
        with market_events_connection() as conn:
            for i in range(40):
                conn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      market_regime, created_at
                    ) VALUES ('s57g', ?, 'ETH', 'SHORT', 100, 101, -6.0, -6.0, 100, 'STOP',
                      'STRONG_BULL', ?)
                    """,
                    (i + 1, self.now),
                )
            conn.commit()
            feats = {
                "symbol": "ETH",
                "direction": "SHORT",
                "market_regime": s57.REGIME_STRONG_BULL,
                "hour": 12,
                "weekday": 2,
            }
            with patch.object(s55, "S55_ENABLED", True), \
                    patch.object(s57, "S57_FILTER_ENABLED", True), \
                    patch.object(s57, "S57_MIN_EVIDENCE", 30):
                allow, decision, estimate = s55.should_open_trade(
                    conn, features=feats, open_count=0,
                )
            self.assertFalse(allow)
            self.assertEqual(decision, s55.GATE_REGIME_BLOCK)
            self.assertIn("regime", estimate)


if __name__ == "__main__":
    unittest.main()
