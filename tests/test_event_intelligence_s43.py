"""S43 — Event Intelligence Engine tests."""

from __future__ import annotations

import time
import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.event_intelligence.confidence import (
    event_confidence,
    freshness_score,
)
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    cluster_articles,
    run_event_engine_cycle_s43,
)
from bot.research.market_events.signal_intelligence.event_intelligence.reputation import (
    clear_reputation_cache,
    source_reputation,
)
from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
    run_narrative_engine_cycle_s42,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    clear_watchlist_cache,
)


class TestSourceReputationS43(unittest.TestCase):
    def setUp(self) -> None:
        clear_reputation_cache()

    def test_known_sources(self) -> None:
        self.assertGreaterEqual(source_reputation("Reuters"), 0.99)
        self.assertGreaterEqual(source_reputation("CoinDesk"), 0.95)
        self.assertLess(source_reputation("Unknown"), 0.5)


class TestClusterArticlesS43(unittest.TestCase):
    def test_multiple_etf_articles_become_one_event(self) -> None:
        now = int(time.time())
        articles = [
            {
                "id": 1,
                "title": "Bitcoin ETF inflows smash weekly records",
                "summary": "BlackRock IBIT leads ETF inflows into BTC",
                "source": "CoinDesk",
                "timestamp": now,
                "symbols": ["BTC"],
            },
            {
                "id": 2,
                "title": "BTC ETF inflows accelerate as BlackRock leads",
                "summary": "Spot bitcoin ETF inflows hit new highs",
                "source": "Reuters",
                "timestamp": now - 120,
                "symbols": ["BTC"],
            },
            {
                "id": 3,
                "title": "ETF inflows into Bitcoin continue to climb",
                "summary": "The Block: institutional ETF demand for BTC",
                "source": "The Block",
                "timestamp": now - 200,
                "symbols": ["BTC"],
            },
            {
                "id": 4,
                "title": "Wu: US spot BTC ETF sees strong inflows",
                "summary": "ETF inflow narrative continues",
                "source": "Wu Blockchain",
                "timestamp": now - 300,
                "symbols": ["BTC"],
            },
            {
                "id": 5,
                "title": "Fed speakers remain hawkish on rates",
                "summary": "Powell allies push back on early cuts",
                "source": "Bloomberg",
                "timestamp": now - 50,
                "symbols": [],
            },
            {
                "id": 6,
                "title": "Federal Reserve officials stay hawkish",
                "summary": "Fed meeting narrative: rates stay higher",
                "source": "Reuters",
                "timestamp": now - 80,
                "symbols": [],
            },
        ]
        clusters = cluster_articles(articles)
        # Expect ~2 thematic clusters (ETF + Fed), not 6
        self.assertLessEqual(len(clusters), 3)
        etf = [c for c in clusters if any("etf" in (a.get("title") or "").lower() for a in c)]
        self.assertTrue(etf)
        self.assertGreaterEqual(len(etf[0]), 3)

    def test_perf_1000_articles_under_2s(self) -> None:
        now = int(time.time())
        topics = [
            ("ETF inflows bitcoin", ["BTC"], "ETF"),
            ("Fed hawkish rates", [], "Fed"),
            ("Hyperliquid volume surge", ["HYPE"], "Exchange"),
            ("Ethereum layer2 fees", ["ETH", "ARB"], "Layer2"),
            ("Solana defi tvl", ["SOL"], "DeFi"),
        ]
        articles = []
        for i in range(1000):
            topic, syms, _ = topics[i % len(topics)]
            src = ["CoinDesk", "Reuters", "The Block", "Decrypt", "Wu Blockchain"][i % 5]
            articles.append({
                "id": i + 1,
                "title": f"{topic} update #{i // 5}",
                "summary": f"{topic} coverage from {src}",
                "source": src,
                "timestamp": now - (i % 50) * 60,
                "symbols": list(syms),
            })
        t0 = time.perf_counter()
        clusters = cluster_articles(articles)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0, msg=f"clustering took {elapsed:.3f}s")
        self.assertLess(len(clusters), 200)
        self.assertGreater(len(clusters), 0)


class TestEventEnginePersistS43(unittest.TestCase):
    def setUp(self) -> None:
        clear_watchlist_cache()
        clear_reputation_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s43.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v58", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 58)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cycle_persists_one_etf_event_and_feeds_narrative(self) -> None:
        now = int(time.time())
        articles = [
            {
                "id": 10,
                "title": "Bitcoin ETF inflows smash weekly records",
                "summary": "BlackRock ETF inflows into BTC",
                "source": "CoinDesk",
                "timestamp": now,
                "symbols": ["BTC"],
            },
            {
                "id": 11,
                "title": "BTC ETF inflows accelerate further",
                "summary": "Reuters: spot bitcoin ETF inflows",
                "source": "Reuters",
                "timestamp": now - 30,
                "symbols": ["BTC"],
            },
            {
                "id": 12,
                "title": "ETF inflows for Bitcoin hit fresh high",
                "summary": "The Block ETF flow report",
                "source": "The Block",
                "timestamp": now - 60,
                "symbols": ["BTC"],
            },
        ]
        result = run_event_engine_cycle_s43(now=now, articles=articles)
        self.assertGreaterEqual(result["events"], 1)
        self.assertGreaterEqual(result["created"], 1)

        with market_events_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM market_intel_events").fetchone()["n"]
            row = conn.execute(
                "SELECT title, source_count, headline_count, confidence "
                "FROM market_intel_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertGreaterEqual(n, 1)
        self.assertGreaterEqual(int(row["source_count"]), 2)
        self.assertGreaterEqual(int(row["headline_count"]), 2)

        narr = run_narrative_engine_cycle_s42(now=now + 10, write_reports=False)
        self.assertGreaterEqual(narr["events_used"], 1)
        self.assertGreaterEqual(narr["active_assets"], 1)

        with market_events_connection() as conn:
            btc = conn.execute(
                """
                SELECT news_count FROM market_asset_intelligence
                WHERE symbol='BTC' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(btc)
        self.assertGreaterEqual(int(btc["news_count"]), 1)

    def test_confidence_uses_reputation(self) -> None:
        low = event_confidence(
            sources=["Unknown"],
            headline_count=1,
            agreement=0.2,
            freshness=0.2,
            importance=0.2,
        )
        high = event_confidence(
            sources=["Reuters", "Bloomberg", "CoinDesk"],
            headline_count=5,
            agreement=0.9,
            freshness=0.95,
            importance=0.8,
        )
        self.assertGreater(high, low)
        self.assertGreater(freshness_score(int(time.time()), now=int(time.time())), 0.9)


if __name__ == "__main__":
    unittest.main()
