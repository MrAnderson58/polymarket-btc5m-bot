"""S45 — Intelligence Quality Engine regression tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.narrative_engine.binding import (
    bind_event_assets,
    entity_symbols,
)
from bot.research.market_events.signal_intelligence.narrative_engine.narratives import (
    detect_narratives,
)
from bot.research.market_events.signal_intelligence.narrative_engine.quality import (
    compute_net_score,
    enrich_event_for_report,
    market_impact,
    render_macro_intelligence,
    render_polymarket_intelligence,
    select_top_by_net_score,
    why_it_matters,
)
from bot.research.market_events.signal_intelligence.narrative_engine.reports import (
    render_claude_market_context,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    clear_watchlist_cache,
)


class TestNetScoreS45(unittest.TestCase):
    def test_net_score_formula(self) -> None:
        self.assertEqual(compute_net_score(0.8, 0.2), 0.6)
        self.assertEqual(compute_net_score(0.1, 0.7), -0.6)
        self.assertEqual(compute_net_score(0.25, 0.25), 0.0)

    def test_asset_not_in_both_top_lists(self) -> None:
        assets = [
            {
                "symbol": "BTC",
                "news_count": 3,
                "bullish_score": 0.7,
                "bearish_score": 0.2,
                "neutral_score": 0.1,
                "importance": 0.8,
                "confidence": 0.7,
                "risk_level": "MEDIUM",
                "summary": "btc up",
                "narrative": "ETF",
            },
            {
                "symbol": "ETH",
                "news_count": 2,
                "bullish_score": 0.2,
                "bearish_score": 0.75,
                "neutral_score": 0.05,
                "importance": 0.7,
                "confidence": 0.6,
                "risk_level": "HIGH",
                "summary": "eth down",
                "narrative": "DeFi",
            },
            {
                "symbol": "SOL",
                "news_count": 1,
                "bullish_score": 0.45,
                "bearish_score": 0.40,
                "neutral_score": 0.15,
                "importance": 0.4,
                "confidence": 0.4,
                "risk_level": "LOW",
                "summary": "flat",
                "narrative": "L2",
            },
        ]
        for a in assets:
            a["net_score"] = compute_net_score(a["bullish_score"], a["bearish_score"])
        bull, bear = select_top_by_net_score(assets, threshold=0.12, limit=5)
        bull_syms = {a["symbol"] for a in bull}
        bear_syms = {a["symbol"] for a in bear}
        self.assertTrue(bull_syms.isdisjoint(bear_syms))
        self.assertIn("BTC", bull_syms)
        self.assertIn("ETH", bear_syms)
        # |net| below threshold → excluded
        self.assertNotIn("SOL", bull_syms | bear_syms)


class TestBindingS45(unittest.TestCase):
    def setUp(self) -> None:
        clear_watchlist_cache()

    def test_circle_not_btc(self) -> None:
        syms = bind_event_assets(
            title="Circle expands USDC reserves",
            body="Stablecoin issuer Circle announces USDC minting for DeFi",
            narratives=["Stablecoins", "DeFi"],
        )
        self.assertNotIn("BTC", syms)
        self.assertTrue(set(syms) & {"ETH", "AAVE", "XRP"})

    def test_solana_staking_to_sol(self) -> None:
        syms = bind_event_assets(
            title="Solana staking yields rise",
            body="Validators report higher SOL staking participation",
        )
        self.assertIn("SOL", syms)

    def test_mica_maps_regulation_assets(self) -> None:
        ents = entity_symbols("EU MiCA rules hit exchanges and stablecoins")
        self.assertTrue(set(ents) & {"XRP", "BNB", "ETH"})


class TestImpactAndWhyS45(unittest.TestCase):
    def test_impact_and_why(self) -> None:
        ev = {
            "title": "Bitcoin ETF inflows smash records",
            "summary": "BlackRock IBIT leads",
            "sentiment": 0.5,
            "importance": 0.85,
            "confidence": 0.8,
            "source_count": 5,
            "freshness": 0.9,
            "symbols": ["BTC"],
            "narrative": "ETF",
            "sources": ["CoinDesk", "Reuters", "tg:ETFNews", "x:watcher"],
            "last_seen": int(time.time()),
        }
        enriched = enrich_event_for_report(ev)
        self.assertIn(enriched["market_impact"], {"LOW", "MEDIUM", "HIGH", "CRITICAL"})
        self.assertTrue(enriched["why_it_matters"])
        self.assertIn("matters", enriched["why_it_matters"].lower())
        self.assertIn("RSS", enriched["confirmed_by"] or "")

    def test_critical_impact(self) -> None:
        level = market_impact(
            importance=0.95,
            confidence=0.9,
            source_count=6,
            freshness=0.95,
            affected_assets=["BTC", "ETH", "SOL"],
        )
        self.assertIn(level, {"HIGH", "CRITICAL"})


class TestMacroPolySectionsS45(unittest.TestCase):
    def test_macro_not_empty_with_data(self) -> None:
        rows = [
            {"name": "Fed", "title": "Fed speakers hawkish", "summary": "rates", "created_at": 1},
            {"name": "DXY", "title": "DXY macro watch", "summary": "dollar", "created_at": 1},
            {"name": "US10Y", "title": "US10Y macro watch", "summary": "yields", "created_at": 1},
            {"name": "Oil", "title": "Oil macro watch", "summary": "crude", "created_at": 1},
        ]
        text = render_macro_intelligence(rows)
        self.assertNotEqual(text, "—")
        self.assertIn("Macro Summary", text)
        self.assertIn("Fed", text)

    def test_polymarket_not_empty_with_data(self) -> None:
        now = int(time.time())
        rows = [
            {
                "name": "fed_cut",
                "question": "Fed cuts in September?",
                "probability": 0.62,
                "created_at": now - 100,
            },
            {
                "name": "fed_cut",
                "question": "Fed cuts in September?",
                "probability": 0.71,
                "created_at": now,
            },
            {
                "name": "btc_100k",
                "question": "BTC above 100k?",
                "probability": 0.44,
                "created_at": now,
            },
        ]
        text = render_polymarket_intelligence(rows)
        self.assertNotEqual(text, "—")
        self.assertIn("Highest probability", text)
        self.assertIn("consensus", text.lower())


class TestReportQualityS45(unittest.TestCase):
    def test_report_sections_filled(self) -> None:
        now = int(time.time())
        events = [
            enrich_event_for_report({
                "title": "ETF inflows accelerate",
                "summary": "flows",
                "sentiment": 0.4,
                "importance": 0.7,
                "confidence": 0.75,
                "source_count": 4,
                "freshness": 0.8,
                "symbols": ["BTC"],
                "narrative": "ETF",
                "sources": ["CoinDesk", "Reuters", "tg:news"],
                "last_seen": now,
            }),
        ]
        assets = [
            {
                "symbol": "BTC",
                "news_count": 2,
                "bullish_score": 0.7,
                "bearish_score": 0.2,
                "neutral_score": 0.1,
                "net_score": 0.5,
                "importance": 0.7,
                "confidence": 0.7,
                "risk_level": "MEDIUM",
                "summary": "btc etf",
                "narrative": "ETF",
                "macro_score": 0.2,
                "whale_score": 0.1,
                "polymarket_score": 0.1,
                "market_score": 0.5,
            },
            {
                "symbol": "ETH",
                "news_count": 1,
                "bullish_score": 0.2,
                "bearish_score": 0.6,
                "neutral_score": 0.2,
                "net_score": -0.4,
                "importance": 0.5,
                "confidence": 0.5,
                "risk_level": "MEDIUM",
                "summary": "eth soft",
                "narrative": "DeFi",
                "macro_score": 0.2,
                "whale_score": 0.1,
                "polymarket_score": 0.1,
                "market_score": 0.3,
            },
        ]
        macro_rows = [
            {"name": "Fed", "title": "Fed watch", "summary": "hawkish", "created_at": now},
            {"name": "DXY", "title": "DXY watch", "summary": "firm", "created_at": now},
        ]
        poly_rows = [
            {
                "name": "fed_cut",
                "question": "Fed cut?",
                "probability": 0.55,
                "created_at": now,
            },
        ]
        md = render_claude_market_context(
            assets=assets,
            briefs=[],
            events=events,
            now=now,
            macro_rows=macro_rows,
            poly_rows=poly_rows,
        )
        for section in (
            "## Executive Summary",
            "## Top Events",
            "## Macro Outlook",
            "## Prediction Markets",
            "## Top Bullish Assets",
            "## Top Bearish Assets",
            "## Source Confidence",
            "## AI Conclusion",
        ):
            self.assertIn(section, md)
        self.assertIn("Why it matters", md)
        self.assertIn("Impact:", md)
        # Macro / poly not empty placeholders only
        self.assertIn("Fed", md)
        self.assertIn("Fed cut", md)
        # No dual listing of same asset
        bull_idx = md.index("## Top Bullish Assets")
        bear_idx = md.index("## Top Bearish Assets")
        src_idx = md.index("## Source Confidence")
        bull_block = md[bull_idx:bear_idx]
        bear_block = md[bear_idx:src_idx]
        self.assertIn("**BTC**", bull_block)
        self.assertNotIn("**BTC**", bear_block)
        self.assertIn("**ETH**", bear_block)


class TestNarrativesS45(unittest.TestCase):
    def test_multi_categories_no_general_default(self) -> None:
        labels = detect_narratives(
            "BlackRock Bitcoin ETF inflows while Fed rate cut bets grow"
        )
        self.assertIn("ETF", labels)
        self.assertIn("Fed", labels)
        self.assertIn("Institutional", labels)
        self.assertNotIn("General", labels)


class TestSchemaS45(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s45.db"
        configure_unit_test_db_isolation(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_adds_net_score(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 61)
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v61", applied)
            cols = {
                str(r[1])
                for r in conn.execute(
                    "PRAGMA table_info(market_asset_intelligence)"
                ).fetchall()
            }
            self.assertIn("net_score", cols)
            ev_cols = {
                str(r[1])
                for r in conn.execute(
                    "PRAGMA table_info(market_intel_events)"
                ).fetchall()
            }
            self.assertIn("market_impact", ev_cols)
            self.assertIn("why_it_matters", ev_cols)


if __name__ == "__main__":
    unittest.main()
