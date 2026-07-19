"""S42 — AI Narrative Engine tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.narrative_engine.confidence import (
    agreement_from_sentiments,
    calculate_confidence,
)
from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
    run_narrative_engine_cycle_s42,
)
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    run_event_engine_cycle_s43,
)
from bot.research.market_events.signal_intelligence.narrative_engine.narratives import (
    detect_narratives,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    clear_watchlist_cache,
    detect_symbols,
    watched_symbols,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    insert_news_item_n11,
)


class TestNarrativeDetectionS42(unittest.TestCase):
    def test_multi_labels(self) -> None:
        labels = detect_narratives(
            "BlackRock Bitcoin ETF inflows rise while Fed rate cut bets grow"
        )
        self.assertIn("ETF", labels)
        self.assertIn("Fed", labels)
        self.assertIn("Institutional Adoption", labels)

    def test_hack_security(self) -> None:
        labels = detect_narratives("Protocol hack drains liquidity after exploit")
        self.assertIn("Hack", labels)


class TestConfidenceS42(unittest.TestCase):
    def test_higher_with_quality_and_volume(self) -> None:
        low = calculate_confidence(
            source_qualities=[0.4],
            headline_count=1,
            importance=0.3,
            agreement=0.2,
            freshness_sec=80_000,
        )
        high = calculate_confidence(
            source_qualities=[0.9, 0.85],
            headline_count=10,
            importance=0.9,
            agreement=0.9,
            freshness_sec=600,
        )
        self.assertGreater(high, low)
        self.assertGreaterEqual(high, 0.5)
        self.assertLessEqual(high, 1.0)

    def test_agreement(self) -> None:
        items = [
            {"bullish": 0.8, "bearish": 0.1},
            {"bullish": 0.7, "bearish": 0.2},
            {"bullish": 0.75, "bearish": 0.15},
        ]
        self.assertGreater(agreement_from_sentiments(items), 0.5)


class TestAssetAggregationS42(unittest.TestCase):
    def setUp(self) -> None:
        clear_watchlist_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s42.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v56", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 58)
            now = 1_800_000_000
            for title, summary in (
                ("Bitcoin ETF inflows hit record after BlackRock buy", "Institutional BTC"),
                ("Ethereum Layer2 fees drop on Arbitrum", "ETH L2"),
                ("Fed signals rate cut as CPI cools", "Macro Fed"),
                ("Hyperliquid volume soars", "HYPE perps"),
            ):
                insert_news_item_n11(
                    conn,
                    {
                        "published_at": now,
                        "source": "CoinDesk",
                        "title": title,
                        "summary": summary,
                        "url": "https://example.com",
                        "symbols": None,
                    },
                    now=now,
                )
            # Ensure symbols tagged for watched assets
            conn.execute(
                "UPDATE market_news_feed_n11 SET symbols='BTC' "
                "WHERE title LIKE 'Bitcoin%'"
            )
            conn.execute(
                "UPDATE market_news_feed_n11 SET symbols='ETH' "
                "WHERE title LIKE 'Ethereum%'"
            )
            conn.execute(
                "UPDATE market_news_feed_n11 SET symbols='HYPE' "
                "WHERE title LIKE 'Hyperliquid%'"
            )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cycle_writes_intel_and_reports(self) -> None:
        reports_dir = Path(self.tmp.name) / "reports"
        # Cluster N11 articles into intel events, then narrative reads events only.
        run_event_engine_cycle_s43(now=1_800_000_050, lookback_sec=7200)
        result = run_narrative_engine_cycle_s42(
            window_sec=7200,
            now=1_800_000_100,
            write_reports=False,
        )
        self.assertEqual(result["assets_written"], len(watched_symbols()))
        self.assertGreaterEqual(result["active_assets"], 1)
        self.assertGreaterEqual(result["events_used"], 1)

        with market_events_connection() as conn:
            n_intel = conn.execute(
                "SELECT COUNT(*) AS n FROM market_asset_intelligence"
            ).fetchone()["n"]
            n_top = conn.execute(
                "SELECT COUNT(*) AS n FROM market_top_assets"
            ).fetchone()["n"]
            btc = conn.execute(
                """
                SELECT narrative, confidence, news_count
                FROM market_asset_intelligence
                WHERE symbol='BTC'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(n_intel, len(watched_symbols()))
        self.assertGreaterEqual(n_top, 1)
        self.assertGreaterEqual(int(btc["news_count"]), 1)
        self.assertGreater(float(btc["confidence"]), 0.1)

        from bot.research.market_events.signal_intelligence.narrative_engine.reports import (
            write_narrative_reports_s42,
        )
        with market_events_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM market_asset_intelligence ORDER BY symbol"
            ).fetchall()
            assets = []
            for r in rows:
                d = dict(r)
                d["top_headlines"] = []
                assets.append(d)
        paths = write_narrative_reports_s42(
            assets=assets, briefs=[], now=1_800_000_100, reports_dir=reports_dir,
        )
        claude = Path(paths["claude_market_context"]).read_text(encoding="utf-8")
        tg = Path(paths["telegram_brief"]).read_text(encoding="utf-8")
        self.assertIn("# MARKET CONTEXT", claude)
        self.assertIn("### BTC", claude)
        self.assertIn("🚨 AI Market Brief", tg)
        self.assertIn("AI Conclusion", tg)

    def test_symbol_aliases(self) -> None:
        self.assertIn("BTC", detect_symbols("Bitcoin rallies"))
        self.assertIn("HYPE", detect_symbols("Hyperliquid listing"))


if __name__ == "__main__":
    unittest.main()
