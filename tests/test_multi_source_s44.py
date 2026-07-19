"""S44 — Multi-Source Intelligence Platform tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    cluster_articles,
    run_event_engine_cycle_s43,
)
from bot.research.market_events.signal_intelligence.multi_source.config_loader import (
    clear_sources_cache,
    enabled_sources,
    load_sources_config,
)
from bot.research.market_events.signal_intelligence.multi_source.health import (
    fetch_source_health,
    format_source_health_report,
    record_source_health,
)
from bot.research.market_events.signal_intelligence.multi_source.orchestrator import (
    run_collector_safe,
    run_multi_source_cycle_s44,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    insert_news_item_n11,
    is_duplicate_title_n11,
)


class TestConfigLoaderS44(unittest.TestCase):
    def setUp(self) -> None:
        clear_sources_cache()

    def test_rss_config_has_expanded_sources(self) -> None:
        rows = load_sources_config("rss_sources")
        names = {r.get("name") for r in rows}
        for required in (
            "CoinDesk", "The Block", "Blockworks", "Decrypt",
            "Cointelegraph", "CryptoSlate", "Bitcoin Magazine",
            "The Defiant", "DL News",
        ):
            self.assertIn(required, names)
        enabled = enabled_sources("rss_sources")
        self.assertTrue(all(r.get("enabled", True) for r in enabled))
        # Reuters Crypto is disabled by default in config
        disabled = [r for r in rows if r.get("name") == "Reuters Crypto"]
        self.assertTrue(disabled)
        self.assertFalse(disabled[0].get("enabled"))

    def test_telegram_and_twitter_configs(self) -> None:
        tg = load_sources_config("telegram_sources")
        tw = load_sources_config("twitter_sources")
        self.assertGreaterEqual(len(tg), 6)
        self.assertTrue(any(r.get("name") == "WuBlockchain" for r in tg))
        self.assertTrue(any(r.get("username") == "lookonchain" for r in tw))


class TestDedupeS44(unittest.TestCase):
    def test_title_dedupe(self) -> None:
        existing = ["Bitcoin ETF inflows smash weekly records"]
        self.assertTrue(
            is_duplicate_title_n11(
                "Bitcoin ETF inflows smash weekly records", existing,
            )
        )
        self.assertFalse(
            is_duplicate_title_n11("Completely unrelated headline xyz", existing)
        )


class TestMergeAcrossSourcesS44(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s44.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v59", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 59)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_event_engine_merges_rss_and_macro_like_articles(self) -> None:
        now = int(time.time())
        articles = [
            {
                "id": 1,
                "title": "Bitcoin ETF inflows smash weekly records",
                "summary": "CoinDesk ETF inflows",
                "source": "CoinDesk",
                "timestamp": now,
                "symbols": ["BTC"],
            },
            {
                "id": 2,
                "title": "BTC ETF inflows accelerate",
                "summary": "tg:ETFNews flow update",
                "source": "tg:ETFNews",
                "timestamp": now - 30,
                "symbols": ["BTC"],
            },
            {
                "id": "macro-1",
                "title": "Fed speakers remain hawkish",
                "summary": "macro:Fed rates",
                "source": "macro:Fed",
                "timestamp": now - 20,
                "symbols": [],
            },
        ]
        clusters = cluster_articles(articles)
        self.assertLessEqual(len(clusters), 3)
        result = run_event_engine_cycle_s43(now=now, articles=articles)
        self.assertGreaterEqual(result["events"], 1)
        with market_events_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_intel_events"
            ).fetchone()["n"]
        self.assertGreaterEqual(n, 1)


class TestFailureRecoveryS44(unittest.TestCase):
    def test_safe_collector_isolates_failure(self) -> None:
        def boom() -> dict:
            raise RuntimeError("simulated collector crash")

        out = run_collector_safe("boom", boom)
        self.assertFalse(out.get("ok"))
        self.assertIn("simulated", out.get("error") or "")

    def test_orchestrator_continues_after_one_failure(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s44b.db"
        configure_unit_test_db_isolation(self.db)
        try:
            with market_events_connection() as conn:
                apply_migrations(conn)

            def ok_rss() -> dict:
                return {"source_type": "rss", "ok": True, "inserted": 0}

            def bad_tg() -> dict:
                raise RuntimeError("tg down")

            with patch(
                "bot.research.market_events.signal_intelligence.multi_source.orchestrator.COLLECTORS",
                (("rss", ok_rss), ("telegram", bad_tg)),
            ):
                result = run_multi_source_cycle_s44(run_events=False)
            self.assertTrue(result.get("ok"))
            self.assertTrue((result["collectors"]["rss"] or {}).get("ok", True))
            self.assertFalse((result["collectors"]["telegram"] or {}).get("ok"))
        finally:
            self.tmp.cleanup()


class TestSourceHealthS44(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s44h.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_health_record_and_report(self) -> None:
        record_source_health(
            source_type="rss",
            source_name="CoinDesk",
            status="ok",
            latency_ms=12.5,
            items=3,
        )
        record_source_health(
            source_type="telegram",
            source_name="WuBlockchain",
            status="error",
            error="timeout",
            latency_ms=900.0,
            items=0,
        )
        rows = fetch_source_health()
        self.assertGreaterEqual(len(rows), 2)
        text = format_source_health_report(rows)
        self.assertIn("Source Health", text)
        self.assertIn("CoinDesk", text)


class TestCollectorInsertPathS44(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s44c.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_insert_news_from_telegram_source_type(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            insert_news_item_n11(
                conn,
                {
                    "published_at": now,
                    "source": "tg:Lookonchain",
                    "source_type": "telegram",
                    "title": "Whale moves 1000 BTC to Binance",
                    "summary": "On-chain whale transfer",
                    "url": "https://t.me/s/lookonchain",
                },
                now=now,
            )
            conn.commit()
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM market_news_feed_n11"
            ).fetchone()["n"]
        self.assertGreaterEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
