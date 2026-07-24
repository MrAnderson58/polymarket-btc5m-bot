"""S66 Trade Data Completeness Audit tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import trade_data_audit_s66 as s66
from bot.research.market_events.signal_intelligence import trade_intelligence_s55 as s55
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestTradeDataAuditS66(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s66.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 40) -> None:
        for i in range(n):
            ts = self.now - i * 3600
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime, atr, volatility,
                  news_score, fear_greed, oi_delta, created_at, timestamp, snapshot_json
                ) VALUES (?, 'hist:er_v2', ?, 'BTC', 'LONG', 100, 101, ?, ?, 300, 'TP1',
                  ?, ?, ?, ?, 'RANGE', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, i + 1, 0.5, 0.5,
                    0.001, 0.7, i % 24, i % 7,
                    1.2, 1.2, 0.4, 55.0, 100.0,
                    ts, ts,
                    json.dumps({
                        "session": "NewYork",
                        "decision_confidence": 0.7,
                        "btc_dominance": 52.0,
                        "market_regime_version": "s57_v1",
                        "entry_reason": "funding_ok",
                        "ema_trend": 0.5,
                        "open_interest": 100.0,
                    }),
                ),
            )
        conn.commit()

    def test_build_entry_features_attribution(self) -> None:
        row = {
            "timestamp": self.now,
            "symbol": "ETHUSDT",
            "direction": "SHORT",
            "signal_type": "validation_signal",
            "snapshot_funding": 0.0001,
            "snapshot_open_interest": 1234.0,
            "snapshot_atr": 50.0,
            "snapshot_volume": 1e6,
            "snapshot_fear_greed": 40.0,
            "snapshot_trend": 1.0,
            "snapshot_news_score": 0.2,
            "snapshot_decision_confidence": 0.81,
        }
        with research_connection() as conn:
            feats = s55.build_entry_features(conn, row)
        self.assertEqual(feats.get("direction"), "SHORT")
        self.assertEqual(feats.get("strategy"), "validation_signal")
        self.assertEqual(feats.get("session"), s55._session_from_hour(feats.get("hour")))
        self.assertEqual(feats.get("decision_confidence"), 0.81)
        self.assertIn("features_json", feats)
        payload = json.loads(feats["features_json"])
        self.assertIn("session", payload)
        self.assertIn("strategy", payload)

    def test_audit_exports(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s66.run_trade_data_audit(conn, report_dir=self.out)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("stage"), "S66")
        self.assertEqual(out.get("n_trades"), 40)
        fields = {f["field"]: f for f in (out.get("fields") or [])}
        self.assertIn("confidence", fields)
        self.assertIn("funding", fields)
        self.assertIn("entry_reason", fields)
        self.assertIn("session", fields)
        self.assertGreaterEqual(fields["direction"]["fill_pct"], 99.0)
        self.assertGreaterEqual(fields["funding"]["fill_pct"], 99.0)
        self.assertGreaterEqual(fields["session"]["fill_pct"], 99.0)
        self.assertGreaterEqual(fields["entry_reason"]["fill_pct"], 99.0)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        self.assertIn("Trade Data Completeness", md.read_text(encoding="utf-8"))
        payload = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("stage"), "S66")

    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("audit-trade-data", proc.stdout)


if __name__ == "__main__":
    unittest.main()
