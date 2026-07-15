"""Phase N1.1 — News Collector MVP tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    fetch_latest_news_n11,
    format_news_latest_n11,
    is_duplicate_title_n11,
    news_feed_dashboard_n11,
    run_news_update_n11,
    title_similarity_n11,
)
from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS


_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Test</title>
<item>
  <title>Bitcoin ETF inflows hit record high</title>
  <link>https://example.com/btc-etf</link>
  <description>BTC sees large inflows</description>
  <pubDate>Mon, 14 Jul 2026 12:00:00 GMT</pubDate>
</item>
<item>
  <title>Solana network upgrade lands this week</title>
  <link>https://example.com/sol</link>
  <description>SOL validators roll out upgrade</description>
  <pubDate>Mon, 14 Jul 2026 11:00:00 GMT</pubDate>
</item>
<item>
  <title>Bitcoin ETF inflows hit record highs</title>
  <link>https://example.com/btc-etf-2</link>
  <description>Near duplicate</description>
  <pubDate>Mon, 14 Jul 2026 10:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


class TestNewsCollectorN11(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(str(Path(self.tmp.name) / "me.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_version(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 49)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertTrue(
                any(v == "v49" for v in applied)
                or conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='market_news_feed_n11'"
                ).fetchone()
            )

    def test_dedup_similarity(self) -> None:
        a = "Bitcoin ETF inflows hit record high"
        b = "Bitcoin ETF inflows hit record highs"
        self.assertGreaterEqual(title_similarity_n11(a, b), 0.90)
        self.assertTrue(is_duplicate_title_n11(b, [a]))

    def test_update_inserts_and_dedups(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)

            def _fake_get(url: str, *, timeout: float = 12.0) -> str:
                return _SAMPLE_RSS

            with patch(
                "bot.research.market_events.signal_intelligence.news_collector_n11._http_get",
                side_effect=_fake_get,
            ):
                result = run_news_update_n11(conn, limit_per_feed=10)
                # Second run should skip all as duplicates
                result2 = run_news_update_n11(conn, limit_per_feed=10)

            n = conn.execute("SELECT COUNT(*) AS n FROM market_news_feed_n11").fetchone()["n"]

        # 5 feeds × 3 items, but near-dup of BTC ETF collapsed across batch → unique titles per feed
        # Across feeds identical titles also dedup → expect 2 unique titles total from sample
        self.assertEqual(n, 2)
        self.assertGreaterEqual(result["inserted"], 2)
        self.assertEqual(result2["inserted"], 0)
        self.assertGreaterEqual(result2["skipped_duplicates"], 1)

        with market_events_connection() as conn:
            rows = fetch_latest_news_n11(conn, limit=10)
            text = format_news_latest_n11(rows)
            dash = news_feed_dashboard_n11(conn, limit=10)
        self.assertIn("CoinDesk", text)  # last source written or any — feeds iterate in order
        self.assertIn("News Feed", text)
        self.assertEqual(dash["tab"], "News Feed")
        self.assertEqual(len(dash["items"]), 2)

    def test_news_in_pure_ro(self) -> None:
        self.assertIn("/news", PURE_READONLY_COMMANDS)


if __name__ == "__main__":
    unittest.main()
