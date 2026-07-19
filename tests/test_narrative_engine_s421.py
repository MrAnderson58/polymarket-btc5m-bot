"""S42.1 — Narrative Engine consumes market_news_summary / briefs."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
    format_narrative_debug_s42,
    run_narrative_engine_cycle_s42,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    clear_watchlist_cache,
)


class TestNarrativeConsumesSummariesS421(unittest.TestCase):
    def setUp(self) -> None:
        clear_watchlist_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s421.db"
        configure_unit_test_db_isolation(self.db)
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            # Insert summary only (no N11 feed) — engine must still activate BTC.
            conn.execute(
                """
                INSERT INTO market_news_summary (
                  period_start, period_end, symbol, summary,
                  bullish_score, bearish_score, neutral_score,
                  importance, sources, headline_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now - 1800,
                    self.now - 60,
                    "BTC",
                    "BTC: Bitcoin ETF inflows smash weekly records\nHeadlines in window: 3.",
                    0.7,
                    0.1,
                    0.2,
                    0.8,
                    '["CoinDesk"]',
                    3,
                    self.now - 60,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_daily_briefs (
                  period_start, period_end, global_narrative,
                  top_bullish_json, top_bearish_json, macro_events_json,
                  fed_json, etf_json, whales_json, polymarket_json,
                  risk_level, risk_score, headline_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now - 7200,
                    self.now - 60,
                    "BTC ETF narrative leads the tape",
                    '[{"symbol":"BTC","score":0.7}]',
                    "[]",
                    "[]",
                    "[]",
                    '["BTC ETF"]',
                    "[]",
                    "[]",
                    "LOW",
                    0.2,
                    3,
                    self.now - 60,
                ),
            )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_summary_only_produces_btc_intel_and_reports(self) -> None:
        reports_dir = Path(self.tmp.name) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "bot.research.market_events.signal_intelligence.narrative_engine.reports.REPORTS_DIR",
            reports_dir,
        ):
            result = run_narrative_engine_cycle_s42(
                now=self.now,
                write_reports=True,
                debug=False,
            )

        self.assertGreaterEqual(result["summaries_used"], 1)
        self.assertGreaterEqual(result["active_assets"], 1)
        self.assertGreaterEqual(result["briefs_used"], 1)

        with market_events_connection() as conn:
            btc = conn.execute(
                """
                SELECT symbol, news_count, summary FROM market_asset_intelligence
                WHERE symbol='BTC' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(btc)
        self.assertEqual(btc["symbol"], "BTC")
        self.assertGreaterEqual(int(btc["news_count"]), 1)
        self.assertIn("BTC", btc["summary"])

        claude = (reports_dir / "claude_market_context.md").read_text(encoding="utf-8")
        tg = (reports_dir / "telegram_brief.md").read_text(encoding="utf-8")
        self.assertIn("BTC", claude)
        self.assertIn("BTC", tg)

    def test_debug_format_lists_loaded_and_skipped(self) -> None:
        result = run_narrative_engine_cycle_s42(
            now=self.now,
            write_reports=False,
            debug=False,
        )
        text = format_narrative_debug_s42(result["debug"])
        self.assertIn("Loaded summaries:", text)
        self.assertIn("BTC", text)
        self.assertIn("Loaded briefs:", text)
        self.assertIn("Generated assets:", text)


if __name__ == "__main__":
    unittest.main()
