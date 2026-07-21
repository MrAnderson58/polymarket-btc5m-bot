"""S46.2 — Analytical Reasoning Engine tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.ai_analyst.context_builder import build_market_context
from bot.research.ai_analyst.prompt_builder import build_messages, load_prompt
from bot.research.ai_analyst.reasoning_engine import (
    detect_contradictions,
    enrich_context_for_analysis,
    format_analysis_quality_block,
)
from bot.research.ai_analyst.report_generator import run_ai_analyst


def _sample_live() -> dict:
    return {
        "quotes": {
            "spx": {"value": 5250.0, "change_24h": 10.0, "trend": "Bullish", "source": "test"},
            "nasdaq": {"value": 18000.0, "change_24h": 20.0, "trend": "Bullish", "source": "test"},
            "vix": {"value": 14.0, "change_24h": -0.5, "trend": "Bearish", "source": "test"},
            "dxy": {"value": 103.5, "change_24h": 0.3, "trend": "Bullish", "source": "test"},
            "us10y": {"value": 4.2, "change_24h": 0.02, "trend": "Bullish", "source": "test"},
            "us02y": {"value": 4.0, "change_24h": 0.0, "trend": "Neutral", "source": "test"},
        },
        "etf": {
            "btc_etf": {"netflow_5d": 500.0, "netflow_1d": 120.0, "trend": "Bullish"},
            "eth_etf": {"netflow_5d": 10.0, "netflow_1d": 1.0, "trend": "Bullish"},
        },
    }


class TestReasoningEngineS462(unittest.TestCase):
    def test_enrich_adds_quality_and_hints(self) -> None:
        ctx = enrich_context_for_analysis({
            "btc": {"price": 65000, "change_24h_pct": 0.1},
            "sp500": {"value": 5250, "trend": "Bullish"},
            "macro": {"dxy": {"value": 104, "trend": "Bullish"}},
            "etf": {"btc_etf": {"netflow_5d": 100}},
            "fear_greed": {"current": 30},
            "context_completeness": 70,
            "data_gaps": ["polymarket"],
        })
        self.assertIn("analysis_quality", ctx)
        self.assertIn("reasoning_hints", ctx)
        q = ctx["analysis_quality"]
        for key in ("context_completeness", "reasoning_depth", "data_gaps", "confidence"):
            self.assertIn(key, q)
        self.assertIn("cross_asset_pairs_available", ctx["reasoning_hints"])

    def test_contradiction_etf_flat_btc(self) -> None:
        ctx = {
            "btc": {"price": 65000, "change_24h_pct": 0.2},
            "etf": {"btc_etf": {"netflow_5d": 200}},
        }
        out = detect_contradictions(ctx)
        self.assertTrue(any("ETF" in c and "flat" in c.lower() for c in out))

    def test_format_quality_block(self) -> None:
        text = format_analysis_quality_block({
            "context_completeness": 67,
            "reasoning_depth": 55,
            "confidence": 61,
            "data_gaps": ["funding"],
        })
        self.assertIn("## Analysis Quality", text)
        self.assertIn("67%", text)


class TestPromptsS462(unittest.TestCase):
    def test_reasoning_rules_loaded(self) -> None:
        rules = load_prompt("reasoning_rules")
        self.assertIn("EXPLAIN", rules)
        self.assertIn("Key Takeaways", rules)

    def test_build_messages_includes_rules(self) -> None:
        system, _ = build_messages(prompt_name="full_report", context={"btc": {}})
        self.assertIn("EXPLAIN", system)
        self.assertIn("Bloomberg", system)


class TestReportStructureS462(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s462.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = 1_700_000_000
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, btc_price, funding, open_interest,
                  fear_greed, btc_dominance, volume, spx, vix, dxy, gold, oil,
                  recorder_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                """,
                ("u1", now, 65000, 0.0001, 1e9, 30, 53, 120, 5250, 14, 103.5, 2310, 81, now),
            )
            conn.execute(
                """
                INSERT INTO market_intel_events (
                  event_uid, created_at, updated_at, title, summary, narrative,
                  symbols_json, sentiment, importance, confidence, source_count,
                  headline_count, first_seen, last_seen, sources_json, freshness,
                  article_ids_json, market_impact, why_it_matters, polarity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "e1", now, now, "Bitcoin ETF inflows smash records",
                    "BlackRock IBIT", "ETF", '["BTC"]', 0.5, 0.8, 0.7, 3,
                    3, now, now, '["CoinDesk"]', 0.9,
                    "[]", "HIGH", "ETF flows matter for BTC.", "Bullish",
                ),
            )
            conn.commit()
        self.reports = Path(self.tmp.name) / "reports"
        self.now = 1_700_000_000

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_report_s462_sections(self) -> None:
        run_ai_analyst(
            flags=["morning"],
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=_sample_live(),
        )
        # run only full via default - use explicit full
        run_ai_analyst(
            flags=None,
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=_sample_live(),
        )
        report = (self.reports / "market_report.md").read_text()
        for section in (
            "## Data Timestamp",
            "## Executive Summary",
            "Today's Theme:",
            "Market Bias:",
            "Confidence:",
            "Key Drivers:",
            "Main Risks:",
            "## Cross-Asset Relationships",
            "## Market Contradictions",
            "## Current Narrative",
            "## Analysis Quality",
            "### Key Takeaways",
        ):
            self.assertIn(section, report, msg=f"missing {section}")

        ctx = json.loads((self.reports / "market_context.json").read_text())
        self.assertIn("analysis_quality", ctx)
        self.assertIn("reasoning_hints", ctx)

    def test_brief_structures(self) -> None:
        run_ai_analyst(
            flags=["btc", "macro", "sp500"],
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=_sample_live(),
        )
        btc = (self.reports / "btc_brief.md").read_text()
        for h in ("### Market State", "### Bullish Factors", "### Bearish Factors",
                  "### What to Watch Next", "### Key Takeaways"):
            self.assertIn(h, btc)

        macro = (self.reports / "macro_brief.md").read_text()
        for h in ("### Macro Regime", "### Dollar", "### Rates", "### Bottom Line"):
            self.assertIn(h, macro)

        sp = (self.reports / "sp500_brief.md").read_text()
        for h in ("### Risk Appetite", "### Volatility", "### BTC Correlation", "### Bottom Line"):
            self.assertIn(h, sp)

    def test_telegram_publication_format(self) -> None:
        run_ai_analyst(
            flags=["telegram"],
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=_sample_live(),
        )
        tg = (self.reports / "telegram_post.md").read_text()
        self.assertIn("📰 Market Snapshot", tg)
        self.assertIn("📈 Main Driver", tg)
        self.assertIn("(No trading advice)", tg)
        self.assertLessEqual(len(tg.strip()), 1200)

    def test_x_post_limit(self) -> None:
        run_ai_analyst(
            flags=["x"],
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=_sample_live(),
        )
        x = (self.reports / "x_post.md").read_text().strip()
        self.assertLessEqual(len(x), 280)
        self.assertNotIn("#", x)


if __name__ == "__main__":
    unittest.main()
