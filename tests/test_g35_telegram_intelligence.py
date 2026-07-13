"""G3.5 Telegram Intelligence tests (research-only)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.telegram_g3 import format_professional_telegram_g3
from bot.research.market_events.signal_intelligence.telegram_intelligence_g35 import (
    build_hourly_market_brief_g35,
)


class TelegramIntelligenceG35Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g35.db"
        self._env = patch.dict(os.environ, {"ME_G35_TELEGRAM_INTELLIGENCE": "true"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v34(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v34", applied)
            self.assertEqual(SCHEMA_VERSION, 34)

    def test_hourly_brief_renders(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            msg = build_hourly_market_brief_g35(conn)
            self.assertIn("Hourly Market Brief", msg)
            self.assertIn("Market Score", msg)
            self.assertIn("Largest movers", msg)

    def test_signal_contains_chart_links(self) -> None:
        msg = format_professional_telegram_g3(
            symbol="SOL",
            direction="SHORT",
            confidence=8.7,
            probability=0.82,
            market_score=61,
            liquidity_state="Capitulation",
            trend_summary="12 red candles",
            funding=-0.0002,
            oi_rising=True,
            btc_context="Neutral",
            reasons=["Liquidations"],
            trade_plan={"entry": 100, "sl": 101, "tp1": 99, "tp2": 98, "tp3": 97, "risk_reward": 2.5, "position_size_pct": 3},
            claude_summary=None,
            historical=[],
            signal_uuid="test",
            paper_mode=True,
            trend_coverage_pct=100.0,
        )
        self.assertIn("TradingView", msg)
        self.assertIn("Binance Futures", msg)
        self.assertIn("Coinglass", msg)


if __name__ == "__main__":
    unittest.main()

