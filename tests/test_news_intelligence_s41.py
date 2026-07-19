"""S41 — News Intelligence Layer tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.news_intelligence.aggregator import (
    run_news_aggregation_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.briefs import (
    build_global_brief,
    run_global_brief_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.reports import (
    render_market_brief_md,
    write_period_reports_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.tagging import (
    tag_news_item,
)
from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
    clear_watchlist_cache,
    detect_symbols,
    load_watchlist,
    watched_symbols,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    extract_symbols_n11,
    insert_news_item_n11,
)


class TestWatchlistS41(unittest.TestCase):
    def setUp(self) -> None:
        clear_watchlist_cache()

    def test_default_assets_present(self) -> None:
        syms = watched_symbols()
        for s in ("BTC", "ETH", "HYPE", "SOL", "ATOM"):
            self.assertIn(s, syms)
        self.assertGreaterEqual(len(syms), 20)

    def test_alias_detection(self) -> None:
        self.assertIn("BTC", detect_symbols("Bitcoin rallies after ETF news"))
        self.assertIn("ETH", detect_symbols("Ethereum upgrade live"))
        self.assertIn("HYPE", detect_symbols("Hyperliquid volume spikes"))


class TestTaggingS41(unittest.TestCase):
    def test_tag_fields(self) -> None:
        tagged = tag_news_item(
            timestamp=1_700_000_000,
            source="CoinDesk",
            title="Bitcoin and Hyperliquid surge",
            body="ETF inflows continue.",
            url="https://example.com/a",
        )
        self.assertEqual(tagged["source_type"], "rss")
        self.assertIn("BTC", tagged["symbols"])
        self.assertIn("HYPE", tagged["symbols"])
        self.assertIn("importance", tagged)
        self.assertEqual(tagged["language"], "en")


class TestNewsIntelPipelineS41(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s41.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v56", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 56)
            now = 1_700_000_100
            for title, summary in (
                ("Bitcoin ETF inflows hit record", "Bullish BTC ETF flows"),
                ("Ethereum hack fears rise", "Exploit rumor hits ETH"),
                ("Hyperliquid volume soars", "HYPE perpetual volume"),
            ):
                insert_news_item_n11(
                    conn,
                    {
                        "published_at": now,
                        "source": "CoinDesk",
                        "title": title,
                        "summary": summary,
                        "url": "https://example.com",
                    },
                    now=now,
                )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_aggregate_and_brief_and_reports(self) -> None:
        agg = run_news_aggregation_cycle_s41(window_sec=3600, now=1_700_000_200)
        self.assertGreaterEqual(agg["summaries_written"], 1)

        with market_events_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM market_news_summary").fetchone()["n"]
        self.assertGreaterEqual(n, 1)

        brief = run_global_brief_cycle_s41(window_sec=7200, now=1_700_000_200)
        self.assertIn(brief["risk_level"], ("LOW", "MEDIUM", "HIGH"))
        self.assertGreater(brief["brief_id"], 0)

        reports_dir = Path(self.tmp.name) / "reports"
        out = write_period_reports_s41(reports_dir=reports_dir, now=1_700_000_200)
        for name in ("morning.md", "afternoon.md", "evening.md"):
            path = reports_dir / name
            self.assertTrue(path.is_file(), msg=name)
            text = path.read_text(encoding="utf-8")
            self.assertIn("# Market Brief", text)
            self.assertIn("Top narratives", text)
            self.assertIn("AI conclusion", text)
        self.assertEqual(out["period"] in ("morning", "afternoon", "evening"), True)

    def test_extract_symbols_uses_watchlist(self) -> None:
        syms = extract_symbols_n11("Hyperliquid listing", "")
        self.assertIn("HYPE", syms)

    def test_render_without_brief(self) -> None:
        md = render_market_brief_md(
            brief=None,
            summaries=[],
            period="morning",
            generated_at=1_700_000_000,
        )
        self.assertIn("# Market Brief", md)

    def test_build_global_brief_empty(self) -> None:
        b = build_global_brief([], now=1)
        self.assertEqual(b["risk_level"], "LOW")


class TestWatchlistYamlFileS41(unittest.TestCase):
    def test_config_file_exists(self) -> None:
        from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
            DEFAULT_WATCHLIST_PATH,
        )
        self.assertTrue(DEFAULT_WATCHLIST_PATH.is_file())
        clear_watchlist_cache()
        wl = load_watchlist()
        self.assertGreaterEqual(len(wl["assets"]), 20)


if __name__ == "__main__":
    unittest.main()
