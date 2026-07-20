"""S46 — AI Research Analyst tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.ai_analyst.config import load_llm_settings, load_agent_profiles
from bot.research.ai_analyst.context_builder import build_market_context
from bot.research.ai_analyst.llm_client import get_llm_client
from bot.research.ai_analyst.prompt_builder import build_messages, load_prompt
from bot.research.ai_analyst.report_generator import run_ai_analyst
from bot.research.ai_analyst.cli import main as cli_main


class TestPromptsS46(unittest.TestCase):
    def test_prompts_exist(self) -> None:
        for name in (
            "system_base", "full_report", "morning_brief", "evening_brief",
            "btc_brief", "macro_brief", "sp500_brief", "x_post",
            "telegram_post", "json_summary",
        ):
            text = load_prompt(name)
            self.assertGreater(len(text), 20)

    def test_build_messages_embeds_context(self) -> None:
        system, user = build_messages(
            prompt_name="x_post",
            context={"btc": {"price": 100}, "intelligence": {"top_events": []}},
        )
        self.assertIn("only", system.lower())
        self.assertIn('"price": 100', user)


class TestLLMClientS46(unittest.TestCase):
    def test_template_provider(self) -> None:
        settings = load_llm_settings()
        # Force template via get with mutated settings
        from bot.research.ai_analyst.config import LLMSettings
        client = get_llm_client(LLMSettings(
            provider="template", model="template",
            timeout_sec=5, max_retries=0, max_tokens=1000,
            base_url=None, api_key=None,
        ))
        system, user = build_messages(
            prompt_name="json_summary",
            context={
                "btc": {"price": 1},
                "sp500": {},
                "macro": {},
                "intelligence": {"top_events": [{"title": "ETF inflows", "sentiment": 0.4}]},
                "fear_greed": {"current": 30},
                "funding": {},
                "open_interest": {},
            },
        )
        resp = client.complete(system=system, user=user)
        data = json.loads(resp.text)
        self.assertIn("market_bias", data)
        self.assertIn("top_events", data)


class TestContextAndRunS46(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s46.db"
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
                ("u1", now - 3600, 64000, 0.0001, 1e9, 40, 52, 100, 5200, 15, 104, 2300, 80, now),
            )
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, btc_price, funding, open_interest,
                  fear_greed, btc_dominance, volume, spx, vix, dxy, gold, oil,
                  recorder_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                """,
                ("u2", now, 65000, 0.0002, 1.1e9, 42, 53, 120, 5250, 14, 103.5, 2310, 81, now),
            )
            conn.execute(
                """
                INSERT INTO market_macro_events (
                  created_at, name, event_type, title, summary, value, unit, tags_json, raw_json
                ) VALUES (?, 'Fed', 'rss', 'Fed hawkish', 'rates', NULL, NULL, '[]', '{}')
                """,
                (now,),
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
                    3, now, now, '["CoinDesk","Reuters","tg:news"]', 0.9,
                    "[]", "HIGH", "ETF flows matter for BTC.", "Bullish",
                ),
            )
            conn.commit()
        self.reports = Path(self.tmp.name) / "reports"
        self.now = 1_700_000_000

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_context_builder_fields(self) -> None:
        live = {
            "quotes": {
                "spx": {"value": 5250.0, "change_24h": 50.0, "trend": "Bullish", "source": "test"},
                "nasdaq": {"value": 18000.0, "change_24h": 100.0, "trend": "Bullish", "source": "test"},
                "vix": {"value": 14.0, "change_24h": -1.0, "trend": "Bearish", "source": "test"},
                "dxy": {"value": 103.5, "change_24h": -0.5, "trend": "Bearish", "source": "test"},
                "us10y": {"value": 4.25, "change_24h": 0.02, "trend": "Bullish", "unit": "%", "source": "test"},
                "us02y": {"value": 4.10, "change_24h": 0.01, "trend": "Bullish", "unit": "%", "source": "test"},
            },
            "etf": {
                "unit": "USD_millions",
                "source": "test",
                "btc_etf": {
                    "netflow_1d": 120.5,
                    "netflow_5d": 500.0,
                    "netflow_30d": 1200.0,
                    "trend": "Bullish",
                },
                "eth_etf": {
                    "netflow_1d": -10.0,
                    "netflow_5d": 40.0,
                    "netflow_30d": 200.0,
                    "trend": "Bullish",
                },
            },
            "elapsed_ms": 1,
        }
        ctx = build_market_context(now=self.now, live_payload=live)
        self.assertEqual(ctx["btc"]["price"], 65000)
        self.assertIsNotNone(ctx["btc"]["change_1h_pct"])
        self.assertEqual(ctx["sp500"]["value"], 5250.0)
        self.assertEqual(ctx["sp500"]["trend"], "Bullish")
        self.assertEqual(ctx["nasdaq"]["value"], 18000.0)
        self.assertEqual(ctx["vix"]["value"], 14.0)
        self.assertEqual(ctx["macro"]["dxy"]["value"], 103.5)
        self.assertEqual(ctx["macro"]["us10y"]["value"], 4.25)
        self.assertEqual(ctx["macro"]["us02y"]["value"], 4.10)
        self.assertNotIn("title", ctx["macro"]["dxy"])
        self.assertEqual(ctx["etf"]["btc_etf"]["netflow_5d"], 500.0)
        self.assertIn("context_completeness", ctx)
        self.assertGreaterEqual(ctx["context_completeness"], 70)
        # No raw None leaves in serialized JSON for key blocks
        blob = json.dumps(ctx)
        self.assertNotIn(": null", blob)
        self.assertIn("top_events", ctx["intelligence"])
        self.assertIn("fed", ctx["macro"])

    def test_run_writes_artifacts(self) -> None:
        live = {
            "quotes": {
                "spx": {"value": 5250.0, "change_24h": 10.0, "trend": "Bullish", "source": "test"},
                "nasdaq": {"value": 18000.0, "change_24h": 20.0, "trend": "Bullish", "source": "test"},
                "vix": {"value": 14.0, "change_24h": -0.5, "trend": "Bearish", "source": "test"},
                "dxy": {"value": 103.5, "change_24h": -0.2, "trend": "Bearish", "source": "test"},
                "us10y": {"value": 4.2, "change_24h": 0.01, "trend": "Bullish", "source": "test"},
                "us02y": {"value": 4.0, "change_24h": 0.0, "trend": "Neutral", "source": "test"},
            },
            "etf": {
                "btc_etf": {"netflow_5d": 100.0, "netflow_1d": 20.0, "trend": "Bullish"},
                "eth_etf": {"netflow_5d": 10.0, "netflow_1d": 1.0, "trend": "Bullish"},
            },
        }
        result = run_ai_analyst(
            flags=None,
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_payload=live,
        )
        self.assertTrue(result["ok"])
        expected = {
            "market_report.md",
            "morning_brief.md",
            "evening_brief.md",
            "btc_brief.md",
            "macro_brief.md",
            "sp500_brief.md",
            "x_post.md",
            "telegram_post.md",
            "market_summary.json",
            "market_context.json",
        }
        for name in expected:
            path = self.reports / name
            self.assertTrue(path.is_file(), msg=f"missing {name}")
            self.assertGreater(path.stat().st_size, 10)

        summary = json.loads((self.reports / "market_summary.json").read_text())
        for key in (
            "market_bias", "risk_level", "main_theme", "btc_summary",
            "sp500_summary", "macro_summary", "top_events", "top_risks", "next_24h",
        ):
            self.assertIn(key, summary)

        x_text = (self.reports / "x_post.md").read_text().strip()
        self.assertLessEqual(len(x_text), 280)
        tg = (self.reports / "telegram_post.md").read_text().strip()
        self.assertLessEqual(len(tg), 800)

        report = (self.reports / "market_report.md").read_text()
        for section in (
            "## Executive Summary", "## Bitcoin", "## Macro",
            "## Prediction Markets", "## Key Events", "## Conclusion",
        ):
            self.assertIn(section, report)

    def test_flag_subset(self) -> None:
        result = run_ai_analyst(
            flags=["btc", "x"],
            reports_dir=self.reports,
            force_template=True,
            now=self.now,
            live_enrich=False,
        )
        ids = {a["agent_id"] for a in result["artifacts"]}
        self.assertEqual(ids, {"s46_btc", "s46_x"})

    def test_agent_profiles_ready_for_future(self) -> None:
        profiles = load_agent_profiles()
        self.assertIn("s46_full", profiles)
        self.assertEqual(profiles["s46_full"].prompt, "full_report")


class TestCLIS46(unittest.TestCase):
    def test_help(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cli_main(["--help"])
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
