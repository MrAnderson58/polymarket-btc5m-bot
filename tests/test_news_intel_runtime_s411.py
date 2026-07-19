"""S41.1 — News Intelligence runtime integration (worker → summary → narrative)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.process_manager import SERVICES
from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
    run_narrative_engine_cycle_s42,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    insert_news_item_n11,
)
from bot.research.market_events.signal_intelligence.news_intelligence.aggregator import (
    run_news_aggregation_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.briefs import (
    run_global_brief_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.worker import (
    run_news_intelligence_worker_s41,
)
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    run_event_engine_cycle_s43,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    clear_watchlist_cache as clear_narrative_watchlist,
)
from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
    clear_watchlist_cache as clear_news_watchlist,
)


class TestNewsIntelRegisteredS411(unittest.TestCase):
    def test_start_all_includes_news_intel(self) -> None:
        keys = [s.key for s in SERVICES]
        self.assertIn("news-intel", keys)
        self.assertIn("event-engine", keys)
        # News → events → narrative
        self.assertLess(keys.index("news-intel"), keys.index("event-engine"))
        self.assertLess(keys.index("event-engine"), keys.index("narrative-engine"))
        svc = next(s for s in SERVICES if s.key == "news-intel")
        self.assertEqual(svc.log_name, "news-intelligence.log")
        self.assertIn("news-intel-worker", svc.module_args)


class TestNewsIntelRuntimePipelineS411(unittest.TestCase):
    def setUp(self) -> None:
        clear_news_watchlist()
        clear_narrative_watchlist()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s411.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            self.now = now
            for title, summary in (
                ("Bitcoin ETF inflows hit record high", "BTC ETF bullish flows"),
                ("Ethereum Layer2 fees drop", "ETH L2 activity rises"),
                ("Solana DeFi TVL surges", "SOL protocol inflows"),
            ):
                insert_news_item_n11(
                    conn,
                    {
                        "published_at": now,
                        "source": "CoinDesk",
                        "title": title,
                        "summary": summary,
                        "url": "https://example.com/s411",
                    },
                    now=now,
                )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_worker_cycle_populates_summary_and_narrative_report(self) -> None:
        # One worker cycle: skip live RSS (use inserted N11 rows).
        stats = run_news_intelligence_worker_s41(
            aggregation_interval_sec=1,
            brief_interval_sec=1,
            poll_sec=1,
            max_cycles=1,
            collect_rss=False,
        )
        self.assertGreaterEqual(stats["aggregations"], 1)
        self.assertGreaterEqual(stats["briefs"], 1)
        self.assertEqual(stats["errors"], 0)

        with market_events_connection() as conn:
            n_sum = conn.execute(
                "SELECT COUNT(*) AS n FROM market_news_summary"
            ).fetchone()["n"]
            n_brief = conn.execute(
                "SELECT COUNT(*) AS n FROM market_daily_briefs"
            ).fetchone()["n"]
        self.assertGreater(n_sum, 0)
        self.assertGreater(n_brief, 0)

        # S43: cluster articles into events before narrative.
        ev = run_event_engine_cycle_s43(now=self.now + 30)
        self.assertGreaterEqual(ev["events"], 1)

        reports_dir = Path(self.tmp.name) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "bot.research.market_events.signal_intelligence.narrative_engine.reports.REPORTS_DIR",
            reports_dir,
        ):
            result = run_narrative_engine_cycle_s42(
                window_sec=7200,
                now=self.now + 60,
                write_reports=True,
            )
        self.assertGreater(result["events_used"], 0)
        self.assertGreater(result["active_assets"], 0)
        self.assertGreaterEqual(result["assets_written"], 1)

        claude = reports_dir / "claude_market_context.md"
        tg = reports_dir / "telegram_brief.md"
        self.assertTrue(claude.is_file())
        self.assertTrue(tg.is_file())
        claude_txt = claude.read_text(encoding="utf-8")
        tg_txt = tg.read_text(encoding="utf-8")
        self.assertIn("# MARKET CONTEXT", claude_txt)
        self.assertIn("Top Events", claude_txt)
        self.assertTrue(
            "Bitcoin" in claude_txt or "BTC" in claude_txt or "ETF" in claude_txt,
            msg="claude report should mention real events",
        )
        self.assertIn("AI Market Brief", tg_txt)
        self.assertIn("Top Events", tg_txt)

    def test_aggregate_alone_writes_summary(self) -> None:
        agg = run_news_aggregation_cycle_s41(window_sec=3600, now=self.now + 30)
        self.assertGreaterEqual(agg["summaries_written"], 1)
        brief = run_global_brief_cycle_s41(window_sec=7200, now=self.now + 30)
        self.assertGreater(brief["brief_id"], 0)


if __name__ == "__main__":
    unittest.main()
